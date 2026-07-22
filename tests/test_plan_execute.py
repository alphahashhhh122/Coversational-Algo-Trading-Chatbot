from __future__ import annotations

import unittest

from iimc_trading_platform.orchestration import grounded_tool_response
from iimc_trading_platform.services.plan_execute_service import PlanExecuteService


def _findings(symbol: str, ratios: dict, trend: str = "up") -> dict:
    return {
        "symbol": symbol,
        "company_name": symbol.title(),
        "exchange": "NSE",
        "fundamentals": {"available": bool(ratios), "ratios": ratios},
        "technicals": {"available": True, "trend": trend, "rsi": 55},
        "sections_available": ["fundamentals", "technicals"],
    }


class _Agent:
    def __init__(self, by_symbol: dict) -> None:
        self.by_symbol = by_symbol
        self.calls: list[str] = []

    def run(self, symbol: str, exchange: str = "NSE") -> dict:
        self.calls.append(symbol)
        return self.by_symbol[symbol]


class PlanExecuteTest(unittest.TestCase):
    def test_compares_and_picks_leader_on_fundamentals(self) -> None:
        agent = _Agent(
            {
                "RELIANCE": _findings("RELIANCE", {"roe": 0.12, "net_margin": 0.10, "debt_to_equity": 0.6}),
                "TCS": _findings("TCS", {"roe": 0.40, "net_margin": 0.20, "debt_to_equity": 0.1}),
            }
        )
        result = PlanExecuteService(agent).run(["RELIANCE", "TCS"])
        self.assertEqual(sorted(agent.calls), ["RELIANCE", "TCS"])
        # TCS wins higher roe, higher net_margin, lower debt_to_equity.
        self.assertEqual(result["fundamental_leader"], "TCS")
        answer = grounded_tool_response("compare_investments", result)
        self.assertIn("Comparing RELIANCE vs TCS", answer)
        self.assertIn("TCS leads", answer)
        self.assertIn("not a buy/sell recommendation", answer.lower())

    def test_missing_fundamentals_reported_not_invented(self) -> None:
        agent = _Agent(
            {
                "RELIANCE": _findings("RELIANCE", {"roe": 0.12}),
                "TCS": _findings("TCS", {}),  # no ratios stored
            }
        )
        result = PlanExecuteService(agent).run(["RELIANCE", "TCS"])
        self.assertIsNone(result["fundamental_leader"])
        self.assertEqual(result["comparison"], [])
        answer = grounded_tool_response("compare_investments", result)
        self.assertIn("only compare what's available", answer)

    def test_tie_reports_no_clear_leader(self) -> None:
        agent = _Agent(
            {
                "A": _findings("A", {"roe": 0.20, "debt_to_equity": 0.1}),
                "B": _findings("B", {"roe": 0.10, "debt_to_equity": 0.05}),
            }
        )
        # A wins roe, B wins debt_to_equity -> 1-1 tie.
        result = PlanExecuteService(agent).run(["A", "B"])
        self.assertIsNone(result["fundamental_leader"])
        self.assertTrue(any("mixed" in n for n in result["notes"]))

    def test_needs_two_symbols(self) -> None:
        with self.assertRaises(ValueError):
            PlanExecuteService(_Agent({})).run(["RELIANCE"])


if __name__ == "__main__":
    unittest.main()
