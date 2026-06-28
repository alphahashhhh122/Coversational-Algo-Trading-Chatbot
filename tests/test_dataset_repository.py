from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.domain import DataDomain, DataQualityStatus
from iimc_trading_platform.infrastructure import (
    DuckDBDatasetRepository,
    initialize_database,
)


class DatasetRepositoryTest(unittest.TestCase):
    def test_repository_returns_latest_real_quality_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.duckdb"
            initialize_database(db_path)
            con = connect(db_path)
            try:
                con.execute(
                    """
                    INSERT INTO data_catalog VALUES (
                        'nifty_options', 'market_data', 'options_ohlcv',
                        'NIFTY', 'NFO', '5m', ?, ?, 66080,
                        'options_ohlcv', 'source_1',
                        'clean_with_warnings', 'quality.json', CURRENT_TIMESTAMP
                    )
                    """,
                    [
                        datetime(2026, 4, 23, 9, 15),
                        datetime(2026, 5, 22, 15, 25),
                    ],
                )
                con.execute(
                    """
                    INSERT INTO data_quality_reports VALUES (
                        'run_1', 'source_1', 'nifty_options', 'quality.json',
                        69262, 66080, 66080, 3182, 0, 90,
                        'clean_with_warnings', CURRENT_TIMESTAMP
                    )
                    """
                )
            finally:
                con.close()

            dataset = DuckDBDatasetRepository(db_path).get("nifty_options")

            self.assertIsNotNone(dataset)
            self.assertEqual(dataset.data_domain, DataDomain.MARKET_DATA)
            self.assertEqual(
                dataset.quality.status,
                DataQualityStatus.CLEAN_WITH_WARNINGS,
            )
            self.assertEqual(dataset.quality.total_rows, 69262)
            self.assertEqual(dataset.quality.valid_rows, 66080)
            self.assertEqual(dataset.quality.duplicate_rows, 3182)
            self.assertEqual(dataset.quality.invalid_rows, 0)


if __name__ == "__main__":
    unittest.main()
