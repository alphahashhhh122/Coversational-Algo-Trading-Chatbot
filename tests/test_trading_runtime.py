from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.domain import (
    ExecutionMode,
    OrderStatus,
    RiskOutcome,
)
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services import (
    BacktestService,
    EvidenceService,
    OrderService,
    RiskService,
)
from iimc_trading_platform.strategies import build_strategy_registry


class TradingRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runtime.duckdb"
        initialize_database(self.db_path)
        self.dataset_id = self._seed_dataset()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_four_strategies_are_explicitly_registered(self) -> None:
        names = {
            item["name"]
            for item in build_strategy_registry().list()
        }
        self.assertEqual(
            names,
            {
                "ema_crossover",
                "rule_spec",
                "sma_crossover",
                "rsi_mean_reversion",
                "momentum_roc",
            },
        )

    def test_ema_backtest_is_deterministic_and_persisted(self) -> None:
        service = BacktestService(self.db_path)
        first = service.run(
            strategy_name="ema_crossover",
            dataset_id=self.dataset_id,
            parameters={
                "fast_period": 5,
                "slow_period": 18,
                "stop_loss_pct": 0.03,
            },
            requested_quantity=2,
        )
        second = service.run(
            strategy_name="ema_crossover",
            dataset_id=self.dataset_id,
            parameters={
                "fast_period": 5,
                "slow_period": 18,
                "stop_loss_pct": 0.03,
            },
            requested_quantity=2,
        )

        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["total_trades"], second["total_trades"])
        self.assertEqual(first["net_pnl"], second["net_pnl"])
        self.assertEqual(first["max_drawdown"], second["max_drawdown"])
        self.assertEqual(
            first["manifest_sha256"],
            second["manifest_sha256"],
        )
        self.assertIn("sharpe_ratio", first["metrics"])
        self.assertIn("profit_factor", first["metrics"])

        con = connect(self.db_path)
        try:
            counts = con.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM strategy_signals WHERE run_id = ?),
                    (SELECT COUNT(*) FROM risk_decisions WHERE run_id = ?),
                    (SELECT COUNT(*) FROM order_events WHERE run_id = ?),
                    (SELECT COUNT(*) FROM order_state_events
                     WHERE order_id IN (
                         SELECT order_id FROM order_events WHERE run_id = ?
                     )),
                    (SELECT COUNT(*) FROM performance_summaries WHERE run_id = ?)
                    ,
                    (SELECT COUNT(*) FROM experiment_manifests WHERE run_id = ?)
                """,
                [first["run_id"]] * 6,
            ).fetchone()
        finally:
            con.close()

        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])
        self.assertGreater(counts[2], 0)
        self.assertGreaterEqual(counts[3], counts[2] * 3)
        self.assertEqual(counts[4], 1)
        self.assertEqual(counts[5], 1)

        evidence = EvidenceService(
            self.db_path,
            Path(self.temp_dir.name) / "artifacts",
        )
        timeline = evidence.run_timeline(first["run_id"])
        self.assertEqual(timeline["counts"]["signals"], counts[0])
        self.assertEqual(
            [event["event_type"] for event in timeline["events"][:4]],
            ["signal", "risk_decision", "order", "fill"],
        )
        comparison = evidence.compare_runs(
            [first["run_id"], second["run_id"]]
        )
        self.assertEqual(len(comparison["ranking"]), 2)
        report = evidence.create_run_report(
            first["run_id"],
            created_by="test",
        )
        self.assertTrue(report["artifact_available"])
        self.assertIn(first["run_id"], report["content"])

    def test_risk_service_resizes_quantity_and_stores_policy(self) -> None:
        risk = RiskService(self.db_path)
        result = risk.evaluate(
            run_id="run_risk",
            signal_id="sig_risk",
            signal_type="entry",
            symbol="NIFTY",
            price=25_000.0,
            requested_quantity=500,
            confidence=1.0,
            execution_mode=ExecutionMode.RESEARCH,
        )

        self.assertTrue(result.approved)
        self.assertEqual(result.outcome, RiskOutcome.RESIZED)
        self.assertLess(result.approved_quantity, result.requested_quantity)

        con = connect(self.db_path)
        try:
            policy_count = con.execute(
                "SELECT COUNT(*) FROM risk_limits WHERE policy_version = '1.0.0'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertGreaterEqual(policy_count, 3)

    def test_live_backtest_creates_risk_evidence_without_fills(self) -> None:
        service = BacktestService(self.db_path, allow_live_trading=True)
        result = service.run(
            strategy_name="ema_crossover",
            dataset_id=self.dataset_id,
            parameters={
                "fast_period": 5,
                "slow_period": 18,
                "stop_loss_pct": 0.03,
            },
            execution_mode=ExecutionMode.LIVE,
            requested_quantity=1,
        )

        con = connect(self.db_path)
        try:
            counts = con.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM risk_decisions WHERE run_id = ?),
                    (SELECT COUNT(*) FROM order_events WHERE run_id = ?),
                    (SELECT COUNT(*) FROM trade_fills WHERE run_id = ?),
                    (SELECT checks_json FROM risk_decisions
                     WHERE run_id = ?
                     ORDER BY created_at
                     LIMIT 1)
                """,
                [result["run_id"]] * 4,
            ).fetchone()
        finally:
            con.close()

        self.assertGreater(counts[0], 0)
        self.assertGreater(counts[1], 0)
        self.assertEqual(counts[2], 0)
        self.assertIn('"mode": "live"', counts[3])

    def test_order_idempotency_and_state_machine(self) -> None:
        service = OrderService(self.db_path)
        first = service.create_order(
            run_id="run_1",
            decision_id="risk_1",
            symbol="NIFTY",
            side="BUY",
            order_type="MARKET",
            quantity=1,
            execution_mode=ExecutionMode.RESEARCH,
            price=25_000.0,
        )
        repeated = service.create_order(
            run_id="run_1",
            decision_id="risk_1",
            symbol="NIFTY",
            side="BUY",
            order_type="MARKET",
            quantity=1,
            execution_mode=ExecutionMode.RESEARCH,
            price=25_000.0,
        )
        self.assertEqual(first.order_id, repeated.order_id)

        filled = service.record_simulated_fill(
            first,
            fill_price=25_000.0,
            fees=2.5,
            realized_pnl=-2.5,
            filled_at=datetime(2026, 1, 1, 9, 15),
        )
        self.assertEqual(filled.status, OrderStatus.FILLED)
        with self.assertRaises(ValueError):
            service.transition(filled.order_id, OrderStatus.SUBMITTED)

    def _seed_dataset(self) -> str:
        source_id = "source_runtime"
        dataset_id = "nifty_runtime_5m"
        start = datetime(2026, 1, 1, 9, 15)
        rows = []
        for index in range(360):
            timestamp = start + timedelta(minutes=5 * index)
            spot = (
                25_000
                + math.sin(index / 8.0) * 180
                + math.sin(index / 29.0) * 80
                + index * 0.2
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
                    "runtime.csv",
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
                    "runtime.csv",
                    "runtime.csv",
                    "hash",
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
