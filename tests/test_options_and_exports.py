from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import OfflineOrchestrator
from iimc_trading_platform.services.options_analytics_service import (
    OptionsAnalyticsService,
)
from iimc_trading_platform.tools.registry import build_default_tool_registry


class FakeChainClient:
    def option_expiries(self, **kwargs):
        return {"data": ["24JUL25", "31JUL25"]}

    def option_chain(self, **kwargs):
        self.last_request = kwargs
        return {
            "data": {
                "underlying_ltp": 25000.0,
                "chain": [
                    {
                        "strike": 24900,
                        "ce": {"ltp": 160.0, "oi": 1000, "symbol": "N24900CE"},
                        "pe": {"ltp": 60.0, "oi": 3000, "symbol": "N24900PE"},
                    },
                    {
                        "strike": 25000,
                        "ce": {"ltp": 110.0, "oi": 5000, "symbol": "N25000CE"},
                        "pe": {"ltp": 100.0, "oi": 4000, "symbol": "N25000PE"},
                    },
                    {
                        "strike": 25100,
                        "ce": {"ltp": 70.0, "oi": 8000, "symbol": "N25100CE"},
                        "pe": {"ltp": 150.0, "oi": 2000, "symbol": "N25100PE"},
                    },
                ],
            }
        }


class OptionChainAnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "opt.duckdb"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_chain_snapshot_computes_analytics(self) -> None:
        client = FakeChainClient()
        service = OptionsAnalyticsService(self.db_path, client)

        result = service.chain_snapshot(underlying="nifty")

        self.assertEqual(result["expiry_date"], "24JUL25")
        analytics = result["analytics"]
        self.assertEqual(analytics["atm_strike"], 25000)
        self.assertEqual(analytics["atm_straddle_cost"], 210.0)
        self.assertEqual(
            analytics["put_call_oi_ratio"], round(9000 / 14000, 4),
        )
        self.assertEqual(analytics["max_call_oi_strike"], 25100)
        self.assertEqual(analytics["max_put_oi_strike"], 25000)

    def test_chain_requires_credentials(self) -> None:
        service = OptionsAnalyticsService(self.db_path, None)
        with self.assertRaises(ValueError):
            service.chain_snapshot(underlying="NIFTY")

    def test_router_routes_option_chain_questions(self) -> None:
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="test",
        )
        cases = {
            "Show the Bank Nifty option chain": "BANKNIFTY",
            "What is the put-call ratio for Nifty?": "NIFTY",
            "Which Nifty strike has the highest open interest?": "NIFTY",
            "What is the ATM straddle cost for banknifty": "BANKNIFTY",
        }
        for message, expected in cases.items():
            decision = OfflineOrchestrator().select_tool(
                message, [], registry,
            )
            self.assertEqual(
                decision.tool_name, "get_option_chain", message,
            )
            self.assertEqual(
                decision.arguments["underlying"], expected, message,
            )


class FakeQuoteClient:
    def __init__(self, prices: dict[str, float]) -> None:
        self.prices = prices

    def quote(self, *, symbol: str, exchange: str) -> dict:
        return {"data": {"ltp": self.prices[symbol]}}


class PriceAlertTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "alerts.duckdb"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_alert_triggers_only_when_crossed(self) -> None:
        from iimc_trading_platform.services.price_alert_service import (
            PriceAlertService,
        )

        service = PriceAlertService(
            self.db_path, FakeQuoteClient({"RELIANCE": 1490.0}),
        )
        created = service.create(
            symbol="RELIANCE", direction="above", threshold=1500.0,
        )
        self.assertEqual(created["status"], "active")

        first = service.evaluate()
        self.assertEqual(first["checked"], 1)
        self.assertEqual(first["triggered"], [])

        service.client = FakeQuoteClient({"RELIANCE": 1505.0})
        second = service.evaluate()
        self.assertEqual(len(second["triggered"]), 1)

        alerts = service.list()["alerts"]
        self.assertEqual(alerts[0]["status"], "triggered")
        self.assertEqual(alerts[0]["last_price"], 1505.0)

    def test_invalid_direction_rejected(self) -> None:
        from iimc_trading_platform.services.price_alert_service import (
            PriceAlertService,
        )

        with self.assertRaises(ValueError):
            PriceAlertService(self.db_path).create(
                symbol="X", direction="sideways", threshold=1,
            )

    def test_router_creates_and_lists_alerts(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        decision = OfflineOrchestrator().select_tool(
            "Alert me when RELIANCE goes above 1500", [], registry,
        )
        self.assertEqual(decision.tool_name, "create_price_alert")
        self.assertEqual(decision.arguments["symbol"], "RELIANCE")
        self.assertEqual(decision.arguments["direction"], "above")
        self.assertEqual(decision.arguments["threshold"], 1500.0)

        below = OfflineOrchestrator().select_tool(
            "Notify me if INFY falls below 1400.5", [], registry,
        )
        self.assertEqual(below.arguments["direction"], "below")
        self.assertEqual(below.arguments["threshold"], 1400.5)

        listing = OfflineOrchestrator().select_tool(
            "show my alerts", [], registry,
        )
        self.assertEqual(listing.tool_name, "list_price_alerts")


if __name__ == "__main__":
    unittest.main()
