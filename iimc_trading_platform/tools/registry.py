from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config import AppConfig
from ..db import connect
from ..domain import ExecutionMode
from ..infrastructure import DuckDBAuditRepository
from ..infrastructure.openalgo import OpenAlgoClient
from ..services.capability_coverage_service import CapabilityCoverageService
from ..services.audit_service import AuditService
from ..services.backtest_service import BacktestService
from ..services.custom_strategy_service import CustomStrategyService
from ..services.evidence_service import EvidenceService
from ..services.execution_readiness_service import ExecutionReadinessService
from ..services.freshness_service import FreshnessService
from ..services.instrument_discovery_service import InstrumentDiscoveryService
from ..services.knowledge_service import KnowledgeService
from ..services.market_news_service import MarketNewsService
from ..services.openalgo_readiness_service import OpenAlgoReadinessService
from ..services.openalgo_service import OpenAlgoSnapshotService
from ..services.operations_service import build_task_service
from ..services.order_service import get_order_timeline
from ..services.platform_dashboard_service import PlatformDashboardService
from ..services.persona_service import PersonaService
from ..services.portfolio_service import PortfolioService
from ..services.research_service import ResearchService
from ..services.risk_service import get_risk_summary
from ..services.sandbox_execution_service import SandboxExecutionService
from .catalog_tools import get_dataset_detail, list_datasets


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    source: str = Field(default="close", min_length=1, max_length=40)
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


class CreateCustomStrategySpecInput(ToolInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    symbol: str = Field(min_length=1, max_length=80)
    timeframe: str = Field(min_length=1, max_length=20)
    indicators: list[CustomIndicatorSpec] = Field(min_length=1, max_length=12)
    entry_rules: list[CustomRuleSpec] = Field(min_length=1, max_length=12)
    exit_rules: list[CustomRuleSpec] = Field(min_length=1, max_length=12)
    risk: CustomStrategyRiskSpec = Field(default_factory=CustomStrategyRiskSpec)
    position_side: Literal["long", "short"] = "long"
    created_by: str = Field(default="chat_user", min_length=1, max_length=200)


class ListCustomStrategySpecsInput(ToolInput):
    limit: int = Field(default=50, ge=1, le=200)


class RunCustomStrategySpecInput(ToolInput):
    spec_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    execution_mode: ExecutionMode = ExecutionMode.RESEARCH
    requested_quantity: int = Field(default=1, ge=1, le=100_000)
    starting_equity: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=1.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1_000)


class RunBacktestInput(ToolInput):
    strategy_name: str = "ema_crossover"
    dataset_id: str | None = None
    parameters: StrategyParameters = Field(default_factory=StrategyParameters)
    execution_mode: ExecutionMode = ExecutionMode.RESEARCH
    requested_quantity: int = Field(default=1, ge=1, le=100_000)
    starting_equity: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=1.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1_000)


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
    ]


class InstrumentSearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200)
    exchange: Literal["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX"]


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


class PrepareLiveIntentInput(PrepareSandboxIntentInput):
    pass


class SandboxIntentActionInput(ToolInput):
    intent_id: str = Field(min_length=1)
    actor: str = Field(default="chat_user", min_length=1, max_length=200)


ToolHandler = Callable[[ToolInput], dict[str, Any]]


