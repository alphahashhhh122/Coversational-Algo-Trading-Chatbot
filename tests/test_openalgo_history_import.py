from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.openalgo_history_import_service import (
    OpenAlgoHistoryImportService,
)


class _HistoryBroker:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None

    def historical(self, **kwargs):
        self.request = kwargs
        start = datetime(2026, 6, 1, 3, 45)
        return {
            "data": [
                {
                    "timestamp": int((start + timedelta(minutes=5 * index)).timestamp()),
                    "open": 1400 + math.sin(index / 4) * 10,
                    "high": 1406 + math.sin(index / 4) * 10,
                    "low": 1394 + math.sin(index / 4) * 10,
                    "close": 1400 + math.sin(index / 4) * 10,
                    "volume": 1000 + index,
                }
                for index in range(80)
            ]
        }


class OpenAlgoHistoryImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.database_path = root / "platform.duckdb"
        initialize_database(self.database_path)
        self.config = AppConfig(
            database_path=self.database_path,
            artifacts_dir=root / "artifacts",
            openalgo_root=root,
            openalgo_api_key="configured",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch(
        "iimc_trading_platform.services.openalgo_history_import_service.InstrumentDiscoveryService"
    )
    def test_imports_resolved_history_with_openalgo_provenance(self, discovery) -> None:
        discovery.return_value.validate_symbol.return_value = {
            "ok": True,
            "resolved_symbol": "RELIANCE",
            "resolved_exchange": "NSE",
            "instrument": {"symbol": "RELIANCE", "exchange": "NSE"},
        }
        service = OpenAlgoHistoryImportService(self.config)
        broker = _HistoryBroker()
        with patch.object(service, "_client", return_value=broker):
            result = service.import_history(
                symbol="reliance",
                exchange="nse",
                asset_class="equity",
                interval="5m",
                start_date="2026-06-01",
                end_date="2026-06-02",
            )

        self.assertEqual(
            result["dataset_id"],
            "openalgo_reliance_nse_5m_20260601_20260602",
        )
        self.assertEqual(result["data_source"], "openalgo_history")
        self.assertEqual(result["row_count"], 80)
        self.assertEqual(broker.request["end_date"], "2026-06-03")
        self.assertEqual(result["provider_end_date_exclusive"], "2026-06-03")
        con = connect(self.database_path)
        try:
            catalog = con.execute(
                "SELECT symbol, exchange, data_type, row_count FROM data_catalog"
            ).fetchone()
            source = con.execute(
                "SELECT source_name FROM raw_file_registry"
            ).fetchone()
        finally:
            con.close()
        self.assertEqual(catalog, ("RELIANCE", "NSE", "equity_ohlcv", 80))
        self.assertTrue(source[0].startswith("openalgo_history:RELIANCE:NSE:5m"))

    @patch(
        "iimc_trading_platform.services.openalgo_history_import_service.InstrumentDiscoveryService"
    )
    def test_rejects_invalid_provider_candle_without_catalog_entry(self, discovery) -> None:
        discovery.return_value.validate_symbol.return_value = {
            "ok": True,
            "resolved_symbol": "RELIANCE",
            "resolved_exchange": "NSE",
        }
        service = OpenAlgoHistoryImportService(self.config)
        broken_broker = _HistoryBroker()
        broken_broker.historical = lambda **kwargs: {
            "data": [{"timestamp": 1780285500, "open": 0}]
        }
        with patch.object(service, "_client", return_value=broken_broker):
            with self.assertRaisesRegex(ValueError, "Invalid OpenAlgo history candle"):
                service.import_history(
                    symbol="RELIANCE",
                    exchange="NSE",
                    asset_class="equity",
                    interval="5m",
                    start_date="2026-06-01",
                    end_date="2026-06-02",
                )
        con = connect(self.database_path)
        try:
            count = con.execute("SELECT COUNT(*) FROM data_catalog").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 0)
