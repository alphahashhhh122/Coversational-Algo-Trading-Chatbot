"""The founding agent roster.

Each agent adapts a *tool* from the tool registry — not a raw service — so an
agent run goes through exactly the same validation, dataset resolution, and
auto-fetch as a chat request. The adapter's job is only to translate an
``AgentTask`` into the tool payload and the tool's payload into structured
findings / evidence / gaps.
"""

from __future__ import annotations

from typing import Any, Callable

from ..tools.registry import ToolRegistry
from .base import AgentResult, AgentTask, ServiceAgent

_Interp = tuple[dict[str, Any], list[dict[str, Any]], list[str]]


def _tool_runner(
    registry: ToolRegistry, tool_name: str,
    payload: Callable[[AgentTask], dict[str, Any]],
) -> Callable[[AgentTask], dict[str, Any]]:
    def run(task: AgentTask) -> dict[str, Any]:
        tool = registry.get(tool_name)
        value = tool.input_model.model_validate(payload(task))
        return tool.handler(value)

    return run


def _require_symbol(task: AgentTask) -> str:
    if not task.symbol:
        raise ValueError("this agent needs a symbol, e.g. symbol='RELIANCE'")
    return task.symbol


def _interpret_research(payload: dict[str, Any]) -> _Interp:
    evidence = [
        {"kind": "section", "name": name}
        for name in payload.get("sections_available", [])
    ]
    return payload, evidence, list(payload.get("gaps", []))


def _interpret_report(payload: dict[str, Any]) -> _Interp:
    evidence = list(payload.get("citations", []))
    return payload, evidence, list(payload.get("gaps", []))


def _interpret_optimization(payload: dict[str, Any]) -> _Interp:
    gaps = [
        row["error"] for row in payload.get("results", []) if row.get("error")
    ]
    if payload.get("best") is None:
        gaps.append("no usable configuration found")
    evidence = [{"kind": "dataset", "dataset_id": payload.get("dataset_id")}]
    return payload, evidence, gaps


def _interpret_walk_forward(payload: dict[str, Any]) -> _Interp:
    gaps = [] if payload.get("status") == "ok" else ["no usable configuration"]
    evidence = [
        {
            "kind": "walk_forward_split",
            "dataset_id": payload.get("dataset_id"),
            "train_bars": payload.get("train_bars"),
            "test_bars": payload.get("test_bars"),
        }
    ]
    return payload, evidence, gaps


def _interpret_comparison(payload: dict[str, Any]) -> _Interp:
    gaps = [
        note for note in payload.get("notes", [])
        if "only compare what's available" in note
    ]
    evidence = [
        {"kind": "metric", **row} for row in payload.get("comparison", [])
    ]
    return payload, evidence, gaps


def _interpret_watch_check(payload: dict[str, Any]) -> _Interp:
    return payload, [
        {"kind": "watch_fired", **row} for row in payload.get("fired", [])
    ], list(payload.get("errors", []))


def build_founding_roster(
    registry: ToolRegistry,
    *,
    chat_runner: Callable[[str], dict[str, Any]] | None = None,
) -> list[ServiceAgent]:
    """The agents shipped with the platform, adapting existing tools."""

    roster = [
        ServiceAgent(
            agent_id="market_researcher@1.0",
            name="market_researcher",
            version="1.0",
            category="research",
            description=(
                "Parallel multi-analyst briefing: valuation, fundamentals, "
                "technicals, and news for one symbol."
            ),
            capabilities=("research", "analyze"),
            runner=_tool_runner(
                registry, "deep_research",
                lambda t: {"symbol": _require_symbol(t), "exchange": t.exchange},
            ),
            interpret=_interpret_research,
        ),
        ServiceAgent(
            agent_id="deep_researcher@1.0",
            name="deep_researcher",
            version="1.0",
            category="research",
            description=(
                "Iterative self-critiquing research loop that deepens thin "
                "coverage with cited public documents."
            ),
            capabilities=("research", "analyze", "cite"),
            runner=_tool_runner(
                registry, "deep_research_report",
                lambda t: {"symbol": _require_symbol(t), "exchange": t.exchange},
            ),
            interpret=_interpret_report,
        ),
        ServiceAgent(
            agent_id="strategy_discoverer@1.0",
            name="strategy_discoverer",
            version="1.0",
            category="strategy",
            description=(
                "Backtests a parameter grid over stored history and reports the "
                "honest leaderboard, flagging thin results."
            ),
            capabilities=("backtest", "optimize"),
            runner=_tool_runner(
                registry, "run_strategy_optimization",
                lambda t: {
                    "symbol": _require_symbol(t),
                    "exchange": t.exchange,
                    "strategy_name": t.params.get("strategy_name", "ema_crossover"),
                },
            ),
            interpret=_interpret_optimization,
        ),
        ServiceAgent(
            agent_id="strategy_validator@1.0",
            name="strategy_validator",
            version="1.0",
            category="strategy",
            description=(
                "Walk-forward check: optimises on older data, tests the winner "
                "on unseen data, and calls out overfitting."
            ),
            capabilities=("backtest", "validate"),
            runner=_tool_runner(
                registry, "validate_strategy_walk_forward",
                lambda t: {
                    "symbol": _require_symbol(t),
                    "exchange": t.exchange,
                    "strategy_name": t.params.get("strategy_name", "ema_crossover"),
                },
            ),
            interpret=_interpret_walk_forward,
        ),
        ServiceAgent(
            agent_id="comparator@1.0",
            name="comparator",
            version="1.0",
            category="research",
            description=(
                "Plan-and-execute comparison: researches each symbol in "
                "parallel and reports a factual side-by-side."
            ),
            capabilities=("research", "compare"),
            runner=_tool_runner(
                registry, "compare_investments",
                lambda t: {
                    "symbols": list(t.symbols) or ([t.symbol] if t.symbol else []),
                    "exchange": t.exchange,
                },
            ),
            interpret=_interpret_comparison,
        ),
        ServiceAgent(
            agent_id="sentinel@1.0",
            name="sentinel",
            version="1.0",
            category="monitor",
            description=(
                "Evaluates the technical watches against fresh candles and "
                "reports which conditions fired. Notifies only; never trades."
            ),
            capabilities=("monitor",),
            runner=_tool_runner(registry, "check_watches", lambda t: {}),
            interpret=_interpret_watch_check,
        ),
    ]
    if chat_runner is not None:
        roster.append(
            ServiceAgent(
                agent_id="conversational_assistant@1.0",
                name="conversational_assistant",
                version="1.0",
                category="assistant",
                description=(
                    "The chat assistant itself: routes plain-language requests "
                    "to the right tool and answers from real data."
                ),
                capabilities=("chat", "route"),
                runner=lambda t: chat_runner(
                    t.params.get("message") or _require_symbol(t)
                ),
                interpret=lambda payload: (payload, [], []),
            )
        )
    return roster
