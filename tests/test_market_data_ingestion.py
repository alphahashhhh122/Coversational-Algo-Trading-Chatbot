from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.backtest_service import BacktestService
from iimc_trading_platform.services.capability_coverage_service import (
    CapabilityCoverageService,
)
from iimc_trading_platform.services.custom_strategy_service import (
    CustomStrategyService,
)
from iimc_trading_platform.services.market_data_ingestion_service import (
    MarketDataIngestionService,
)


class MarketDataIngestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "platform.duckdb"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_equity_ohlcv_import_is_cataloged_and_backtestable(self) -> None:
        imported = MarketDataIngestionService(self.db_path).import_ohlcv(
            dataset_id="reliance_5m_local",
            asset_class="equity",
            symbol="RELIANCE",
            exchange="NSE",
            interval="5m",
            candles=_candles(),
            source_name="reliance_5m.json",
        )
        created = CustomStrategyService(self.db_path).create_spec(
            name="equity_macd",
            description="MACD crossover over supplied equity candles.",
            symbol="RELIANCE",
            timeframe="5m",
            indicators=[
                {
                    "name": "MACD_LINE",
                    "type": "MACD",
                    "source": "close",
                    "fast_period": 3,
                    "slow_period": 6,
                    "signal_period": 3,
                },
                {
                    "name": "MACD_SIGNAL",
                    "type": "MACD_SIGNAL",
                    "source": "close",
                    "fast_period": 3,
                    "slow_period": 6,
                    "signal_period": 3,
                },
            ],
            entry_rules=[
                {
                    "left": "MACD_LINE",
                    "operator": "crosses_above",
                    "right": "MACD_SIGNAL",
                }
            ],
            exit_rules=[
                {
                    "left": "MACD_LINE",
                    "operator": "crosses_below",
                    "right": "MACD_SIGNAL",
                }
            ],
        )
        result = CustomStrategyService(self.db_path).run_backtest(
            spec_id=created["spec_id"],
            dataset_id=imported["dataset_id"],
        )

        self.assertEqual(imported["storage_table"], "market_ohlcv")
        self.assertEqual(imported["quality_status"], "clean")
        self.assertEqual(created["status"], "draft_executable")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["dataset_id"], "reliance_5m_local")

    def test_invalid_candles_are_rejected_without_catalog_entry(self) -> None:
        service = MarketDataIngestionService(self.db_path)
        candles = _candles()
        candles[1]["high"] = candles[1]["close"] - 1

        with self.assertRaisesRegex(ValueError, "OHLC bounds"):
            service.import_ohlcv(
                dataset_id="invalid_equity",
                asset_class="equity",
                symbol="RELIANCE",
                exchange="NSE",
                interval="5m",
                candles=candles,
                source_name="invalid.json",
            )

        con = connect(self.db_path)
        try:
            count = con.execute("SELECT COUNT(*) FROM data_catalog").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 0)

    def test_plain_options_ohlcv_is_importable_for_rule_backtesting(self) -> None:
        imported = MarketDataIngestionService(self.db_path).import_ohlcv(
            dataset_id="nifty_call_5m_local",
            asset_class="options",
            symbol="NIFTY26JUL25000CE",
            exchange="NFO",
            interval="5m",
            candles=_candles(),
            source_name="nifty_call_5m.json",
        )
        status = CapabilityCoverageService(
            self.db_path,
            _AlwaysUnavailableReadiness(),
        ).platform_status(
            symbol="NIFTY26JUL25000CE",
            exchange="NFO",
            asset_class="options",
            interval="5m",
            start_date="2026-01-01",
            end_date="2026-01-02",
        )

        self.assertEqual(imported["asset_class"], "options")
        self.assertTrue(status["local_dataset_exists"])
        self.assertEqual(status["local_dataset"]["dataset_id"], imported["dataset_id"])

    def test_api_import_has_audit_evidence_and_readiness_finds_dataset(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.root / "artifacts",
                    openalgo_root=self.root,
                )
            )
        )
        response = client.post(
            "/datasets/ohlcv",
            json={
                "dataset_id": "btc_1h_local",
                "asset_class": "crypto",
                "symbol": "BTCUSDT",
                "exchange": "BINANCE",
                "interval": "1h",
                "candles": [
                    {
                        **candle,
                        "timestamp": candle["timestamp"].isoformat(),
                    }
                    for candle in _candles()
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["audit_id"].startswith("audit_"))
        service = CapabilityCoverageService(
            self.db_path,
            _AlwaysUnavailableReadiness(),
        )
        status = service.platform_status(
            symbol="BTCUSDT",
            exchange="BINANCE",
            asset_class="crypto",
            interval="1h",
            start_date="2026-01-01",
            end_date="2026-01-02",
        )
        self.assertTrue(status["local_dataset_exists"])
        self.assertEqual(status["local_dataset"]["dataset_id"], "btc_1h_local")

    def test_api_validates_custom_rules_without_persisting_them(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.root / "artifacts",
                    openalgo_root=self.root,
                )
            )
        )
        response = client.post(
            "/custom-strategy-specs/validate",
            json={
                "name": "unsupported_option_surface",
                "description": "Require option IV surface data.",
                "symbol": "NIFTY",
                "timeframe": "5m",
                "position_side": "short",
                "indicators": [{"type": "IV_SKEW", "period": 14, "source": "iv"}],
                "entry_rules": [{"left": "IV_SKEW_14", "operator": ">", "right": 0}],
                "exit_rules": [{"left": "IV_SKEW_14", "operator": "<", "right": 0}],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["can_execute_without_new_code"])
        self.assertTrue(payload["missing_capabilities"])
        con = connect(self.db_path)
        try:
            count = con.execute("SELECT COUNT(*) FROM custom_strategy_specs").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 0)

    def test_point_in_time_features_drive_a_custom_strategy_without_lookahead(self) -> None:
        ingestion = MarketDataIngestionService(self.db_path)
        prices = ingestion.import_ohlcv(
            dataset_id="reliance_feature_prices",
            asset_class="equity",
            symbol="RELIANCE",
            exchange="NSE",
            interval="5m",
            candles=_candles(),
            source_name="reliance_prices.json",
        )
        timestamps = [item["timestamp"] for item in _candles()]
        features = ingestion.import_features(
            dataset_id="reliance_news_features",
            symbol="RELIANCE",
            exchange="NSE",
            observations=[
                {
                    "feature_name": "news_sentiment",
                    "observed_at": timestamps[0],
                    "available_at": timestamps[0],
                    "value": -0.4,
                    "metadata": {"provider": "local_archive"},
                },
                {
                    "feature_name": "news_sentiment",
                    "observed_at": timestamps[5],
                    "available_at": timestamps[5],
                    "value": 0.7,
                    "metadata": {"provider": "local_archive"},
                },
                {
                    "feature_name": "news_sentiment",
                    "observed_at": timestamps[12],
                    "available_at": timestamps[12],
                    "value": -0.5,
                    "metadata": {"provider": "local_archive"},
                },
            ],
            source_name="reliance_news_features.json",
        )
        service = CustomStrategyService(self.db_path)
        created = service.create_spec(
            name="news_sentiment_breakout",
            description="Trade only after published news sentiment improves.",
            symbol="RELIANCE",
            timeframe="5m",
            indicators=[],
            feature_inputs=[
                {
                    "name": "news_sentiment",
                    "dataset_id": features["dataset_id"],
                    "feature_name": "news_sentiment",
                    "alignment": "asof",
                    "max_age_hours": 1,
                }
            ],
            entry_rules=[
                {"left": "news_sentiment", "operator": ">", "right": 0.2}
            ],
            exit_rules=[
                {"left": "news_sentiment", "operator": "<", "right": 0.0}
            ],
        )
        result = service.run_backtest(
            spec_id=created["spec_id"],
            dataset_id=prices["dataset_id"],
        )

        con = connect(self.db_path)
        try:
            first_signal = con.execute(
                """
                SELECT min(timestamp)
                FROM strategy_signals
                WHERE run_id = ? AND signal_type = 'entry'
                """,
                [result["run_id"]],
            ).fetchone()[0]
            parameters = con.execute(
                """
                SELECT parameters_json
                FROM strategy_runs
                WHERE run_id = ?
                """,
                [result["run_id"]],
            ).fetchone()[0]
        finally:
            con.close()

        self.assertEqual(features["storage_table"], "market_features")
        self.assertTrue(features["point_in_time_safe"])
        self.assertEqual(created["status"], "draft_executable")
        self.assertGreaterEqual(first_signal, timestamps[5])
        self.assertIn("external_feature_lineage", parameters)

    def test_feature_import_rejects_ambiguous_availability_time(self) -> None:
        timestamp = datetime(2026, 1, 2, 9, 15)
        with self.assertRaisesRegex(ValueError, "available_at"):
            MarketDataIngestionService(self.db_path).import_features(
                dataset_id="invalid_features",
                symbol="RELIANCE",
                exchange="NSE",
                observations=[
                    {
                        "feature_name": "open_interest",
                        "observed_at": timestamp,
                        "available_at": timestamp - timedelta(minutes=1),
                        "value": 100.0,
                    }
                ],
                source_name="invalid_features.json",
            )

    def test_api_imports_feature_series_and_validates_feature_rule(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.root / "artifacts",
                    openalgo_root=self.root,
                )
            )
        )
        timestamp = datetime(2026, 1, 2, 9, 15).isoformat()
        imported = client.post(
            "/datasets/features",
            json={
                "dataset_id": "api_open_interest",
                "symbol": "RELIANCE",
                "exchange": "NSE",
                "observations": [
                    {
                        "feature_name": "open_interest",
                        "observed_at": timestamp,
                        "available_at": timestamp,
                        "value": 125000,
                    }
                ],
            },
        )
        validated = client.post(
            "/custom-strategy-specs/validate",
            json={
                "name": "oi_rule",
                "description": "Use imported open-interest observations.",
                "symbol": "RELIANCE",
                "timeframe": "5m",
                "indicators": [],
                "feature_inputs": [
                    {
                        "name": "open_interest",
                        "dataset_id": "api_open_interest",
                        "feature_name": "open_interest",
                        "alignment": "asof",
                        "max_age_hours": 1,
                    }
                ],
                "entry_rules": [
                    {"left": "open_interest", "operator": ">", "right": 100000}
                ],
                "exit_rules": [
                    {"left": "open_interest", "operator": "<", "right": 100000}
                ],
            },
        )

        self.assertEqual(imported.status_code, 200)
        self.assertTrue(imported.json()["audit_id"].startswith("audit_"))
        self.assertEqual(validated.status_code, 200)
        self.assertTrue(validated.json()["can_execute_without_new_code"])

    def test_saved_feature_draft_becomes_executable_after_import(self) -> None:
        service = CustomStrategyService(self.db_path)
        created = service.create_spec(
            name="late_feature_data",
            description="Feature data can arrive after a draft is reviewed.",
            symbol="RELIANCE",
            timeframe="5m",
            indicators=[],
            feature_inputs=[
                {
                    "name": "earnings_surprise",
                    "dataset_id": "reliance_earnings",
                    "feature_name": "earnings_surprise",
                    "alignment": "asof",
                    "max_age_hours": 72,
                }
            ],
            entry_rules=[
                {"left": "earnings_surprise", "operator": ">", "right": 0}
            ],
            exit_rules=[
                {"left": "earnings_surprise", "operator": "<", "right": 0}
            ],
        )
        ingestion = MarketDataIngestionService(self.db_path)
        prices = ingestion.import_ohlcv(
            dataset_id="late_feature_prices",
            asset_class="equity",
            symbol="RELIANCE",
            exchange="NSE",
            interval="5m",
            candles=_candles(),
            source_name="late_feature_prices.json",
        )
        first_timestamp = _candles()[0]["timestamp"]
        ingestion.import_features(
            dataset_id="reliance_earnings",
            symbol="RELIANCE",
            exchange="NSE",
            observations=[
                {
                    "feature_name": "earnings_surprise",
                    "observed_at": first_timestamp,
                    "available_at": first_timestamp,
                    "value": 1.0,
                }
            ],
            source_name="reliance_earnings.json",
        )
        result = service.run_backtest(
            spec_id=created["spec_id"],
            dataset_id=prices["dataset_id"],
        )

        self.assertEqual(created["status"], "requires_review")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(service.get_spec(created["spec_id"])["status"], "draft_executable")


class _AlwaysUnavailableReadiness:
    def readiness(self, **_: str) -> dict[str, object]:
        return {
            "provider_configured": False,
            "quote_available": False,
            "historical_available": False,
            "verified_now": False,
            "unsupported_reason": "OpenAlgo credentials are not configured.",
            "analyzer_path_status": "not_configured",
            "paper_path_status": "not_configured",
            "live_path_status": "not_configured",
        }


def _candles() -> list[dict[str, object]]:
    start = datetime(2026, 1, 2, 9, 15)
    closes = [100, 99, 98, 97, 98, 100, 103, 106, 109, 110, 108, 105, 102, 99, 98, 101, 104, 107]
    return [
        {
            "timestamp": start + timedelta(minutes=5 * index),
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000 + index,
        }
        for index, close in enumerate(closes)
    ]


if __name__ == "__main__":
    unittest.main()
