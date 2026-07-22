"""A plan-and-execute agent for comparing investments (LangGraph).

Given two or more symbols, it plans one research step per symbol, executes those
read-only research sub-agents in parallel, then synthesises a **factual**
side-by-side comparison — who leads on each available fundamental/technical
metric — and a cautious overall read.

Deliberately bounded and safe: it is read-only, prepares no orders, and gives no
buy/sell recommendation. It compares real data and says which name is stronger on
*these metrics*, with the standing reminder that this is not investment advice.
Missing data is reported, never invented.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .research_agent_service import ResearchAgentService

# Ratios where a HIGHER value is better, and where LOWER is better.
_HIGHER_BETTER = ("roe", "roa", "net_margin", "operating_margin",
                  "revenue_growth", "earnings_growth", "current_ratio")
_LOWER_BETTER = ("debt_to_equity",)


class _State(TypedDict, total=False):
    symbols: list[str]
    exchange: str
    plan: list[str]
    findings: dict[str, Any]
    comparison: list[dict[str, Any]]
    fundamental_leader: str | None
    notes: list[str]


class PlanExecuteService:
    def __init__(self, research_agent: ResearchAgentService) -> None:
        self.research_agent = research_agent
        self._graph = self._build_graph()

    # -- public ---------------------------------------------------------------

    def run(self, symbols: list[str], exchange: str = "NSE") -> dict[str, Any]:
        clean = []
        for symbol in symbols:
            up = (symbol or "").upper().strip()
            if up and up not in clean:
                clean.append(up)
        if len(clean) < 2:
            raise ValueError(
                "Give me at least two symbols to compare, e.g. "
                "'compare RELIANCE and TCS'."
            )
        clean = clean[:3]  # keep it bounded
        final: _State = self._graph.invoke({"symbols": clean, "exchange": exchange})
        return {
            "symbols": final["symbols"],
            "exchange": exchange,
            "findings": final.get("findings", {}),
            "comparison": final.get("comparison", []),
            "fundamental_leader": final.get("fundamental_leader"),
            "notes": final.get("notes", []),
            "no_synthetic_fallback": True,
        }

    # -- graph ----------------------------------------------------------------

    def _build_graph(self) -> Any:
        graph = StateGraph(_State)
        graph.add_node("plan", self._plan)
        graph.add_node("execute", self._execute)
        graph.add_node("synthesize", self._synthesize)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", "synthesize")
        graph.add_edge("synthesize", END)
        return graph.compile()

    def _plan(self, state: _State) -> _State:
        return {"plan": [f"research {sym}" for sym in state["symbols"]]}

    def _execute(self, state: _State) -> _State:
        exchange = state["exchange"]

        async def _gather() -> dict[str, Any]:
            results = await asyncio.gather(
                *(
                    asyncio.to_thread(self.research_agent.run, sym, exchange)
                    for sym in state["symbols"]
                )
            )
            return {sym: res for sym, res in zip(state["symbols"], results)}

        return {"findings": asyncio.run(_gather())}

    def _synthesize(self, state: _State) -> _State:
        symbols = state["symbols"]
        findings = state.get("findings", {})
        notes: list[str] = []
        comparison: list[dict[str, Any]] = []
        lead_count = {sym: 0 for sym in symbols}

        # Compare the fundamental ratios available for every symbol.
        ratios_by_symbol = {
            sym: findings.get(sym, {}).get("fundamentals", {}).get("ratios", {})
            for sym in symbols
        }
        all_ratios = _HIGHER_BETTER + _LOWER_BETTER
        for ratio in all_ratios:
            values = {sym: ratios_by_symbol[sym].get(ratio) for sym in symbols}
            if any(v is None for v in values.values()):
                continue  # only compare where every symbol has the number
            higher_is_better = ratio in _HIGHER_BETTER
            winner = (
                max(values, key=lambda s: values[s])
                if higher_is_better
                else min(values, key=lambda s: values[s])
            )
            lead_count[winner] += 1
            comparison.append(
                {
                    "metric": ratio,
                    "values": values,
                    "better": winner,
                    "direction": "higher" if higher_is_better else "lower",
                }
            )

        if not comparison:
            notes.append(
                "No fundamentals are stored for every symbol, so I can only "
                "compare what's available — add statements in the Data tab for a "
                "fuller picture."
            )

        # Technical trend, reported but not scored (it's a short-term read).
        for sym in symbols:
            tech = findings.get(sym, {}).get("technicals", {})
            if tech.get("available"):
                notes.append(
                    f"{sym}: {tech.get('trend')} trend, RSI {tech.get('rsi')}."
                )

        leader = None
        if comparison:
            top = max(lead_count, key=lambda s: lead_count[s])
            # Only call a leader when it's a clear win, not a tie.
            if list(lead_count.values()).count(lead_count[top]) == 1:
                leader = top
                notes.append(
                    f"{top} leads on {lead_count[top]} of {len(comparison)} "
                    "compared fundamentals."
                )
            else:
                notes.append("The fundamentals are mixed — no clear leader.")

        return {
            "comparison": comparison,
            "fundamental_leader": leader,
            "notes": notes,
        }
