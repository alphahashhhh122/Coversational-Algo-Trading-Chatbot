"""The typed input model for every tool.

One model per tool, each validating its own arguments before a handler ever
runs. Kept apart from the tool declarations because this is what changes
when an argument changes, not when a capability does.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..domain import ExecutionMode
from .contracts import ToolInput


class EmptyInput(ToolInput):
    pass

class DatasetDetailInput(ToolInput):
    dataset_id: str = Field(min_length=1)

class StrategyParameters(ToolInput):
    fast_period: int | None = Field(default=None, ge=1, le=10_000)
    slow_period: int | None = Field(default=None, ge=2, le=20_000)
    period: int | None = Field(default=None, ge=2, le=10_000)
    oversold: float | None = Field(default=None, ge=0, le=100)
    overbought: float | None = Field(default=None, ge=0, le=100)
    threshold: float | None = Field(default=None, ge=0)
    stop_loss_pct: float | None = Field(default=None, ge=0, le=1)
    entry_threshold: float | None = Field(default=None, ge=-1, le=1)
    exit_threshold: float | None = Field(default=None, ge=-1, le=1)

    def values_for_strategy(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)

class CustomIndicatorSpec(ToolInput):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=40)
    period: int | None = Field(default=None, ge=1, le=10_000)
    source: str = Field(default="close", min_length=1, max_length=80)
    fast_period: int | None = Field(default=None, ge=1, le=10_000)
    slow_period: int | None = Field(default=None, ge=1, le=10_000)
    signal_period: int | None = Field(default=None, ge=1, le=10_000)
    stddev: float | None = Field(default=None, gt=0, le=100)

class CustomRuleSpec(ToolInput):
    left: str = Field(min_length=1, max_length=120)
    operator: str = Field(min_length=1, max_length=40)
    right: str | float | int = Field()
    joiner: Literal["AND", "OR"] = "AND"

class CustomStrategyRiskSpec(ToolInput):
    max_position_size: int | None = Field(default=None, ge=1, le=100_000)
    stop_loss_pct: float | None = Field(default=None, ge=0, le=1)
    take_profit_pct: float | None = Field(default=None, ge=0, le=10)
    trailing_stop_pct: float | None = Field(default=None, ge=0, le=1)

class CustomSessionSpec(ToolInput):
    start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

class CustomFeatureInput(ToolInput):
    name: str = Field(min_length=1, max_length=80)
    dataset_id: str = Field(min_length=1, max_length=160)
    feature_name: str = Field(min_length=1, max_length=80)
    alignment: Literal["asof"] = "asof"
    max_age_hours: float = Field(gt=0, le=8_760)

class CreateCustomStrategySpecInput(ToolInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    symbol: str = Field(min_length=1, max_length=80)
    timeframe: str = Field(min_length=1, max_length=20)
    indicators: list[CustomIndicatorSpec] = Field(default_factory=list, max_length=12)
    feature_inputs: list[CustomFeatureInput] = Field(default_factory=list, max_length=24)
    entry_rules: list[CustomRuleSpec] = Field(min_length=1, max_length=12)
    exit_rules: list[CustomRuleSpec] = Field(min_length=1, max_length=12)
    risk: CustomStrategyRiskSpec = Field(default_factory=CustomStrategyRiskSpec)
    session: CustomSessionSpec | None = None
    position_side: Literal["long", "short"] = "long"
    created_by: str = Field(default="chat_user", min_length=1, max_length=200)

class CompileCustomStrategyInput(ToolInput):
    text: str = Field(min_length=5, max_length=4000)
    symbol: str | None = Field(default=None, min_length=1, max_length=80)
    timeframe: str | None = Field(default=None, min_length=1, max_length=20)

class ListCustomStrategySpecsInput(ToolInput):
    limit: int = Field(default=50, ge=1, le=200)

class OptionContractSelection(ToolInput):
    expiry: str | None = Field(default=None, min_length=1, max_length=80)
    strike: float | None = Field(default=None, gt=0)
    option_type: Literal["CALL", "PUT", "CE", "PE"] | None = None

class RunCustomStrategySpecInput(ToolInput):
    spec_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    execution_mode: ExecutionMode = ExecutionMode.RESEARCH
    requested_quantity: int = Field(default=1, ge=1, le=100_000)
    starting_equity: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=1.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1_000)
    instrument: OptionContractSelection | None = None

class RunBacktestInput(ToolInput):
    strategy_name: str = "ema_crossover"
    dataset_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution_mode: ExecutionMode = ExecutionMode.RESEARCH
    requested_quantity: int = Field(default=1, ge=1, le=100_000)
    starting_equity: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=1.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1_000)
    instrument: OptionContractSelection | None = None
    symbol: str | None = Field(default=None, min_length=1, max_length=80)
    exchange: str | None = Field(default=None, min_length=1, max_length=20)
    asset_class: Literal[
        "equity", "index", "futures", "options", "commodity", "crypto"
    ] | None = None
    interval: str | None = Field(default=None, min_length=1, max_length=20)

class RunIdInput(ToolInput):
    run_id: str = Field(min_length=1)

class RunComparisonInput(ToolInput):
    run_ids: list[str] = Field(min_length=2, max_length=10)

class RobustnessExperimentInput(ToolInput):
    strategy_name: str
    dataset_id: str
    parameter_grid: list[StrategyParameters] = Field(
        min_length=1,
        max_length=12,
    )
    split_ratio: float = Field(default=0.7, ge=0.5, le=0.85)
    requested_quantity: int = Field(default=1, ge=1, le=100_000)
    starting_equity: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=1.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1_000)
    persist_selected_runs: bool = True

class ExperimentIdInput(ToolInput):
    experiment_id: str = Field(min_length=1)

class PortfolioIdInput(ToolInput):
    portfolio_id: str = Field(min_length=1)

class PersonaIdInput(ToolInput):
    persona_id: str = Field(min_length=1)

class OpenAlgoSnapshotInput(ToolInput):
    snapshot_type: Literal[
        "analyzer",
        "funds",
        "positionbook",
        "orderbook",
        "tradebook",
        "holdings",
    ]

class InstrumentSearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200)
    exchange: Literal["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX"]

class MarketQuoteInput(ToolInput):
    query: str = Field(min_length=1, max_length=200)
    exchange: Literal["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX"] = "NSE"

class SymbolValidationInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=120)
    exchange: Literal["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX"]

class OptionSymbolInput(ToolInput):
    underlying: str = Field(min_length=1, max_length=40)
    exchange: Literal["NFO", "BFO", "NSE_INDEX", "BSE_INDEX"]
    expiry_date: str = Field(
        min_length=5,
        max_length=12,
        description="OpenAlgo expiry in DDMMMYY format, for example 30DEC25.",
    )
    offset: str = Field(
        default="ATM",
        min_length=3,
        max_length=6,
        description="ATM, ITM1-ITM50, or OTM1-OTM50.",
    )
    option_type: Literal["CE", "PE"]

class DatasetFreshnessInput(ToolInput):
    dataset_id: str = Field(min_length=1)
    purpose: Literal[
        "historical_research",
        "current_market",
        "broker_state",
        "reference",
    ] = "historical_research"

class KnowledgeSearchInput(ToolInput):
    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)

class AnalyzeKnowledgeDocumentInput(ToolInput):
    document: str = Field(min_length=1, max_length=200)
    max_chunks: int = Field(default=8, ge=1, le=50)

class FindAndAnalyzeDocumentInput(ToolInput):
    query: str = Field(min_length=1, max_length=200)
    max_chunks: int = Field(default=8, ge=1, le=50)

class DeepResearchInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(default="NSE", max_length=20)

class DeepResearchReportInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(default="NSE", max_length=20)

class StrategyOptimizationInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(default="NSE", max_length=20)
    asset_class: str = Field(default="equity", max_length=20)
    interval: str = Field(default="5m", max_length=8)
    strategy_name: Literal["ema_crossover", "sma_crossover"] = "ema_crossover"

class WalkForwardValidationInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(default="NSE", max_length=20)
    asset_class: str = Field(default="equity", max_length=20)
    interval: str = Field(default="5m", max_length=8)
    strategy_name: Literal["ema_crossover", "sma_crossover"] = "ema_crossover"

class PortfolioAnalysisInput(ToolInput):
    symbols: list[str] = Field(min_length=2, max_length=5)
    exchange: str = Field(default="NSE", max_length=20)
    scheme: Literal["inverse_volatility", "equal_weight"] = (
        "inverse_volatility"
    )

class CompareInvestmentsInput(ToolInput):
    symbols: list[str] = Field(min_length=2, max_length=3)
    exchange: str = Field(default="NSE", max_length=20)

class CreateWatchInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    condition: Literal[
        "rsi_below", "rsi_above", "price_above_ema20", "price_below_ema20"
    ]
    threshold: float | None = Field(default=None, ge=0, le=100)
    exchange: str = Field(default="NSE", max_length=20)

class WatchSymbolInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(default="NSE", max_length=20)

class RememberInput(ToolInput):
    note: str = Field(min_length=1, max_length=500)

class RecallMemoryInput(ToolInput):
    query: str | None = Field(default=None, max_length=200)

class FundamentalAnalysisInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    market_price: float | None = Field(default=None, gt=0)

class RunScreenInput(ToolInput):
    name: str = Field(min_length=1, max_length=80)

class FetchWebDocumentInput(ToolInput):
    url: str = Field(min_length=8, max_length=1000)
    title: str | None = Field(default=None, max_length=200)

class WatchlistSymbolInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(default="NSE", max_length=20)

class TechnicalScreenInput(ToolInput):
    condition: Literal[
        "rsi_below",
        "rsi_above",
        "price_above_ema",
        "price_below_ema",
        "volume_spike",
    ]
    threshold: float = Field(default=30.0, gt=0)
    period: int = Field(default=14, ge=2, le=200)
    interval: str = Field(default="D", max_length=8)
    universe: str | None = Field(default=None, max_length=30)

class ApprovePendingOrderInput(ToolInput):
    intent_id: str | None = Field(default=None, max_length=60)

class DirectOrderInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=1, le=100_000)
    side: Literal["BUY", "SELL"] = "BUY"
    exchange: str = Field(default="NSE", max_length=20)
    product: Literal["MIS", "CNC", "NRML"] = "MIS"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: float | None = Field(default=None, gt=0)

class CreatePriceAlertInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=40)
    direction: Literal["above", "below"]
    threshold: float = Field(gt=0)
    exchange: str = Field(default="NSE", max_length=20)

class OptionChainInput(ToolInput):
    underlying: str = Field(min_length=1, max_length=40)
    exchange: str = Field(default="NSE_INDEX", max_length=20)
    expiry_date: str | None = Field(default=None, max_length=12)
    strike_count: int = Field(default=10, ge=1, le=50)

class PlatformReadinessInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=80)
    exchange: str = Field(min_length=1, max_length=20)
    asset_class: Literal[
        "equity",
        "index",
        "futures",
        "options",
        "commodity",
        "crypto",
    ]
    interval: str = Field(min_length=1, max_length=20)
    start_date: str = Field(min_length=4, max_length=20)
    end_date: str = Field(min_length=4, max_length=20)

class OpenAlgoHistoryImportInput(ToolInput):
    symbol: str = Field(min_length=1, max_length=80)
    exchange: str = Field(min_length=1, max_length=20)
    asset_class: Literal[
        "equity", "index", "futures", "options", "commodity", "crypto"
    ]
    interval: str = Field(min_length=1, max_length=20)
    start_date: str = Field(min_length=4, max_length=20)
    end_date: str = Field(min_length=4, max_length=20)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=160)

class MarketNewsInput(ToolInput):
    query: str | None = Field(default=None, max_length=500)
    symbol: str | None = Field(default=None, max_length=80)

class PrepareSandboxIntentInput(ToolInput):
    decision_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1, max_length=100)
    exchange: Literal["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX"]
    side: Literal["BUY", "SELL"]
    product: Literal["MIS", "CNC", "NRML"]
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] = "MARKET"
    quantity: int = Field(ge=1, le=100_000)
    strategy_name: str = Field(min_length=1, max_length=200)
    limit_price: float | None = Field(default=None, ge=0)
    trigger_price: float | None = Field(default=None, ge=0)
    requested_by: str = Field(default="chat_user", min_length=1, max_length=200)

class SandboxIntentActionInput(ToolInput):
    intent_id: str = Field(min_length=1)
    actor: str = Field(default="chat_user", min_length=1, max_length=200)


class UpdateCustomStrategySpecInput(CreateCustomStrategySpecInput):
    spec_id: str = Field(min_length=1, max_length=160)
class PrepareLiveIntentInput(PrepareSandboxIntentInput):
    pass
