"""Research and memory: fundamentals, deep research, portfolios, recall.

One slice of the tool catalogue. ``build`` takes only the services its
own tools use, so each group's dependencies are visible instead of being
shared implicitly through one factory's scope.
"""

from __future__ import annotations

from typing import Any

from ..contracts import ToolCapabilityMetadata, ToolDefinition
from ..inputs import (
    CompareInvestmentsInput,
    DeepResearchInput,
    DeepResearchReportInput,
    FundamentalAnalysisInput,
    PlatformReadinessInput,
    PortfolioAnalysisInput,
    RecallMemoryInput,
    RememberInput,
)


def build(
    *,
    _portfolio_agent: Any,
    deep_research_loop: Any,
    fundamentals: Any,
    memory: Any,
    plan_execute: Any,
    research: Any,
    research_agent: Any,
) -> list[ToolDefinition]:
    return [
                ToolDefinition(
                    name="analyze_fundamentals",
                    description=(
                        "Compute deterministic fundamental ratios (growth, "
                        "margins, ROE, ROA, leverage, liquidity, FCF, EPS, P/E) "
                        "from imported financial statements. Every ratio records "
                        "its formula and inputs; missing data yields warnings, "
                        "never invented values."
                    ),
                    input_model=FundamentalAnalysisInput,
                    handler=lambda value: fundamentals.analyze(
                        FundamentalAnalysisInput.model_validate(
                            value.model_dump()
                        ).symbol,
                        market_price=FundamentalAnalysisInput.model_validate(
                            value.model_dump()
                        ).market_price,
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("analyze", "explain"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="deep_research",
                    description=(
                        "Produce a multi-analyst research briefing for a stock: "
                        "fans out to valuation, fundamentals, technicals, and news "
                        "specialists in parallel and returns structured findings to "
                        "synthesize into a balanced thesis. Read-only; never places "
                        "or prepares orders; unavailable sections are reported, not "
                        "fabricated. Use for 'research/analyse/deep dive on SYMBOL'."
                    ),
                    input_model=DeepResearchInput,
                    handler=lambda value: research_agent.run(
                        DeepResearchInput.model_validate(value.model_dump()).symbol,
                        DeepResearchInput.model_validate(
                            value.model_dump()
                        ).exchange,
                    ),
                    side_effects=(
                        "read-only: parallel quote/fundamentals/technicals/news "
                        "lookups"
                    ),
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("research", "analyze", "retrieve"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="deep_research_report",
                    description=(
                        "Run an iterative, self-critiquing research loop on a stock: "
                        "plan → gather (parallel specialists) → assess its own "
                        "coverage → one targeted deepening pass that fetches and "
                        "cites a public document when data is thin → a cited report. "
                        "Read-only; bounded; never trades; every claim traces to a "
                        "real source. Use for 'deep dive / full research report / "
                        "in-depth research on SYMBOL'."
                    ),
                    input_model=DeepResearchReportInput,
                    handler=lambda value: deep_research_loop.run(
                        DeepResearchReportInput.model_validate(value.model_dump()).symbol,
                        DeepResearchReportInput.model_validate(
                            value.model_dump()
                        ).exchange,
                    ),
                    side_effects=(
                        "read-only research; may fetch and index one public web "
                        "document for citations"
                    ),
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("research", "analyze", "retrieve"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="compare_investments",
                    description=(
                        "Plan-and-execute comparison of two or three stocks: researches "
                        "each in parallel (read-only), then reports a factual "
                        "side-by-side of the fundamentals/technicals available and which "
                        "name leads on each. Not a buy/sell recommendation and no orders "
                        "are prepared; missing data is reported, not invented. Use for "
                        "'compare A and B', 'A vs B', 'which is stronger, A or B'."
                    ),
                    input_model=CompareInvestmentsInput,
                    handler=lambda value: plan_execute.run(
                        CompareInvestmentsInput.model_validate(value.model_dump()).symbols,
                        CompareInvestmentsInput.model_validate(
                            value.model_dump()
                        ).exchange,
                    ),
                    side_effects="read-only: researches each symbol in parallel",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("research", "analyze", "compare"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="analyse_portfolio",
                    description=(
                        "Portfolio-level research over two or three symbols: "
                        "correlation between them on aligned returns, "
                        "concentration, and proposed weights (equal or "
                        "inverse-volatility). Research output only - it "
                        "proposes weights and places nothing. Use for "
                        "'build a portfolio from A and B' or 'how correlated "
                        "are A and B'."
                    ),
                    input_model=PortfolioAnalysisInput,
                    handler=lambda value: _portfolio_agent().analyse(
                        PortfolioAnalysisInput.model_validate(
                            value.model_dump()
                        ).symbols,
                        exchange=PortfolioAnalysisInput.model_validate(
                            value.model_dump()
                        ).exchange,
                        scheme=PortfolioAnalysisInput.model_validate(
                            value.model_dump()
                        ).scheme,
                    ),
                    side_effects="read-only: reads stored candles",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("research", "analyze"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="remember",
                    description=(
                        "Save something the user asks you to remember across "
                        "sessions — a preference, a risk profile, a reminder. Stores "
                        "the note verbatim; never invents or infers facts. Use for "
                        "'remember that ...', 'note that I ...', 'keep in mind ...'."
                    ),
                    input_model=RememberInput,
                    handler=lambda value: memory.remember_note(
                        RememberInput.model_validate(value.model_dump()).note
                    ),
                    side_effects="writes one row to agent_memory (user note)",
                    retry_safe=False,
                    capabilities=ToolCapabilityMetadata(
                        actions=("store",),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="recall_memory",
                    description=(
                        "Recall what has been remembered: the user's saved notes and, "
                        "if a symbol is named, the last research summary on file for "
                        "it. Returns exactly what was stored, with timestamps; nothing "
                        "is fabricated. Use for 'what do you remember', 'what do you "
                        "know about me', 'what did we find on SYMBOL'."
                    ),
                    input_model=RecallMemoryInput,
                    handler=lambda value: memory.recall(
                        RecallMemoryInput.model_validate(value.model_dump()).query
                    ),
                    side_effects="read-only: reads agent_memory",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("retrieve",),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="get_research_context",
                    description=(
                        "Return combined symbol research context: architecture "
                        "readiness, local data availability, provider status, and "
                        "stored market news without fabricating missing data."
                    ),
                    input_model=PlatformReadinessInput,
                    handler=lambda value: research.research_context(
                        **PlatformReadinessInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="read-only readiness and stored-news checks",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="create_research_brief",
                    description=(
                        "Create and persist a deterministic market research brief "
                        "from governed data readiness, execution blockers, stored "
                        "news provenance, and safety guards."
                    ),
                    input_model=PlatformReadinessInput,
                    handler=lambda value: research.create_brief(
                        **PlatformReadinessInput.model_validate(
                            value.model_dump()
                        ).model_dump(),
                        created_by="chat_user",
                    ),
                    side_effects="creates a persisted research brief",
                    retry_safe=False,
                    required_role="researcher",
                ),
    ]
