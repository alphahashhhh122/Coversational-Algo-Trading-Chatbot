from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import OfflineOrchestrator
from iimc_trading_platform.services.screener_service import ScreenerService
from iimc_trading_platform.tools.registry import build_default_tool_registry


class FakeHistoryClient:
    """Falling closes for WEAKSTK (low RSI), rising for STRONGSTK."""

    def historical(self, *, symbol, exchange, interval, start_date, end_date):
        if symbol == "WEAKSTK":
            closes = [200 - i * 2 for i in range(40)]
        else:
            closes = [100 + i * 2 for i in range(40)]
        return {
            "data": [
                {"close": close, "volume": 1000 + i}
                for i, close in enumerate(closes)
            ]
        }


class ScreenerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scr.duckdb"
        initialize_database(self.db_path)
        self.service = ScreenerService(self.db_path, FakeHistoryClient())
        self.service.add_symbol("WEAKSTK")
        self.service.add_symbol("STRONGSTK")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_watchlist_roundtrip(self) -> None:
        listed = self.service.list_symbols()["symbols"]
        self.assertEqual(
            {item["symbol"] for item in listed},
            {"WEAKSTK", "STRONGSTK"},
        )
        self.service.remove_symbol("WEAKSTK")
        self.assertEqual(
            len(self.service.list_symbols()["symbols"]), 1,
        )

    def test_rsi_below_screen_finds_falling_symbol(self) -> None:
        result = self.service.scan(condition="rsi_below", threshold=30.0)

        matched = {item["symbol"] for item in result["matches"]}
        self.assertIn("WEAKSTK", matched)
        self.assertNotIn("STRONGSTK", matched)
        self.assertEqual(result["skipped"], [])

    def test_price_above_ema_screen(self) -> None:
        result = self.service.scan(
            condition="price_above_ema", threshold=1.0, period=20,
        )
        matched = {item["symbol"] for item in result["matches"]}
        self.assertIn("STRONGSTK", matched)

    def test_empty_watchlist_raises(self) -> None:
        service = ScreenerService(self.db_path, FakeHistoryClient())
        service.remove_symbol("WEAKSTK")
        service.remove_symbol("STRONGSTK")
        with self.assertRaisesRegex(ValueError, "watchlist is empty"):
            service.scan(condition="rsi_below")

    def test_router_watchlist_and_screen(self) -> None:
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="test",
        )
        add = OfflineOrchestrator().select_tool(
            "Add RELIANCE to my watchlist", [], registry,
        )
        self.assertEqual(add.tool_name, "add_watchlist_symbol")
        self.assertEqual(add.arguments["symbol"], "RELIANCE")

        screen = OfflineOrchestrator().select_tool(
            "Find stocks where RSI is below 30", [], registry,
        )
        self.assertEqual(screen.tool_name, "run_technical_screen")
        self.assertEqual(screen.arguments["condition"], "rsi_below")
        self.assertEqual(screen.arguments["threshold"], 30.0)

        listing = OfflineOrchestrator().select_tool(
            "show my watchlist", [], registry,
        )
        self.assertEqual(listing.tool_name, "list_watchlist")


class PortfolioMarkToMarketTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "pf.duckdb"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_mark_to_market_computes_live_unrealized(self) -> None:
        from iimc_trading_platform.services.portfolio_service import (
            PortfolioService,
        )

        service = PortfolioService(self.db_path)
        created = service.create(
            name="paper_sim", starting_cash=100000.0, created_by="tester",
        )
        portfolio_id = created["portfolio_id"]
        from datetime import datetime

        from iimc_trading_platform.db import connect

        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO portfolio_positions VALUES "
                "(?, 'RELIANCE', 10, 1400.0, 1400.0, 0.0, ?)",
                [portfolio_id, datetime(2026, 7, 18, 10, 0)],
            )
        finally:
            con.close()

        marked = service.mark_to_market(
            portfolio_id, lambda symbol: 1450.0,
        )

        self.assertEqual(marked["positions_marked"][0]["live_price"], 1450.0)
        self.assertEqual(
            marked["positions_marked"][0]["unrealized_pnl"], 500.0,
        )
        self.assertEqual(marked["total_unrealized_pnl"], 500.0)
        self.assertEqual(marked["market_value"], 14500.0)
        self.assertEqual(marked["quote_errors"], [])

        failing = service.mark_to_market(
            portfolio_id,
            lambda symbol: (_ for _ in ()).throw(RuntimeError("no quote")),
        )
        self.assertEqual(failing["positions_marked"], [])
        self.assertEqual(len(failing["quote_errors"]), 1)

    def test_router_routes_mark_to_market(self) -> None:
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="test",
        )
        decision = OfflineOrchestrator().select_tool(
            "Mark portfolio_abc123 to market", [], registry,
        )
        self.assertEqual(decision.tool_name, "mark_portfolio_to_market")
        self.assertEqual(
            decision.arguments["portfolio_id"], "portfolio_abc123",
        )


if __name__ == "__main__":
    unittest.main()
