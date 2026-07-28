"""Committee mode: several agents answer, disagreements are preserved.

A LangGraph ``plan -> convene -> synthesize`` graph that puts a question to
several registered agents in parallel and produces one brief with **per-agent
attribution**.

The design choice that matters: when two agents disagree the committee
**reports the conflict**; it never averages it away. An averaged number hides
the single most useful signal a multi-agent system can give you — that the
evidence is mixed. A confident-looking blended answer would be less honest than
"the fundamentals and the tape point different ways, here's each".

Read-only, like every research agent: no orders, no broker path.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from ..progress import report


class _State(TypedDict, total=False):
    symbol: str
    exchange: str
    members: list[str]
    opinions: dict[str, Any]
    agreements: list[str]
    disagreements: list[dict[str, Any]]
    gaps: list[str]


class CommitteeService:
    def __init__(self, runner: Callable[[str, str, str], dict[str, Any]]) -> None:
        """``runner(agent_name, symbol, exchange) -> findings dict``."""
        self.runner = runner
        self._graph = self._build_graph()

    def run(
        self,
        symbol: str,
        exchange: str = "NSE",
        members: tuple[str, ...] = ("market_researcher", "strategy_validator"),
    ) -> dict[str, Any]:
        symbol = (symbol or "").upper().strip()
        if not symbol:
            raise ValueError("Give me a symbol for the committee to review.")
        final: _State = self._graph.invoke(
            {"symbol": symbol, "exchange": exchange, "members": list(members)}
        )
        return {
            "symbol": symbol,
            "exchange": exchange,
            "members": final.get("members", []),
            "opinions": final.get("opinions", {}),
            "agreements": final.get("agreements", []),
            "disagreements": final.get("disagreements", []),
            "gaps": final.get("gaps", []),
            "no_synthetic_fallback": True,
        }

    def _build_graph(self) -> Any:
        graph = StateGraph(_State)
        graph.add_node("convene", self._convene)
        graph.add_node("synthesize", self._synthesize)
        graph.add_edge(START, "convene")
        graph.add_edge("convene", "synthesize")
        graph.add_edge("synthesize", END)
        return graph.compile()

    def _convene(self, state: _State) -> _State:
        symbol, exchange = state["symbol"], state["exchange"]

        report(
            "convene",
            f"Asking {len(state['members'])} agents about {symbol}: "
            + ", ".join(state["members"]),
        )

        async def _gather() -> dict[str, Any]:
            results = await asyncio.gather(
                *(
                    asyncio.to_thread(self.runner, member, symbol, exchange)
                    for member in state["members"]
                ),
                return_exceptions=True,
            )
            opinions: dict[str, Any] = {}
            for member, result in zip(state["members"], results):
                opinions[member] = (
                    {"error": str(result)[:200]}
                    if isinstance(result, Exception)
                    else result
                )
            return opinions

        return {"opinions": asyncio.run(_gather())}

    def _synthesize(self, state: _State) -> _State:
        """Extract each member's directional read and compare them."""

        report("synthesize", "Comparing what each member concluded")
        opinions = state.get("opinions", {})
        gaps: list[str] = []
        stances: dict[str, str] = {}
        for member, payload in opinions.items():
            if not isinstance(payload, dict) or payload.get("error"):
                gaps.append(
                    f"{member}: {payload.get('error', 'no result') if isinstance(payload, dict) else 'no result'}"
                )
                continue
            stance = _stance_of(payload)
            if stance is None:
                gaps.append(f"{member}: no clear read from the data available")
            else:
                stances[member] = stance

        agreements: list[str] = []
        disagreements: list[dict[str, Any]] = []
        distinct = set(stances.values())
        if len(distinct) == 1 and stances:
            stance = distinct.pop()
            # One member agreeing with itself is not a consensus, and saying so
            # matters: it tells you how much weight the read actually carries.
            agreements.append(
                f"Only one member ({next(iter(stances))}) had a read: {stance}."
                if len(stances) == 1
                else f"All {len(stances)} members read this as {stance}."
            )
        elif len(distinct) > 1:
            # Preserved, not averaged.
            disagreements.append(
                {
                    "topic": "direction",
                    "positions": [
                        {"member": member, "stance": stance}
                        for member, stance in stances.items()
                    ],
                    "note": (
                        "The members disagree. This is reported rather than "
                        "averaged — mixed evidence is itself the finding."
                    ),
                }
            )
        return {
            "agreements": agreements,
            "disagreements": disagreements,
            "gaps": gaps,
        }


def _stance_of(payload: dict[str, Any]) -> str | None:
    """Directional read from an agent's findings, or None when it has none."""

    # Strategy agents: judged on the out-of-sample verdict.
    verdict = payload.get("verdict")
    if verdict in {"holds_up", "weaker_but_positive"}:
        return "constructive"
    if verdict in {"overfit", "poor"}:
        return "cautious"

    # Research agents: trend is the clearest directional signal available.
    technicals = payload.get("technicals") or {}
    if technicals.get("available"):
        trend = technicals.get("trend")
        if trend == "uptrend":
            return "constructive"
        if trend == "downtrend":
            return "cautious"
        if trend == "sideways":
            return "neutral"
    return None
