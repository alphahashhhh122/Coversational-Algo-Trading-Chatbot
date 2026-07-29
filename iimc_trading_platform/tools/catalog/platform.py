"""Platform status, readiness, personas, portfolios, and watches.

One slice of the tool catalogue. ``build`` takes only the services its
own tools use, so each group's dependencies are visible instead of being
shared implicitly through one factory's scope.
"""

from __future__ import annotations

from typing import Any

from ..contracts import ToolCapabilityMetadata, ToolDefinition
from ..inputs import (
    CreateWatchInput,
    EmptyInput,
    PersonaIdInput,
    PlatformReadinessInput,
    PortfolioIdInput,
    WatchSymbolInput,
)


def build(
    *,
    capabilities: Any,
    execution_readiness: Any,
    openalgo_readiness: Any,
    personas: Any,
    platform_dashboard: Any,
    portfolios: Any,
    sandbox_read: Any,
    watches: Any,
) -> list[ToolDefinition]:
    return [
                ToolDefinition(
                    name="get_platform_summary",
                    description=(
                        "Return the current platform capability summary: data, "
                        "asset coverage, execution paths, safety gates, RAG, "
                        "market news, and OpenAlgo readiness."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: platform_dashboard.summary(),
                    side_effects="read-only platform summary checks",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("monitor",),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="create_watch",
                    description=(
                        "Start watching a stock for a technical condition — RSI "
                        "below/above a level, or price above/below its EMA20 — "
                        "evaluated against real broker candles. It only ever "
                        "notifies; it never trades or prepares an order. Use for "
                        "'watch RELIANCE for RSI below 30'."
                    ),
                    input_model=CreateWatchInput,
                    handler=lambda value: watches.create(
                        symbol=CreateWatchInput.model_validate(value.model_dump()).symbol,
                        condition=CreateWatchInput.model_validate(
                            value.model_dump()
                        ).condition,
                        threshold=CreateWatchInput.model_validate(
                            value.model_dump()
                        ).threshold,
                        exchange=CreateWatchInput.model_validate(
                            value.model_dump()
                        ).exchange,
                    ),
                    side_effects="writes one row to technical_watches",
                    retry_safe=False,
                    capabilities=ToolCapabilityMetadata(
                        actions=("monitor", "store"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="list_watches",
                    description=(
                        "List the technical watches on file and their status "
                        "(active / triggered). Read-only."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: watches.list(),
                    side_effects="read-only: reads technical_watches",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("retrieve",),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="remove_watch",
                    description=(
                        "Stop watching a stock's technical condition. Use for 'stop "
                        "watching RELIANCE'."
                    ),
                    input_model=WatchSymbolInput,
                    handler=lambda value: watches.remove(
                        WatchSymbolInput.model_validate(value.model_dump()).symbol,
                        WatchSymbolInput.model_validate(value.model_dump()).exchange,
                    ),
                    side_effects="deletes matching rows from technical_watches",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("monitor",),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="check_watches",
                    description=(
                        "Evaluate all active technical watches now against fresh "
                        "broker candles and report which conditions have fired. "
                        "Read-only; only notifies, never trades. Use for 'check my "
                        "watches'."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: watches.evaluate(),
                    side_effects="reads live candles; marks fired watches triggered",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("monitor", "retrieve"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="check_platform_readiness",
                    description=(
                        "Validate symbol, exchange, asset class, local dataset, "
                        "and provider readiness without fabricating market data."
                    ),
                    input_model=PlatformReadinessInput,
                    handler=lambda value: capabilities.platform_status(
                        **PlatformReadinessInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="read-only readiness checks",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("validate", "monitor"),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research", "paper", "live"),
                        required_data=("instrument_metadata",),
                        required_providers=("openalgo",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="get_execution_readiness",
                    description=(
                        "Return stage-by-stage feasibility for research, "
                        "backtesting, paper trading, and live trading, including "
                        "blockers and required human approvals."
                    ),
                    input_model=PlatformReadinessInput,
                    handler=lambda value: execution_readiness.readiness(
                        **PlatformReadinessInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="read-only execution readiness checks",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_openalgo_monitor",
                    description=(
                        "Check OpenAlgo credentials, availability, analyzer mode, "
                        "funds, orders, trades, and positions without placing orders."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: openalgo_readiness.monitor(),
                    side_effects="read-only OpenAlgo status checks",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("monitor",),
                        asset_classes=("equity", "futures", "options", "commodity"),
                        execution_modes=("paper", "live"),
                        required_providers=("openalgo",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="list_sandbox_intents",
                    description=(
                        "List prepared OpenAlgo sandbox or paper-trading order "
                        "intents and their approval/submission status."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: sandbox_read.list_intents(),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="list_strategy_personas",
                    description=(
                        "List governed trading personas with asset-class scope, "
                        "strategy bias, risk constraints, and dashboard focus."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: personas.list(),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_strategy_persona",
                    description=(
                        "Retrieve one governed trading persona by persona_id. "
                        "Personas guide strategy selection and explanation style "
                        "but never bypass risk, approval, or data-readiness checks."
                    ),
                    input_model=PersonaIdInput,
                    handler=lambda value: personas.get(
                        PersonaIdInput.model_validate(
                            value.model_dump()
                        ).persona_id
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="list_portfolios",
                    description=(
                        "List portfolio accounts and current cash balances."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: portfolios.list(),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_portfolio_snapshot",
                    description=(
                        "Retrieve portfolio cash, positions, exposure, daily loss, "
                        "active reservations, and kill-switch state."
                    ),
                    input_model=PortfolioIdInput,
                    handler=lambda value: portfolios.get(
                        PortfolioIdInput.model_validate(
                            value.model_dump()
                        ).portfolio_id
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
    ]
