from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services import PortfolioService


class PortfolioServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        initialize_database(self.db_path)
        self.service = PortfolioService(self.db_path)
        self.portfolio = self.service.create(
            name="Research Portfolio",
            starting_cash=10_000.0,
            created_by="test",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reservations_prevent_double_spending_and_fill_is_idempotent(self) -> None:
        first = self.service.evaluate_and_reserve(
            portfolio_id=self.portfolio["portfolio_id"],
            symbol="NIFTY",
            side="BUY",
            quantity=8,
            price=1_000.0,
        )
        second = self.service.evaluate_and_reserve(
            portfolio_id=self.portfolio["portfolio_id"],
            symbol="NIFTY",
            side="BUY",
            quantity=8,
            price=1_000.0,
        )

        self.assertTrue(first["approved"])
        self.assertEqual(first["approved_quantity"], 8)
        self.assertTrue(second["approved"])
        self.assertEqual(second["approved_quantity"], 2)

        snapshot = self.service.apply_fill(
            portfolio_id=self.portfolio["portfolio_id"],
            reservation_id=first["reservation_id"],
            reference_id="fill-1",
            price=1_000.0,
            fees=10.0,
        )
        repeated = self.service.apply_fill(
            portfolio_id=self.portfolio["portfolio_id"],
            reservation_id=first["reservation_id"],
            reference_id="fill-1",
            price=1_000.0,
            fees=10.0,
        )

        self.assertEqual(snapshot["cash_balance"], 1_990.0)
        self.assertEqual(snapshot["positions"][0]["quantity"], 8)
        self.assertEqual(repeated["cash_balance"], snapshot["cash_balance"])

    def test_kill_switch_releases_reservations_and_blocks_new_risk(self) -> None:
        reservation = self.service.evaluate_and_reserve(
            portfolio_id=self.portfolio["portfolio_id"],
            symbol="NIFTY",
            side="BUY",
            quantity=1,
            price=1_000.0,
        )
        self.assertTrue(reservation["approved"])

        snapshot = self.service.set_trading_enabled(
            portfolio_id=self.portfolio["portfolio_id"],
            enabled=False,
            reason="operator emergency stop",
            changed_by="approver",
        )
        rejected = self.service.evaluate_and_reserve(
            portfolio_id=self.portfolio["portfolio_id"],
            symbol="NIFTY",
            side="BUY",
            quantity=1,
            price=1_000.0,
        )

        self.assertFalse(snapshot["risk_control"]["trading_enabled"])
        self.assertEqual(snapshot["active_reservations"], 0)
        self.assertFalse(rejected["approved"])
        self.assertIn("kill switch", rejected["reason"].lower())
        self.assertEqual(rejected["checks"]["approved_notional"], 0.0)

    def test_sell_fill_realizes_pnl_and_reduces_position(self) -> None:
        buy = self.service.evaluate_and_reserve(
            portfolio_id=self.portfolio["portfolio_id"],
            symbol="NIFTY",
            side="BUY",
            quantity=2,
            price=1_000.0,
        )
        self.service.apply_fill(
            portfolio_id=self.portfolio["portfolio_id"],
            reservation_id=buy["reservation_id"],
            reference_id="buy-fill",
            price=1_000.0,
        )
        sell = self.service.evaluate_and_reserve(
            portfolio_id=self.portfolio["portfolio_id"],
            symbol="NIFTY",
            side="SELL",
            quantity=1,
            price=1_100.0,
        )
        snapshot = self.service.apply_fill(
            portfolio_id=self.portfolio["portfolio_id"],
            reservation_id=sell["reservation_id"],
            reference_id="sell-fill",
            price=1_100.0,
            fees=5.0,
        )

        self.assertEqual(snapshot["positions"][0]["quantity"], 1)
        self.assertEqual(snapshot["positions"][0]["realized_pnl"], 95.0)
        self.assertEqual(snapshot["cash_balance"], 9_095.0)


if __name__ == "__main__":
    unittest.main()
