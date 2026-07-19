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


if __name__ == "__main__":
    unittest.main()
