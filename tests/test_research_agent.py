from __future__ import annotations

import unittest

from iimc_trading_platform.orchestration import grounded_tool_response
from iimc_trading_platform.services.research_agent_service import (
    ResearchAgentService,
)


class _Fund:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def analyze(self, symbol):
        if not self.ok:
            raise ValueError(f"No financial statements stored for {symbol}.")
        return {
            "period": "FY2026",
            "ratios": [
                {"name": "roe", "value": 0.18},
                {"name": "net_margin", "value": 0.15},
            ],
        }


class _News:
    def fetch(self, *, query, symbol):
        return {"ok": True, "articles": [{"title": "Q4 profit up", "source": "ET"}]}


class _Instr:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def quote(self, *, query, exchange):
        if not self.ok:
            return {"ok": False, "message": "broker session expired"}
        return {"ok": True, "resolved_symbol": query, "quote": {"ltp": 1450}}


class _Screen:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def technical_snapshot(self, symbol, exchange="NSE", **kwargs):
        if not self.ok:
            return {"status": "unavailable", "reason": "broker not configured"}
        return {
            "status": "ok", "symbol": symbol, "last_close": 1450, "rsi": 58.0,
            "ema20": 1442.0, "ema50": 1410.0, "trend": "uptrend",
            "momentum": "neutral", "candles_used": 200,
        }


class ResearchAgentTest(unittest.TestCase):
    def test_all_specialists_available(self) -> None:
        svc = ResearchAgentService(_Fund(), _News(), _Instr(), _Screen())
        result = svc.run("RELIANCE")
        self.assertEqual(
            set(result["sections_available"]),
            {"valuation", "fundamentals", "technicals", "news"},
        )
        self.assertEqual(result["gaps"], [])
        briefing = grounded_tool_response("deep_research", result)
        self.assertIn("RELIANCE", briefing)
        self.assertIn("Recent news", briefing)
        self.assertIn("not investment advice", briefing.lower())

    def test_degrades_without_broker_or_statements(self) -> None:
        svc = ResearchAgentService(
            _Fund(ok=False), _News(), _Instr(ok=False), _Screen(ok=False)
        )
        result = svc.run("WIPRO")
        # Only news survives; the rest are reported as gaps, never fabricated.
        self.assertEqual(result["sections_available"], ["news"])
        self.assertEqual(len(result["gaps"]), 3)
        briefing = grounded_tool_response("deep_research", result)
        self.assertIn("unavailable", briefing.lower())
        self.assertNotIn("None", briefing)

    def test_empty_symbol_is_rejected(self) -> None:
        svc = ResearchAgentService(_Fund(), _News(), _Instr(), _Screen())
        with self.assertRaises(ValueError):
            svc.run("")


if __name__ == "__main__":
    unittest.main()