@dataclass(frozen=True)
class ToolCapabilityMetadata:
    actions: tuple[str, ...] = ()
    asset_classes: tuple[str, ...] = ()
    execution_modes: tuple[str, ...] = ()
    required_data: tuple[str, ...] = ()
    required_providers: tuple[str, ...] = ()
    requires_approval: bool = False
    risk_level: Literal["low", "medium", "high"] = "low"

    def as_dict(self) -> dict[str, Any]:
        return {
            "actions": list(self.actions),
            "asset_classes": list(self.asset_classes),
            "execution_modes": list(self.execution_modes),
            "required_data": list(self.required_data),
            "required_providers": list(self.required_providers),
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
        }

    def summary(self) -> str:
        parts = [f"risk={self.risk_level}"]
        if self.actions:
            parts.append(f"actions={','.join(self.actions)}")
        if self.asset_classes:
            parts.append(f"assets={','.join(self.asset_classes)}")
        if self.execution_modes:
            parts.append(f"modes={','.join(self.execution_modes)}")
        if self.required_data:
            parts.append(f"data={','.join(self.required_data)}")
        if self.required_providers:
            parts.append(f"providers={','.join(self.required_providers)}")
        if self.requires_approval:
            parts.append("approval=required")
        return "; ".join(parts)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: ToolHandler
    side_effects: str
    retry_safe: bool
    required_role: str = "viewer"
    capabilities: ToolCapabilityMetadata = ToolCapabilityMetadata()

    def validate(self, payload: dict[str, Any] | None) -> ToolInput:
        if payload is None:
            payload = {}
        return self.input_model.model_validate(payload)

    @property
    def is_read_only(self) -> bool:
        return self.side_effects == "none" or self.side_effects.startswith(
            "read-only"
        )

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": (
                f"{self.description} Side effects: {self.side_effects}. "
                f"Retry safe: {str(self.retry_safe).lower()}. "
                f"Required role: {self.required_role}. "
                f"Capabilities: {self.capabilities.summary()}."
            ),
            "parameters": _strict_schema(self.input_model.model_json_schema()),
            "strict": True,
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]):
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool: {tool.name}")
            self._tools[tool.name] = tool

    def get(self, tool_name: str) -> ToolDefinition:
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise ValueError(f"Unknown tool: {tool_name}") from exc

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": _strict_schema(
                    tool.input_model.model_json_schema()
                ),
                "side_effects": tool.side_effects,
                "retry_safe": tool.retry_safe,
                "required_role": tool.required_role,
                "capabilities": tool.capabilities.as_dict(),
            }
            for tool in self._tools.values()
        ]

    def openai_tools(self) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self._tools.values()]

    def subset(self, allowed_names: set[str]) -> ToolRegistry:
        return ToolRegistry(
            [
                tool
                for name, tool in self._tools.items()
                if name in allowed_names
            ]
        )

    def allowed_for_role(self, role: str) -> set[str]:
        rank = {
            "viewer": 1,
            "researcher": 2,
            "approver": 3,
            "admin": 4,
        }
        active_rank = rank.get(role, 0)
        return {
            name
            for name, tool in self._tools.items()
            if active_rank >= rank[tool.required_role]
        }

    def call(self, tool_name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        tool = self.get(tool_name)
        validated = tool.validate(payload)
        return tool.handler(validated)


def build_default_tool_registry(
    db_path: Path,
    *,
    allow_live_trading: bool = False,
    openalgo_base_url: str | None = None,
    openalgo_api_key: str | None = None,
    artifacts_dir: Path = Path("artifacts"),
    app_config: AppConfig | None = None,
) -> ToolRegistry:
    active_config = app_config or AppConfig(
        database_path=db_path,
        artifacts_dir=artifacts_dir,
        openalgo_base_url=openalgo_base_url or "http://127.0.0.1:5000",
        openalgo_api_key=openalgo_api_key,
        allow_live_trading=allow_live_trading,
    )
    backtests = BacktestService(
        db_path,
        allow_live_trading=allow_live_trading,
    )
    freshness = FreshnessService(db_path)
    knowledge = KnowledgeService(db_path)
    evidence = EvidenceService(db_path, artifacts_dir)
    from ..services.robustness_service import RobustnessService

    robustness = RobustnessService(db_path)
    portfolios = PortfolioService(db_path)
    tasks = build_task_service(db_path)
    custom_strategies = CustomStrategyService(db_path)
    openalgo_readiness = OpenAlgoReadinessService(active_config)
    capabilities = CapabilityCoverageService(db_path, openalgo_readiness)
    execution_readiness = ExecutionReadinessService(
        active_config,
        capabilities,
        openalgo_readiness,
    )
    news = MarketNewsService(active_config)
    research = ResearchService(
        db_path,
        capabilities,
        news,
        execution_readiness,
    )
    platform_dashboard = PlatformDashboardService(active_config)
    personas = PersonaService(db_path)
    instruments = InstrumentDiscoveryService(active_config)
    sandbox_read = SandboxExecutionService(
        db_path,
        AuditService(DuckDBAuditRepository(db_path)),
        None,
        require_approval=active_config.require_paper_approval,
        allow_live_trading=active_config.allow_live_trading,
    )

    def run_backtest_tool(value: ToolInput) -> dict[str, Any]:
        request = RunBacktestInput.model_validate(value.model_dump())
        dataset_id = request.dataset_id or _latest_dataset_id(db_path)
        if dataset_id is None:
            raise ValueError("No cataloged dataset is available")
        return backtests.run(
            strategy_name=request.strategy_name,
            dataset_id=dataset_id,
            parameters=request.parameters.values_for_strategy(),
            execution_mode=request.execution_mode,
            requested_quantity=request.requested_quantity,
            starting_equity=request.starting_equity,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
        )

    def submit_robustness_tool(value: ToolInput) -> dict[str, Any]:
        request = RobustnessExperimentInput.model_validate(
            value.model_dump()
        )
        return tasks.submit(
            task_type="robustness_experiment",
            payload={
                "strategy_name": request.strategy_name,
                "dataset_id": request.dataset_id,
                "parameter_grid": [
                    candidate.values_for_strategy()
                    for candidate in request.parameter_grid
                ],
                "split_ratio": request.split_ratio,
                "requested_quantity": request.requested_quantity,
                "starting_equity": request.starting_equity,
                "fee_bps": request.fee_bps,
                "slippage_bps": request.slippage_bps,
                "persist_selected_runs": request.persist_selected_runs,
                "requested_by": "chat_user",
            },
            requested_by="chat_user",
        )

    tools = [
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
                name="list_datasets",
                description="List governed datasets and their quality metadata.",
                input_model=EmptyInput,
                handler=lambda value: list_datasets(db_path),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="get_dataset_detail",
                description="Retrieve one dataset by its exact dataset ID.",
                input_model=DatasetDetailInput,
                handler=lambda value: get_dataset_detail(
                    DatasetDetailInput.model_validate(
                        value.model_dump()
                    ).dataset_id,
                    db_path,
                ),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="assess_dataset_freshness",
                description=(
                    "Assess whether a governed dataset is fit for a specific "
                    "purpose such as historical research or current-market use."
                ),
                input_model=DatasetFreshnessInput,
                handler=lambda value: freshness.assess(
                    DatasetFreshnessInput.model_validate(
                        value.model_dump()
                    ).dataset_id,
                    DatasetFreshnessInput.model_validate(
                        value.model_dump()
                    ).purpose,
                ),
                side_effects="creates a persisted freshness assessment",
                retry_safe=True,
            ),
            ToolDefinition(
                name="list_knowledge_documents",
                description=(
                    "List governed unstructured documents available for "
                    "retrieval."
                ),
                input_model=EmptyInput,
                handler=lambda value: knowledge.list_documents(),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="search_knowledge",
                description=(
                    "Retrieve relevant governed document chunks with exact "
                    "document and chunk provenance. Do not use for market facts."
                ),
                input_model=KnowledgeSearchInput,
                handler=lambda value: knowledge.search(
                    KnowledgeSearchInput.model_validate(
                        value.model_dump()
                    ).query,
                    limit=KnowledgeSearchInput.model_validate(
                        value.model_dump()
                    ).limit,
                ),
                side_effects="creates a retrieval audit event",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("retrieve", "explain"),
                    execution_modes=("research",),
                    required_data=("governed_documents",),
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
                name="list_strategies",
                description=(
                    "List registered deterministic strategy plugins and "
                    "parameter schemas."
                ),
                input_model=EmptyInput,
                handler=lambda value: {
                    "strategies": backtests.list_strategies()
                },
                side_effects="none",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("list",),
                    execution_modes=("research",),
                    required_data=("strategy_registry",),
                    risk_level="low",
                ),
            ),
            ToolDefinition(
                name="get_custom_strategy_capabilities",
                description=(
                    "Return the current deterministic custom-strategy rule "
                    "vocabulary, position sides, risk controls, and execution "
                    "policy so unsupported requests can be identified before "
                    "a strategy draft is created."
                ),
                input_model=EmptyInput,
                handler=lambda _: custom_strategies.capabilities(),
                side_effects="read-only capability query",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("inspect_capabilities",),
                    asset_classes=(
                        "equity",
                        "index",
                        "futures",
                        "options",
                        "commodity",
                        "crypto",
                    ),
                    execution_modes=("research",),
                    required_data=("OHLCV",),
                    risk_level="low",
                ),
            ),
            ToolDefinition(
                name="create_custom_strategy_spec",
                description=(
                    "Create and persist a governed draft strategy spec from "
                    "structured indicators, entry rules, exit rules, and risk "
                    "constraints. This does not execute generated code or place "
                    "orders; unsupported primitives are marked requires_review."
                ),
                input_model=CreateCustomStrategySpecInput,
                handler=lambda value: custom_strategies.create_spec(
                    **CreateCustomStrategySpecInput.model_validate(
                        value.model_dump()
                    ).model_dump()
                ),
                side_effects=(
                    "creates a persisted custom strategy draft for review"
                ),
                retry_safe=False,
                required_role="researcher",
                capabilities=ToolCapabilityMetadata(
                    actions=("draft_strategy", "validate_strategy_spec"),
                    asset_classes=(
                        "equity",
                        "index",
                        "futures",
                        "options",
                        "commodity",
                        "crypto",
                    ),
                    execution_modes=("research",),
                    required_data=("strategy_spec",),
                    requires_approval=True,
                    risk_level="medium",
                ),
            ),
            ToolDefinition(
                name="list_custom_strategy_specs",
                description=(
                    "List governed custom strategy draft specs, including "
                    "whether each spec is executable with current primitives "
                    "or requires human review/new backend implementation."
                ),
                input_model=ListCustomStrategySpecsInput,
                handler=lambda value: custom_strategies.list_specs(
                    ListCustomStrategySpecsInput.model_validate(
                        value.model_dump()
                    ).limit
                ),
                side_effects="read-only database query",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("list", "review_strategy_specs"),
                    execution_modes=("research",),
                    required_data=("custom_strategy_specs",),
                    risk_level="low",
                ),
            ),
            ToolDefinition(
                name="run_custom_strategy_spec",
                description=(
                    "Backtest a persisted custom strategy spec through the "
                    "native deterministic rule-spec runtime. Unsupported specs "
                    "fail closed; arbitrary generated code is never executed."
                ),
                input_model=RunCustomStrategySpecInput,
                handler=lambda value: custom_strategies.run_backtest(
                    **RunCustomStrategySpecInput.model_validate(
                        value.model_dump()
                    ).model_dump()
                ),
                side_effects=(
                    "creates persisted research workflow records for a custom "
                    "rule-spec strategy"
                ),
                retry_safe=False,
                required_role="researcher",
                capabilities=ToolCapabilityMetadata(
                    actions=("backtest", "execute_strategy_spec"),
                    asset_classes=(
                        "equity",
                        "index",
                        "futures",
                        "options",
                        "commodity",
                        "crypto",
                    ),
                    execution_modes=("research", "semi_auto", "live"),
                    required_data=(
                        "custom_strategy_specs",
                        "historical_ohlcv",
                    ),
                    risk_level="medium",
                ),
            ),
            ToolDefinition(
                name="run_backtest",
                description=(
                    "Run a deterministic strategy on a governed dataset. "
                    "Stores the run, signals, risk decisions, simulated orders, "
                    "fills, and performance summary."
                ),
                input_model=RunBacktestInput,
                handler=run_backtest_tool,
                side_effects="creates persisted research workflow records",
                retry_safe=False,
                required_role="researcher",
                capabilities=ToolCapabilityMetadata(
                    actions=("backtest",),
                    asset_classes=(
                        "equity",
                        "index",
                        "futures",
                        "options",
                        "commodity",
                        "crypto",
                    ),
                    execution_modes=("research", "semi_auto", "live"),
                    required_data=("historical_ohlcv", "strategy_registry"),
                    risk_level="medium",
                ),
            ),
            ToolDefinition(
                name="get_backtest_result",
                description="Retrieve a stored strategy run and summary by run ID.",
                input_model=RunIdInput,
                handler=lambda value: _require_result(
                    backtests.get_result(
                        RunIdInput.model_validate(
                            value.model_dump()
                        ).run_id
                    )
                ),
                side_effects="read-only database query",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("monitor",),
                    execution_modes=("research",),
                    required_data=("performance_summaries",),
                    risk_level="low",
                ),
            ),
            ToolDefinition(
                name="get_performance",
                description=(
                    "Retrieve performance summary, equity curve, and drawdown "
                    "for a stored run."
                ),
                input_model=RunIdInput,
                handler=lambda value: backtests.get_performance(
                    RunIdInput.model_validate(value.model_dump()).run_id
                ),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="get_risk_decisions",
                description="Retrieve detailed risk decisions for a stored run.",
                input_model=RunIdInput,
                handler=lambda value: get_risk_summary(
                    db_path,
                    RunIdInput.model_validate(value.model_dump()).run_id,
                ),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="get_order_timeline",
                description=(
                    "Retrieve orders and append-only state transitions for a run."
                ),
                input_model=RunIdInput,
                handler=lambda value: get_order_timeline(
                    db_path,
                    RunIdInput.model_validate(value.model_dump()).run_id,
                ),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="get_run_timeline",
                description=(
                    "Retrieve one chronological workflow joining strategy "
                    "signals, risk decisions, orders, and fills for a run."
                ),
                input_model=RunIdInput,
                handler=lambda value: evidence.run_timeline(
                    RunIdInput.model_validate(value.model_dump()).run_id
                ),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="compare_runs",
                description=(
                    "Compare two to ten stored strategy runs using persisted "
                    "performance evidence."
                ),
                input_model=RunComparisonInput,
                handler=lambda value: evidence.compare_runs(
                    RunComparisonInput.model_validate(
                        value.model_dump()
                    ).run_ids
                ),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="create_run_report",
                description=(
                    "Generate and persist a Markdown evidence report for one "
                    "stored strategy run."
                ),
                input_model=RunIdInput,
                handler=lambda value: evidence.create_run_report(
                    RunIdInput.model_validate(value.model_dump()).run_id,
                    created_by="chat_user",
                ),
                side_effects="creates a persisted report artifact",
                retry_safe=False,
                required_role="researcher",
            ),
            ToolDefinition(
                name="list_reports",
                description="List persisted strategy evidence reports.",
                input_model=EmptyInput,
                handler=lambda value: evidence.list_reports(),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="run_robustness_experiment",
                description=(
                    "Queue a durable chronological train/test parameter "
                    "sensitivity and benchmark experiment."
                ),
                input_model=RobustnessExperimentInput,
                handler=submit_robustness_tool,
                side_effects="creates a durable queued work task",
                retry_safe=True,
                required_role="researcher",
            ),
            ToolDefinition(
                name="get_robustness_experiment",
                description=(
                    "Retrieve a persisted robustness experiment, trials, "
                    "benchmark, and verdict."
                ),
                input_model=ExperimentIdInput,
                handler=lambda value: robustness.get(
                    ExperimentIdInput.model_validate(
                        value.model_dump()
                    ).experiment_id
                ),
                side_effects="read-only database query",
                retry_safe=True,
            ),
            ToolDefinition(
                name="list_robustness_experiments",
                description="List persisted strategy robustness experiments.",
                input_model=EmptyInput,
                handler=lambda value: robustness.list(),
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
    if openalgo_base_url and openalgo_api_key:
        sandbox = SandboxExecutionService(
            db_path,
            AuditService(DuckDBAuditRepository(db_path)),
            OpenAlgoClient(openalgo_base_url, openalgo_api_key),
            require_approval=active_config.require_paper_approval,
            allow_live_trading=active_config.allow_live_trading,
        )
        snapshots = OpenAlgoSnapshotService(
            db_path,
            OpenAlgoClient(openalgo_base_url, openalgo_api_key),
        )
        tools.append(
            ToolDefinition(
                name="get_openalgo_snapshot",
                description=(
                    "Retrieve a read-only account snapshot from OpenAlgo and "
                    "persist the sanitized response for audit."
                ),
                input_model=OpenAlgoSnapshotInput,
                handler=lambda value: snapshots.capture(
                    OpenAlgoSnapshotInput.model_validate(
                        value.model_dump()
                    ).snapshot_type
                ),
                side_effects=(
                    "read-only OpenAlgo API call and local snapshot record"
                ),
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("monitor", "snapshot"),
                    asset_classes=("equity", "futures", "options", "commodity"),
                    execution_modes=("paper", "live"),
                    required_providers=("openalgo",),
                    risk_level="low",
                ),
            )
        )
        tools.extend(
            [
                ToolDefinition(
                    name="prepare_sandbox_order_intent",
                    description=(
                        "Prepare a risk-approved OpenAlgo sandbox order intent "
                        "and create a pending human approval. This does not "
                        "submit an order."
                    ),
                    input_model=PrepareSandboxIntentInput,
                    handler=lambda value: sandbox.prepare_intent(
                        **PrepareSandboxIntentInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects=(
                        "creates an order intent and human approval request"
                    ),
                    retry_safe=True,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("prepare_order",),
                        asset_classes=(
                            "equity",
                            "futures",
                            "options",
                            "commodity",
                        ),
                        execution_modes=("paper",),
                        required_data=("risk_decision", "instrument_metadata"),
                        required_providers=("openalgo",),
                        requires_approval=True,
                        risk_level="high",
                    ),
                ),
                ToolDefinition(
                    name="prepare_live_order_intent",
                    description=(
                        "Prepare a risk-approved live OpenAlgo order intent "
                        "and create a mandatory human approval. This does not "
                        "submit an order."
                    ),
                    input_model=PrepareLiveIntentInput,
                    handler=lambda value: sandbox.prepare_live_intent(
                        **PrepareLiveIntentInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects=(
                        "creates a live order intent and mandatory human "
                        "approval request"
                    ),
                    retry_safe=True,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("prepare_order",),
                        asset_classes=(
                            "equity",
                            "futures",
                            "options",
                            "commodity",
                        ),
                        execution_modes=("live",),
                        required_data=("risk_decision", "instrument_metadata"),
                        required_providers=("openalgo",),
                        requires_approval=True,
                        risk_level="high",
                    ),
                ),
                ToolDefinition(
                    name="list_pending_approvals",
                    description=(
                        "List pending human approvals. The model cannot decide "
                        "or approve them."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: sandbox.list_pending_approvals(),
                    side_effects="read-only database query",
                    retry_safe=True,
                    required_role="approver",
                    capabilities=ToolCapabilityMetadata(
                        actions=("approve", "monitor"),
                        execution_modes=("paper", "live"),
                        required_data=("approvals",),
                        requires_approval=True,
                        risk_level="high",
                    ),
                ),
                ToolDefinition(
                    name="reconcile_sandbox_intent",
                    description=(
                        "Retrieve OpenAlgo order status and reconcile the "
                        "platform order state with an audit snapshot."
                    ),
                    input_model=SandboxIntentActionInput,
                    handler=lambda value: sandbox.reconcile(
                        SandboxIntentActionInput.model_validate(
                            value.model_dump()
                        ).intent_id,
                        actor=SandboxIntentActionInput.model_validate(
                            value.model_dump()
                        ).actor,
                    ),
                    side_effects=(
                        "reads OpenAlgo state and updates local order state"
                    ),
                    retry_safe=True,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("reconcile",),
                        asset_classes=(
                            "equity",
                            "futures",
                            "options",
                            "commodity",
                        ),
                        execution_modes=("paper", "live"),
                        required_data=("order_intent",),
                        required_providers=("openalgo",),
                        risk_level="medium",
                    ),
                ),
            ]
        )
    return ToolRegistry(tools)


def _latest_dataset_id(db_path: Path) -> str | None:
    con = connect(db_path)
    try:
        row = con.execute(
            """
            SELECT dataset_id
            FROM data_catalog
            WHERE quality_status NOT IN ('rejected', 'empty')
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _require_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        raise ValueError("Run not found")
    return result


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Pydantic schema for OpenAI strict function tools."""
    normalized = {
        key: _strict_schema(value) if isinstance(value, dict) else (
            [
                _strict_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
            if isinstance(value, list)
            else value
        )
        for key, value in schema.items()
        if key != "default"
    }
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized
