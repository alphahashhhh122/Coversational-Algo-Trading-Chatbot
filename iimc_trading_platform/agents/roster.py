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


def _interpret_fundamentals(payload: dict[str, Any]) -> _Interp:
    ratios = {
        item["name"]: item["value"]
        for item in payload.get("ratios", [])
        if isinstance(item, dict) and item.get("value") is not None
    }
    gaps = [] if ratios else ["no financial statements stored for this symbol"]
    evidence = [
        {"kind": "statement_period", "ref": payload.get("period")}
    ] if payload.get("period") else []
    # Present a research-shaped payload so the research scorer can read it.
    return (
        {**payload, "sections_available": ["fundamentals"] if ratios else []},
        evidence,
        gaps,
    )


def _interpret_news(payload: dict[str, Any]) -> _Interp:
    articles = payload.get("articles", [])
    evidence = [
        {
            "kind": "citation",
            "source": a.get("source"),
            "ref": a.get("title"),
            "url": a.get("url"),
        }
        for a in articles[:10]
    ]
    gaps = [] if articles else [payload.get("message") or "no articles returned"]
    return (
        {**payload, "sections_available": ["news"] if articles else []},
        evidence,
        gaps,
    )


def _interpret_document(payload: dict[str, Any]) -> _Interp:
    chunks = payload.get("chunks", [])
    evidence = [
        {
            "kind": "citation",
            "source": payload.get("title"),
            "url": payload.get("source_url") or payload.get("source_uri"),
        }
    ] if chunks else []
    gaps = [] if chunks else ["no readable document found for that query"]
    return (
        {**payload, "sections_available": ["document"] if chunks else []},
        evidence,
        gaps,
    )


def _interpret_screen(payload: dict[str, Any]) -> _Interp:
    matches = payload.get("matches", []) or payload.get("results", [])
    errors = payload.get("errors", []) or []
    evidence = [{"kind": "screen_match", "ref": m.get("symbol")} for m in matches
                if isinstance(m, dict)]
    return payload, evidence, list(errors)


def _interpret_portfolio(payload: dict[str, Any]) -> _Interp:
    evidence = [
        {
            "kind": "correlation",
            "ref": "/".join(row["pair"]),
            "value": row["correlation"],
            "observations": row["observations"],
        }
        for row in payload.get("correlations", [])
    ]
    return payload, evidence, list(payload.get("gaps", []))


def _interpret_committee(payload: dict[str, Any]) -> _Interp:
    """A committee's evidence is its members' attributed positions."""
    evidence = [
        {"kind": "member_opinion", "source": member}
        for member in (payload.get("opinions") or {})
    ]
    for disagreement in payload.get("disagreements", []):
        for position in disagreement.get("positions", []):
            evidence.append(
                {
                    "kind": "dissent",
                    "source": position.get("member"),
                    "ref": position.get("stance"),
                }
            )
    covered = ["committee"] if payload.get("opinions") else []
    return (
        {**payload, "sections_available": covered},
        evidence,
        list(payload.get("gaps", [])),
    )


def build_founding_roster(
    registry: ToolRegistry,
    *,
    chat_runner: Callable[[str], dict[str, Any]] | None = None,
    committee_runner: Callable[[str, str], dict[str, Any]] | None = None,
) -> list[ServiceAgent]:
    """The agents shipped with the platform, adapting existing tools.

    ``chat_runner`` and ``committee_runner`` are injected rather than imported
    so this module stays free of API-layer dependencies (and so the roster can
    be built in tests without standing up an app).
    """

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
            agent_id="fundamental_analyst@1.0",
            name="fundamental_analyst",
            version="1.0",
            category="research",
            description=(
                "Reads the imported financial statements and reports the "
                "standard ratios — profitability, leverage, liquidity, growth."
            ),
            capabilities=("research", "fundamentals"),
            runner=_tool_runner(
                registry, "analyze_fundamentals",
                lambda t: {"symbol": _require_symbol(t)},
            ),
            interpret=_interpret_fundamentals,
        ),
        ServiceAgent(
            agent_id="news_analyst@1.0",
            name="news_analyst",
            version="1.0",
            category="research",
            description=(
                "Gathers recent headlines for a symbol or the wider market and "
                "reports them with their sources. Never summarises beyond what "
                "the articles say."
            ),
            capabilities=("research", "news"),
            runner=_tool_runner(
                registry, "get_market_news",
                lambda t: {
                    "symbol": t.symbol,
                    "query": t.params.get("query") or t.symbol or "Indian stock market",
                },
            ),
            interpret=_interpret_news,
        ),
        ServiceAgent(
            agent_id="document_analyst@1.0",
            name="document_analyst",
            version="1.0",
            category="research",
            description=(
                "Finds and reads a company document — annual report, filing, "
                "transcript — using stored copies or fetching a public source, "
                "then answers from its excerpts with the source cited."
            ),
            capabilities=("research", "documents", "cite"),
            runner=_tool_runner(
                registry, "find_and_analyze_document",
                lambda t: {
                    "query": t.params.get("query")
                    or f"{_require_symbol(t)} annual report",
                },
            ),
            interpret=_interpret_document,
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
    if "analyse_portfolio" in registry._tools:
        roster.append(
            ServiceAgent(
                agent_id="portfolio_architect@1.0",
                name="portfolio_architect",
                version="1.0",
                category="research",
                description=(
                    "Looks at how a basket behaves rather than each name "
                    "alone: correlation on aligned returns, concentration, "
                    "and proposed weights. Proposes; never places."
                ),
                capabilities=("research", "portfolio", "risk"),
                runner=_tool_runner(
                    registry, "analyse_portfolio",
                    lambda t: {
                        "symbols": list(t.symbols)
                        or ([t.symbol] if t.symbol else []),
                        "exchange": t.exchange,
                    },
                ),
                interpret=_interpret_portfolio,
            )
        )
    if "get_data_health" in registry._tools:
        roster.append(
            ServiceAgent(
                agent_id="data_health@1.0",
                name="data_health",
                version="1.0",
                category="monitor",
                description=(
                    "Reports what data the platform holds per symbol, so "
                    "coverage gaps are visible up front rather than "
                    "discovered when another agent fails."
                ),
                capabilities=("monitor", "retrieve"),
                runner=_tool_runner(registry, "get_data_health", lambda t: {}),
                interpret=lambda payload: (
                    payload,
                    [{"kind": "coverage", "ref": f"{payload.get('with_price_history')}/{payload.get('symbol_count')} symbols"}],
                    list(payload.get("gaps", [])),
                ),
            )
        )
    # The technical screener only exists when a broker is configured (it needs
    # live candles), so it joins the roster conditionally rather than being
    # registered as a permanently-broken agent.
    if "run_technical_screen" in registry._tools:
        roster.append(
            ServiceAgent(
                agent_id="screener@1.0",
                name="screener",
                version="1.0",
                category="monitor",
                description=(
                    "Scans a universe (default NIFTY 50) for a technical "
                    "condition such as RSI below a level, using live candles."
                ),
                capabilities=("screen", "monitor"),
                runner=_tool_runner(
                    registry, "run_technical_screen",
                    lambda t: {
                        "condition": t.params.get("condition", "rsi_below"),
                        "threshold": t.params.get("threshold", 30),
                        "universe": t.params.get("universe", "nifty50"),
                    },
                ),
                interpret=_interpret_screen,
            )
        )
    if committee_runner is not None:
        roster.append(
            ServiceAgent(
                agent_id="research_committee@1.0",
                name="research_committee",
                version="1.0",
                category="research",
                description=(
                    "Convenes several agents on one symbol and reports their "
                    "attributed positions — preserving disagreement rather "
                    "than averaging it away."
                ),
                capabilities=("research", "synthesize", "multi_agent"),
                runner=lambda t: committee_runner(
                    _require_symbol(t), t.exchange
                ),
                interpret=_interpret_committee,
            )
        )
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
