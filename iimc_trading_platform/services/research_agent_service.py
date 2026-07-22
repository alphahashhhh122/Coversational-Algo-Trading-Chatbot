"""A parallel multi-specialist research agent.

Given a symbol, it fans out to four read-only specialists — valuation/quote,
fundamentals, technicals, and news — concurrently, then returns structured
findings. The chat layer turns those findings into an analyst thesis grounded
strictly in this real data (or a deterministic report when no LLM is set).

This is the first, read-only step of the agentic layer: it never places or
prepares orders and never fabricates values — unavailable sections are
reported as such.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .fundamentals_service import FundamentalsService
from .instrument_discovery_service import InstrumentDiscoveryService
from .instrument_names import company_name
from .market_news_service import MarketNewsService
from .memory_service import MemoryService
from .screener_service import ScreenerService

_KEY_RATIOS = (
    "roe", "roa", "net_margin", "operating_margin", "debt_to_equity",
    "current_ratio", "revenue_growth", "earnings_growth", "eps",
    "pe_ratio", "free_cash_flow",
)


class ResearchAgentService:
    def __init__(
        self,
        fundamentals: FundamentalsService,
        news: MarketNewsService,
        instruments: InstrumentDiscoveryService,
        screener: ScreenerService,
        memory: MemoryService | None = None,
    ) -> None:
        self.fundamentals = fundamentals
        self.news = news
        self.instruments = instruments
        self.screener = screener
        self.memory = memory

    def run(self, symbol: str, exchange: str = "NSE") -> dict[str, Any]:
        return asyncio.run(self._run(symbol, exchange))

    async def _run(self, symbol: str, exchange: str) -> dict[str, Any]:
        symbol = (symbol or "").upper().strip()
        if not symbol:
            raise ValueError("Give me a symbol to research, e.g. 'research RELIANCE'.")
        name = company_name(symbol, exchange) or symbol.title()
        valuation, fundamentals, technicals, news = await asyncio.gather(
            asyncio.to_thread(self._valuation, symbol, exchange, name),
            asyncio.to_thread(self._fundamentals, symbol),
            asyncio.to_thread(self._technicals, symbol, exchange),
            asyncio.to_thread(self._news, symbol, name),
        )
        sections = {
            "valuation": valuation,
            "fundamentals": fundamentals,
            "technicals": technicals,
            "news": news,
        }
        available = [key for key, sec in sections.items() if sec.get("available")]
        gaps = [
            f"{key}: {sec.get('reason', 'unavailable')}"
            for key, sec in sections.items()
            if not sec.get("available")
        ]
        prior = self._prior_research(symbol)
        result = {
            "symbol": symbol,
            "company_name": name,
            "exchange": exchange,
            **sections,
            "sections_available": available,
            "gaps": gaps,
            "previously_researched": prior,
            "no_synthetic_fallback": True,
        }
        self._remember(result)
        return result

    def _prior_research(self, symbol: str) -> dict[str, Any] | None:
        """A one-line 'last time we looked' pointer, if we have one on file."""

        if self.memory is None:
            return None
        try:
            saved = self.memory.get_research(symbol)
        except Exception:  # noqa: BLE001 - memory is a convenience, never fatal
            return None
        if not saved:
            return None
        return {"summary": saved["content"], "updated_at": saved.get("updated_at")}

    def _remember(self, result: dict[str, Any]) -> None:
        """Persist a compact, factual summary of this run for next time."""

        if self.memory is None:
            return
        available = result["sections_available"]
        summary = (
            f"{result['company_name']} ({result['symbol']}): covered "
            f"{', '.join(available) if available else 'no sections'}."
        )
        val = result.get("valuation", {})
        if val.get("available") and val.get("ltp") is not None:
            summary += f" Last price {val['ltp']}."
        tech = result.get("technicals", {})
        if tech.get("available") and tech.get("trend"):
            summary += f" Trend {tech['trend']}, RSI {tech.get('rsi')}."
        try:
            self.memory.save_research(result["symbol"], summary)
        except Exception:  # noqa: BLE001 - never let memory break a research run
            pass

    def _valuation(self, symbol: str, exchange: str, name: str) -> dict[str, Any]:
        try:
            result = self.instruments.quote(query=symbol, exchange=exchange)
        except Exception as exc:  # noqa: BLE001 - reported, not fabricated
            return {"available": False, "reason": str(exc)[:160]}
        if not result.get("ok"):
            return {"available": False, "reason": result.get("message", "no quote")}
        quote = result.get("quote", {})
        return {
            "available": True,
            "resolved_symbol": result.get("resolved_symbol", symbol),
            "ltp": quote.get("ltp") or quote.get("last_price"),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "volume": quote.get("volume"),
        }

    def _fundamentals(self, symbol: str) -> dict[str, Any]:
        try:
            analysis = self.fundamentals.analyze(symbol)
        except ValueError as exc:
            return {"available": False, "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc)[:160]}
        ratios = {
            item["name"]: item["value"]
            for item in analysis.get("ratios", [])
            if item.get("value") is not None
        }
        key = {name: ratios[name] for name in _KEY_RATIOS if name in ratios}
        return {
            "available": bool(ratios),
            "period": analysis.get("period"),
            "ratios": key or ratios,
        }

    def _technicals(self, symbol: str, exchange: str) -> dict[str, Any]:
        snapshot = self.screener.technical_snapshot(symbol, exchange)
        if snapshot.get("status") != "ok":
            return {"available": False, "reason": snapshot.get("reason", "unavailable")}
        return {"available": True, **{k: v for k, v in snapshot.items() if k != "status"}}

    def _news(self, symbol: str, name: str) -> dict[str, Any]:
        try:
            result = self.news.fetch(query=name, symbol=symbol)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc)[:160]}
        if not result.get("ok"):
            return {"available": False, "reason": result.get("message", "no news")}
        headlines = [
            {
                "title": article.get("title"),
                "source": article.get("source"),
                "published_at": article.get("published_at")
                or article.get("retrieved_at"),
            }
            for article in result.get("articles", [])[:5]
        ]
        return {"available": bool(headlines), "headlines": headlines}
