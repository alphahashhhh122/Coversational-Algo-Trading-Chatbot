"""Live market surfaces: quotes, instruments, news, watchlists, screens.

One slice of the tool catalogue. ``build`` takes only the services its
own tools use, so each group's dependencies are visible instead of being
shared implicitly through one factory's scope.
"""

from __future__ import annotations

from typing import Any

from ..contracts import ToolCapabilityMetadata, ToolDefinition
from ..inputs import (
    CreatePriceAlertInput,
    EmptyInput,
    InstrumentSearchInput,
    MarketNewsInput,
    MarketQuoteInput,
    OptionSymbolInput,
    RunScreenInput,
    SymbolValidationInput,
    WatchlistSymbolInput,
)


def build(
    *,
    _price_alerts: Any,
    _screener: Any,
    db_path: Any,
    instruments: Any,
    news: Any,
    screens: Any,
) -> list[ToolDefinition]:
    return [
                ToolDefinition(
                    name="add_watchlist_symbol",
                    description=(
                        "Add a symbol to the screening watchlist (the universe "
                        "scanned by live technical screens)."
                    ),
                    input_model=WatchlistSymbolInput,
                    handler=lambda value: _screener(db_path).add_symbol(
                        WatchlistSymbolInput.model_validate(
                            value.model_dump()
                        ).symbol,
                        WatchlistSymbolInput.model_validate(
                            value.model_dump()
                        ).exchange,
                    ),
                    side_effects="adds a persisted watchlist row",
                    required_role="researcher",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="remove_watchlist_symbol",
                    description="Remove a symbol from the screening watchlist.",
                    input_model=WatchlistSymbolInput,
                    handler=lambda value: _screener(db_path).remove_symbol(
                        WatchlistSymbolInput.model_validate(
                            value.model_dump()
                        ).symbol,
                        WatchlistSymbolInput.model_validate(
                            value.model_dump()
                        ).exchange,
                    ),
                    side_effects="removes a persisted watchlist row",
                    required_role="researcher",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="list_watchlist",
                    description="List the screening watchlist symbols.",
                    input_model=EmptyInput,
                    handler=lambda value: _screener(db_path).list_symbols(),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="create_price_alert",
                    description=(
                        "Create a price alert that triggers when the live "
                        "OpenAlgo quote crosses the threshold (checked every "
                        "minute while the broker connection is configured)."
                    ),
                    input_model=CreatePriceAlertInput,
                    handler=lambda value: _price_alerts(db_path).create(
                        symbol=CreatePriceAlertInput.model_validate(
                            value.model_dump()
                        ).symbol,
                        direction=CreatePriceAlertInput.model_validate(
                            value.model_dump()
                        ).direction,
                        threshold=CreatePriceAlertInput.model_validate(
                            value.model_dump()
                        ).threshold,
                        exchange=CreatePriceAlertInput.model_validate(
                            value.model_dump()
                        ).exchange,
                    ),
                    side_effects="creates a persisted price alert",
                    required_role="researcher",
                    retry_safe=False,
                    capabilities=ToolCapabilityMetadata(
                        actions=("monitor",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="list_price_alerts",
                    description=(
                        "List price alerts with status, last checked price, and "
                        "trigger timestamps."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: _price_alerts(db_path).list(),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="run_screen",
                    description=(
                        "Run a versioned fundamental screen (e.g. quality, "
                        "growth, low_leverage) over all symbols with imported "
                        "financial statements. Reports matches with actual "
                        "values and honest exclusions for missing metrics."
                    ),
                    input_model=RunScreenInput,
                    handler=lambda value: (
                        screens.ensure_defaults()
                        or screens.run(
                            RunScreenInput.model_validate(
                                value.model_dump()
                            ).name
                        )
                    ),
                    side_effects="read-only database query (seeds default screens once)",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("screen", "analyze"),
                        asset_classes=("equity",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="search_instruments",
                    description=(
                        "Search OpenAlgo master contracts by symbol, strike, "
                        "expiry, or option type before quote, history, or order "
                        "workflows."
                    ),
                    input_model=InstrumentSearchInput,
                    handler=lambda value: instruments.search(
                        **InstrumentSearchInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="read-only OpenAlgo symbol search",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("discover",),
                        asset_classes=("equity", "futures", "options", "commodity"),
                        execution_modes=("research", "paper", "live"),
                        required_providers=("openalgo",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="validate_instrument_symbol",
                    description=(
                        "Validate an exact OpenAlgo trading symbol and retrieve "
                        "broker mapping, lot size, tick size, expiry, strike, and "
                        "instrument type."
                    ),
                    input_model=SymbolValidationInput,
                    handler=lambda value: instruments.validate_symbol(
                        **SymbolValidationInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="read-only OpenAlgo symbol validation",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_market_quote",
                    description=(
                        "Resolve an equity, index, futures, or option query and "
                        "return its current provider-backed OpenAlgo quote."
                    ),
                    input_model=MarketQuoteInput,
                    handler=lambda value: instruments.quote(
                        **MarketQuoteInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="read-only OpenAlgo quote request",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("quote",),
                        asset_classes=("equity", "index", "futures", "options", "commodity"),
                        execution_modes=("research", "paper", "live"),
                        required_providers=("openalgo",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="resolve_option_symbol",
                    description=(
                        "Resolve an option contract from underlying, expiry, "
                        "ATM/ITM/OTM offset, and CE/PE using OpenAlgo."
                    ),
                    input_model=OptionSymbolInput,
                    handler=lambda value: instruments.resolve_option_symbol(
                        **OptionSymbolInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="read-only OpenAlgo option symbol resolution",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_market_news",
                    description=(
                        "Fetch provider-backed market news when configured. "
                        "Returns a safe unconfigured response otherwise."
                    ),
                    input_model=MarketNewsInput,
                    handler=lambda value: news.fetch(
                        **MarketNewsInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects=(
                        "stores provider raw-response artifact only when a real "
                        "news provider is configured"
                    ),
                    retry_safe=True,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("research", "fetch_news"),
                        asset_classes=("equity", "index", "commodity", "crypto"),
                        execution_modes=("research",),
                        required_providers=("market_news",),
                        risk_level="low",
                    ),
                ),
    ]
