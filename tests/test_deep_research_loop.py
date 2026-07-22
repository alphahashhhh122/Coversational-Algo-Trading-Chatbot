from __future__ import annotations

import unittest

from iimc_trading_platform.orchestration import grounded_tool_response
from iimc_trading_platform.services.deep_research_loop_service import (
    DeepResearchLoopService,
)


def _findings(*, news_headlines: int, fundamentals: bool) -> dict:
    sections = ["valuation", "technicals"]
    if fundamentals:
        sections.append("fundamentals")
    if news_headlines:
        sections.append("news")
    return {
        "symbol": "RELIANCE",
        "company_name": "Reliance Industries",
        "exchange": "NSE",
        "valuation": {"available": True, "resolved_symbol": "RELIANCE", "ltp": 1400},
        "fundamentals": (
            {"available": True, "period": "FY24", "ratios": {"roe": 0.12}}
            if fundamentals
            else {"available": False, "reason": "no statements stored"}
        ),
        "technicals": {
            "available": True,
            "trend": "up",
            "rsi": 55,
            "momentum": "neutral",
            "ema20": 1390,
            "ema50": 1360,
        },
        "news": {
            "available": bool(news_headlines),
            "headlines": [
                {"title": f"Headline {i}", "source": "Wire"}
                for i in range(news_headlines)
            ],
        },
        "sections_available": sections,
        "gaps": [] if fundamentals and news_headlines >= 2 else ["fundamentals: none"],
    }


class _Agent:
    def __init__(self, findings: dict) -> None:
        self._findings = findings
        self.calls = 0

    def run(self, symbol: str, exchange: str = "NSE") -> dict:
        self.calls += 1
        return self._findings


class _Knowledge:
    def __init__(self) -> None:
        self.calls = 0

    def search_and_fetch(self, query, *, fetched_by="agent"):
        self.calls += 1
        return {
            "title": "Reliance FY24 Annual Report",
            "source_url": "https://example.com/reliance-ar",
            "chunk_count": 12,
        }


class DeepResearchLoopTest(unittest.TestCase):
    def test_thin_data_triggers_one_cited_refine_pass(self) -> None:
        agent = _Agent(_findings(news_headlines=1, fundamentals=False))
        knowledge = _Knowledge()
        svc = DeepResearchLoopService(agent, knowledge)
        result = svc.run("RELIANCE")

        self.assertEqual(result["iterations"], 2)  # gather + one refine
        self.assertEqual(knowledge.calls, 1)
        self.assertEqual(len(result["web_research"]), 1)
        # A citation for the fetched document is present.
        urls = [c.get("url") for c in result["citations"]]
        self.assertIn("https://example.com/reliance-ar", urls)
        # Self-critique honestly names the gaps it saw.
        critique = " ".join(result["self_critique"]).lower()
        self.assertIn("fundamentals are missing", critique)

    def test_complete_data_stops_after_one_pass(self) -> None:
        agent = _Agent(_findings(news_headlines=3, fundamentals=True))
        svc = DeepResearchLoopService(agent, _Knowledge())
        result = svc.run("RELIANCE")
        self.assertEqual(result["iterations"], 1)
        self.assertEqual(result["web_research"], [])

    def test_no_knowledge_service_means_no_refine(self) -> None:
        agent = _Agent(_findings(news_headlines=0, fundamentals=False))
        svc = DeepResearchLoopService(agent, knowledge=None)
        result = svc.run("RELIANCE")
        self.assertEqual(result["iterations"], 1)

    def test_refine_failure_is_reported_not_fatal(self) -> None:
        class Boom:
            def search_and_fetch(self, query, *, fetched_by="agent"):
                raise ValueError("web search unavailable")

        agent = _Agent(_findings(news_headlines=0, fundamentals=False))
        result = DeepResearchLoopService(agent, Boom()).run("RELIANCE")
        self.assertEqual(result["web_research"], [])
        self.assertTrue(
            any("nothing usable" in n for n in result["self_critique"])
        )

    def test_grounded_render_has_report_and_sources(self) -> None:
        agent = _Agent(_findings(news_headlines=1, fundamentals=False))
        result = DeepResearchLoopService(agent, _Knowledge()).run("RELIANCE")
        text = grounded_tool_response("deep_research_report", result)
        self.assertIn("research report", text.lower())
        self.assertIn("Sources:", text)
        self.assertIn("How I researched this", text)

    def test_empty_symbol_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DeepResearchLoopService(_Agent({}), None).run("  ")


if __name__ == "__main__":
    unittest.main()
