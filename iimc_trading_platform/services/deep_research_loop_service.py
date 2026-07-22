"""An iterative, self-critiquing deep-research agent (LangGraph).

Where ``deep_research`` fans out once and returns, this runs a bounded *loop*:

    plan → gather → self-critique → (refine → self-critique)* → cited report

It reuses the parallel :class:`ResearchAgentService` for the first gather, then
inspects its own coverage (a deterministic gap analysis — honest self-critique,
not a hallucinated one) and, if data is thin and a web fetcher is available,
does one targeted deepening pass that pulls and cites a public document. The
final result carries an explicit citation list.

Guardrails, unchanged from the rest of the agent layer: read-only, no order path,
bounded iterations, and nothing is fabricated — unavailable data is reported and
every claim traces back to a real source in ``citations``.

LangGraph is used here (not for the one-shot fan-out) because this is a genuine
loop with conditional continuation — its first real home in this codebase.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .research_agent_service import ResearchAgentService


class _State(TypedDict, total=False):
    symbol: str
    exchange: str
    company_name: str
    findings: dict[str, Any]
    plan: list[str]
    covered: list[str]
    gaps: list[str]
    self_critique: list[str]
    web_research: list[dict[str, Any]]
    refined: int
    max_refines: int
    done: bool


# The questions a briefing should answer. Coverage against this list is the
# agent's self-critique yardstick.
_PLAN = [
    "What is it worth now? (valuation / live quote)",
    "Is the business healthy? (fundamentals)",
    "What is the price doing? (technicals)",
    "What's the latest news and context?",
]


class DeepResearchLoopService:
    def __init__(
        self,
        research_agent: ResearchAgentService,
        knowledge: Any | None = None,
        *,
        max_refines: int = 1,
    ) -> None:
        self.research_agent = research_agent
        self.knowledge = knowledge
        self.max_refines = max_refines
        self._graph = self._build_graph()

    # -- public ---------------------------------------------------------------

    def run(self, symbol: str, exchange: str = "NSE") -> dict[str, Any]:
        symbol = (symbol or "").upper().strip()
        if not symbol:
            raise ValueError(
                "Give me a symbol to research, e.g. 'deep dive on RELIANCE'."
            )
        final: _State = self._graph.invoke(
            {
                "symbol": symbol,
                "exchange": exchange,
                "plan": list(_PLAN),
                "self_critique": [],
                "web_research": [],
                "refined": 0,
                "max_refines": self.max_refines,
                "done": False,
            }
        )
        return self._to_result(final)

    # -- graph ----------------------------------------------------------------

    def _build_graph(self) -> Any:
        graph = StateGraph(_State)
        graph.add_node("gather", self._gather)
        graph.add_node("critique", self._critique)
        graph.add_node("refine", self._refine)
        graph.add_edge(START, "gather")
        graph.add_edge("gather", "critique")
        graph.add_conditional_edges(
            "critique",
            lambda s: "refine" if not s.get("done") else END,
            {"refine": "refine", END: END},
        )
        graph.add_edge("refine", "critique")
        return graph.compile()

    def _gather(self, state: _State) -> _State:
        findings = self.research_agent.run(state["symbol"], state["exchange"])
        covered = list(findings.get("sections_available", []))
        gaps = list(findings.get("gaps", []))
        return {
            "findings": findings,
            "company_name": findings.get("company_name", state["symbol"]),
            "covered": covered,
            "gaps": gaps,
        }

    def _critique(self, state: _State) -> _State:
        findings = state.get("findings", {})
        covered = state.get("covered", [])
        news = findings.get("news", {})
        news_thin = not news.get("available") or len(news.get("headlines", [])) < 2
        fundamentals_missing = "fundamentals" not in covered

        notes = list(state.get("self_critique", []))
        answered = len(covered)
        notes.append(
            f"Coverage: {answered}/4 core questions "
            f"({', '.join(covered) if covered else 'none'})."
        )
        if fundamentals_missing:
            notes.append("Fundamentals are missing — worth a deeper look.")
        if news_thin:
            notes.append("News is thin — looking for more context.")

        # Decide whether another (bounded) deepening pass is worthwhile.
        actionable = (fundamentals_missing or news_thin) and self.knowledge is not None
        can_refine = state.get("refined", 0) < state.get("max_refines", 1)
        done = not (actionable and can_refine)
        if done and state.get("refined", 0) > 0:
            notes.append("No further sources available — finalising.")
        return {"self_critique": notes, "done": done}

    def _refine(self, state: _State) -> _State:
        """One targeted deepening pass: fetch and cite a public document."""

        web = list(state.get("web_research", []))
        query = f"{state.get('company_name', state['symbol'])} annual report results"
        try:
            fetched = self.knowledge.search_and_fetch(query, fetched_by="agent")
            web.append(
                {
                    "title": fetched.get("title"),
                    "url": fetched.get("source_url") or fetched.get("source_uri"),
                    "chunk_count": fetched.get("chunk_count"),
                    "query": query,
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported, not fabricated
            notes = list(state.get("self_critique", []))
            notes.append(f"Deepening pass found nothing usable ({str(exc)[:120]}).")
            return {"refined": state.get("refined", 0) + 1, "self_critique": notes}
        return {"refined": state.get("refined", 0) + 1, "web_research": web}

    # -- output ---------------------------------------------------------------

    def _to_result(self, state: _State) -> dict[str, Any]:
        findings = state.get("findings", {})
        result = {
            "symbol": state["symbol"],
            "company_name": state.get("company_name", state["symbol"]),
            "exchange": state.get("exchange", "NSE"),
            "findings": findings,
            "web_research": state.get("web_research", []),
            "plan": state.get("plan", []),
            "covered": state.get("covered", []),
            "gaps": state.get("gaps", []),
            "self_critique": _dedupe(state.get("self_critique", [])),
            "citations": _citations(findings, state.get("web_research", [])),
            "iterations": 1 + state.get("refined", 0),
            "no_synthetic_fallback": True,
        }
        return result


def _dedupe(notes: list[str]) -> list[str]:
    """Drop repeated self-critique lines (the same gap seen on each pass)."""

    seen: set[str] = set()
    out: list[str] = []
    for note in notes:
        if note not in seen:
            seen.add(note)
            out.append(note)
    return out


def _citations(findings: dict[str, Any], web: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every claim traces to a real source — assembled here, never invented."""

    cites: list[dict[str, Any]] = []
    val = findings.get("valuation", {})
    if val.get("available"):
        cites.append(
            {"source": "Broker live quote", "ref": val.get("resolved_symbol")}
        )
    fund = findings.get("fundamentals", {})
    if fund.get("available"):
        cites.append(
            {
                "source": "Imported financial statements",
                "ref": fund.get("period") or "latest on file",
            }
        )
    tech = findings.get("technicals", {})
    if tech.get("available"):
        cites.append(
            {"source": "Broker candles (RSI/EMA/trend)", "ref": findings.get("symbol")}
        )
    for headline in findings.get("news", {}).get("headlines", []):
        cites.append(
            {
                "source": headline.get("source") or "news",
                "ref": headline.get("title"),
            }
        )
    for doc in web:
        cites.append(
            {"source": doc.get("title") or "web document", "url": doc.get("url")}
        )
    return cites
