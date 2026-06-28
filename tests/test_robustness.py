from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services import RobustnessService
from iimc_trading_platform.services import EvidenceService


class RobustnessServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "robustness.duckdb"
        initialize_database(self.db_path)
        self.dataset_id = self._seed_dataset()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_chronological_screening_persists_trials_and_verdict(self) -> None:
        result = RobustnessService(self.db_path).run(
            strategy_name="ema_crossover",
            dataset_id=self.dataset_id,
            parameter_grid=[
                {
                    "fast_period": 5,
                    "slow_period": 18,
                    "stop_loss_pct": 0.03,
                },
                {
                    "fast_period": 8,
                    "slow_period": 24,
                    "stop_loss_pct": 0.03,
                },
            ],
            split_ratio=0.7,
            fee_bps=1.0,
            slippage_bps=0.5,
            persist_selected_runs=False,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(len(result["trials"]), 2)
        self.assertIn(
            result["verdict"],
            {"robust", "mixed", "fragile", "insufficient_sample"},
        )
        self.assertLess(
            result["windows"]["train_end"],
            result["windows"]["test_start"],
        )
        self.assertEqual(
            sum(1 for trial in result["trials"] if trial["selected"]),
            1,
        )
        self.assertEqual(result["benchmark"]["name"], "buy_and_hold")
        self.assertIsNone(result["selected_train_run_id"])
        self.assertIsNone(result["selected_test_run_id"])
        report = EvidenceService(
            self.db_path,
            Path(self.temp_dir.name) / "artifacts",
        ).create_robustness_report(
            result["experiment_id"],
            created_by="test",
        )
        self.assertTrue(report["artifact_available"])
        self.assertIn(result["experiment_id"], report["content"])

    def _seed_dataset(self) -> str:
        source_id = "source_robustness"
        dataset_id = "nifty_robustness_5m"
        start = datetime(2026, 1, 1, 9, 15)
        rows = []
        for index in range(240):
            timestamp = start + timedelta(minutes=5 * index)
            spot = (
                25_000
                + math.sin(index / 6.0) * 160
                + math.sin(index / 19.0) * 65
                + index * 0.15
            )
            rows.append(
                [
                    "NIFTY",
                    "NFO",
                    "MONTH_E1",
                    "5m",
                    timestamp,
                    "ATM",
                    25_000.0,
                    "CALL",
                    100.0,
                    105.0,
                    95.0,
                    101.0,
                    1000,
                    5000,
                    15.0,
                    spot,
                    source_id,
                    "robustness.csv",
                    "clean",
                    timestamp,
                ]
            )
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO raw_file_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    source_id,
                    "robustness.csv",
                    "robustness.csv",
                    "robustness-hash",
                    100,
                    start,
                    len(rows),
                    len(rows),
                    0,
                    0,
                ],
            )
            con.executemany(
                """
                INSERT INTO options_ohlcv VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                rows,
            )
            con.execute(
                """
                INSERT INTO data_catalog VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    dataset_id,
                    "market_data",
                    "options_ohlcv",
                    "NIFTY",
                    "NFO",
                    "5m",
                    rows[0][4],
                    rows[-1][4],
                    len(rows),
                    "options_ohlcv",
                    source_id,
                    "clean",
                    None,
                    start,
                ],
            )
        finally:
            con.close()
        return dataset_id


if __name__ == "__main__":
    unittest.main()
