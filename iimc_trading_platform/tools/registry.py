from __future__ import annotations

from pathlib import Path
from typing import Any


from ..config import AppConfig
from ..db import connect
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
from ..services.openalgo_history_import_service import OpenAlgoHistoryImportService
from ..services.openalgo_service import OpenAlgoSnapshotService
from ..services.operations_service import build_task_service
from ..services.platform_dashboard_service import PlatformDashboardService
from ..services.persona_service import PersonaService
from ..services.portfolio_service import PortfolioService
from ..services.research_service import ResearchService
from ..services.sandbox_execution_service import SandboxExecutionService
from .contracts import (
    ToolCapabilityMetadata,
    ToolDefinition,
    ToolInput,
    ToolRegistry,
)
from .inputs import (
    PrepareLiveIntentInput,
    ApprovePendingOrderInput,
    DirectOrderInput,
    EmptyInput,
    OpenAlgoSnapshotInput,
    OptionChainInput,
    PortfolioIdInput,
    PrepareSandboxIntentInput,
    RobustnessExperimentInput,
    RunBacktestInput,
    SandboxIntentActionInput,
    TechnicalScreenInput,
)
from . import catalog
# Re-exported so every existing import site keeps working after the split.
# The catalogue moved; the module's public surface did not.
from .inputs import (  # noqa: F401
    CompileCustomStrategyInput,
    CreateCustomStrategySpecInput,
    DatasetDetailInput,
    DatasetFreshnessInput,
    InstrumentSearchInput,
    KnowledgeSearchInput,
    ListCustomStrategySpecsInput,
    MarketQuoteInput,
    OptionSymbolInput,
    RunCustomStrategySpecInput,
    RunIdInput,
    SymbolValidationInput,
)


























































































































# The order tools are registered in is the order they are presented to the
# LLM router. Splitting the catalogue into groups reshuffled it, so the
# original sequence is pinned here rather than left to depend on which file
# a tool happens to live in.
_TOOL_ORDER = (
    "get_platform_summary",
    "list_datasets",
    "get_dataset_detail",
    "assess_dataset_freshness",
    "list_knowledge_documents",
    "search_knowledge",
    "analyze_fundamentals",
    "add_watchlist_symbol",
    "remove_watchlist_symbol",
    "list_watchlist",
    "create_price_alert",
    "list_price_alerts",
    "run_screen",
    "fetch_web_document",
    "run_strategy_optimization",
    "validate_strategy_walk_forward",
    "deep_research",
    "deep_research_report",
    "create_watch",
    "list_watches",
    "remove_watch",
    "check_watches",
    "compare_investments",
    "analyse_portfolio",
    "get_data_health",
    "remember",
    "recall_memory",
    "find_and_analyze_document",
    "analyze_knowledge_document",
    "check_platform_readiness",
    "get_research_context",
    "create_research_brief",
    "get_execution_readiness",
    "get_openalgo_monitor",
    "search_instruments",
    "validate_instrument_symbol",
    "get_market_quote",
    "resolve_option_symbol",
    "list_sandbox_intents",
    "get_market_news",
    "list_strategy_personas",
    "get_strategy_persona",
    "list_strategies",
    "get_custom_strategy_capabilities",
    "compile_custom_strategy_spec",
    "create_custom_strategy_spec",
    "update_custom_strategy_spec",
    "list_custom_strategy_specs",
    "run_custom_strategy_spec",
    "run_backtest",
    "get_backtest_result",
    "get_performance",
    "get_risk_decisions",
    "get_order_timeline",
    "get_run_timeline",
    "compare_runs",
    "create_run_report",
    "import_openalgo_history",
    "list_reports",
    "run_robustness_experiment",
    "get_robustness_experiment",
    "list_robustness_experiments",
    "list_portfolios",
    "get_portfolio_snapshot",
)


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
        strategy_plugin_dir=active_config.strategy_plugin_dir,
        allow_live_trading=allow_live_trading,
    )
    freshness = FreshnessService(db_path)
    knowledge = KnowledgeService(db_path)
    from ..services.fundamentals_service import FundamentalsService
    from ..services.price_alert_service import PriceAlertService
    from ..services.screen_service import ScreenService

    fundamentals = FundamentalsService(db_path)
    screens = ScreenService(db_path)

    def _price_alerts(path: Path) -> PriceAlertService:
        return PriceAlertService(path)

    from ..services.screener_service import ScreenerService

    def _screener(path: Path) -> ScreenerService:
        client = (
            OpenAlgoClient(openalgo_base_url, openalgo_api_key)
            if openalgo_base_url and openalgo_api_key
            else None
        )
        return ScreenerService(path, client)
    evidence = EvidenceService(db_path, artifacts_dir)
    from ..services.robustness_service import RobustnessService

    robustness = RobustnessService(db_path)
    portfolios = PortfolioService(db_path)
    tasks = build_task_service(db_path)
    custom_strategies = CustomStrategyService(db_path)
    openalgo_readiness = OpenAlgoReadinessService(active_config)
    openalgo_history_import = OpenAlgoHistoryImportService(active_config)
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
    from ..services.deep_research_loop_service import DeepResearchLoopService
    from ..services.memory_service import MemoryService
    from ..services.plan_execute_service import PlanExecuteService
    from ..services.research_agent_service import ResearchAgentService
    from ..services.strategy_optimizer_service import StrategyOptimizerService

    from ..services.data_health_service import DataHealthService
    from ..services.portfolio_agent_service import PortfolioAgentService

    def _portfolio_agent() -> PortfolioAgentService:
        return PortfolioAgentService(
            db_path,
            backtests,
            lambda symbol, exchange: _dataset_for_request(
                db_path,
                symbol=symbol,
                exchange=exchange,
                raise_on_missing=False,
            ),
        )

    def _data_health(path: Path) -> DataHealthService:
        return DataHealthService(path)

    memory = MemoryService(db_path)
    research_agent = ResearchAgentService(
        fundamentals, news, instruments, _screener(db_path), memory=memory
    )
    deep_research_loop = DeepResearchLoopService(research_agent, knowledge)
    plan_execute = PlanExecuteService(research_agent)
    optimizer = StrategyOptimizerService(backtests)

    from ..services.watch_service import WatchService

    watches = WatchService(db_path, _screener(db_path))

    def _resolve_dataset_for_symbol(
        symbol: str | None,
        exchange: str | None,
        asset_class: str | None,
        interval: str | None,
    ) -> str:
        dataset_id = _dataset_for_request(
            db_path,
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            raise_on_missing=False,
        )
        if dataset_id is None and symbol and active_config.openalgo_api_key:
            from datetime import date, timedelta

            end = date.today()
            start = end - timedelta(days=45)
            try:
                imported = openalgo_history_import.import_history(
                    symbol=symbol,
                    exchange=exchange or "NSE",
                    asset_class=asset_class or "equity",
                    interval=interval or "5m",
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                )
                dataset_id = imported.get("dataset_id")
            except Exception as exc:
                raise ValueError(
                    f"I couldn't pull {symbol} history from your broker "
                    f"({exc}). Check the symbol and your broker connection."
                ) from exc
        if dataset_id is None:
            raise ValueError(
                f"No historical data is available for {symbol or 'this instrument'} "
                "and the broker connection isn't set up to fetch it."
            )
        return dataset_id
    sandbox_read = SandboxExecutionService(
        db_path,
        AuditService(DuckDBAuditRepository(db_path)),
        None,
        require_approval=active_config.require_paper_approval,
        allow_live_trading=active_config.allow_live_trading,
        max_signal_age_minutes=active_config.paper_signal_max_age_minutes,
    )

    def run_backtest_tool(value: ToolInput) -> dict[str, Any]:
        request = RunBacktestInput.model_validate(value.model_dump())
        dataset_id = request.dataset_id or _dataset_for_request(
            db_path,
            symbol=request.symbol,
            exchange=request.exchange,
            asset_class=request.asset_class,
            interval=request.interval,
            raise_on_missing=False,
        )
        if (
            dataset_id is None
            and request.symbol
            and active_config.openalgo_api_key
        ):
            # Auto-fetch history from the broker so users never import
            # manually: last 45 days at the requested (default 5m) interval.
            from datetime import date, timedelta

            end = date.today()
            start = end - timedelta(days=45)
            try:
                imported = openalgo_history_import.import_history(
                    symbol=request.symbol,
                    exchange=request.exchange or "NSE",
                    asset_class=request.asset_class or "equity",
                    interval=request.interval or "5m",
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                )
                dataset_id = imported.get("dataset_id")
            except Exception as exc:
                raise ValueError(
                    f"I couldn't pull {request.symbol} history from your "
                    f"broker ({exc}). Check the symbol and your broker "
                    "connection."
                ) from exc
        if dataset_id is None:
            raise ValueError(
                f"No historical data is available for {request.symbol or 'this instrument'} "
                "and the broker connection isn't set up to fetch it. Connect "
                "OpenAlgo to enable automatic data."
            )
        return backtests.run(
            strategy_name=request.strategy_name,
            dataset_id=dataset_id,
            parameters=request.parameters,
            execution_mode=request.execution_mode,
            requested_quantity=request.requested_quantity,
            starting_equity=request.starting_equity,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
            instrument=(
                request.instrument.model_dump(exclude_none=True)
                if request.instrument else None
            ),
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

    grouped = [
        *catalog.platform.build(
            capabilities=capabilities,
            execution_readiness=execution_readiness,
            openalgo_readiness=openalgo_readiness,
            personas=personas,
            platform_dashboard=platform_dashboard,
            portfolios=portfolios,
            sandbox_read=sandbox_read,
            watches=watches,
        ),
        *catalog.data.build(
            _data_health=_data_health,
            db_path=db_path,
            freshness=freshness,
            openalgo_history_import=openalgo_history_import,
        ),
        *catalog.knowledge.build(
            knowledge=knowledge,
        ),
        *catalog.research.build(
            _portfolio_agent=_portfolio_agent,
            deep_research_loop=deep_research_loop,
            fundamentals=fundamentals,
            memory=memory,
            plan_execute=plan_execute,
            research=research,
            research_agent=research_agent,
        ),
        *catalog.market.build(
            _price_alerts=_price_alerts,
            _screener=_screener,
            db_path=db_path,
            instruments=instruments,
            news=news,
            screens=screens,
        ),
        *catalog.strategy.build(
            _resolve_dataset_for_symbol=_resolve_dataset_for_symbol,
            backtests=backtests,
            custom_strategies=custom_strategies,
            db_path=db_path,
            evidence=evidence,
            optimizer=optimizer,
            robustness=robustness,
            run_backtest_tool=run_backtest_tool,
            submit_robustness_tool=submit_robustness_tool,
        ),
    ]
    by_name = {tool.name: tool for tool in grouped}
    # Known tools first, in the pinned order; anything new appended, so a
    # tool added to a group without touching _TOOL_ORDER still registers.
    tools = [by_name.pop(name) for name in _TOOL_ORDER if name in by_name]
    tools.extend(by_name.values())
    if openalgo_base_url and openalgo_api_key:
        sandbox = SandboxExecutionService(
            db_path,
            AuditService(DuckDBAuditRepository(db_path)),
            OpenAlgoClient(openalgo_base_url, openalgo_api_key),
            require_approval=active_config.require_paper_approval,
            allow_live_trading=active_config.allow_live_trading,
            max_signal_age_minutes=active_config.paper_signal_max_age_minutes,
        )
        snapshots = OpenAlgoSnapshotService(
            db_path,
            OpenAlgoClient(openalgo_base_url, openalgo_api_key),
        )
        tools.append(
            ToolDefinition(
                name="square_off_all",
                description=(
                    "Close ALL open positions at market (square off) through "
                    "the broker. Use when the user explicitly says to exit "
                    "all / close everything / square off."
                ),
                input_model=EmptyInput,
                handler=lambda value: snapshots.emergency_action(
                    "square_off_positions", actor="chat_user",
                ),
                side_effects="closes all broker positions",
                required_role="approver",
                retry_safe=False,
                capabilities=ToolCapabilityMetadata(
                    actions=("execute",),
                    execution_modes=("paper", "live"),
                    requires_approval=True,
                    risk_level="high",
                ),
            )
        )
        tools.append(
            ToolDefinition(
                name="cancel_all_orders",
                description=(
                    "Cancel ALL pending/open orders at the broker. Use when "
                    "the user explicitly says to cancel all orders."
                ),
                input_model=EmptyInput,
                handler=lambda value: snapshots.emergency_action(
                    "cancel_all_orders", actor="chat_user",
                ),
                side_effects="cancels all broker orders",
                required_role="approver",
                retry_safe=False,
                capabilities=ToolCapabilityMetadata(
                    actions=("execute",),
                    execution_modes=("paper", "live"),
                    requires_approval=True,
                    risk_level="high",
                ),
            )
        )
        from ..services.options_analytics_service import (
            OptionsAnalyticsService,
        )

        option_analytics = OptionsAnalyticsService(
            db_path,
            OpenAlgoClient(openalgo_base_url, openalgo_api_key),
        )
        from ..services.risk_service import RiskService as _RiskService

        direct_risk = _RiskService(
            db_path,
            allow_live_trading=allow_live_trading,
        )
        tools.append(
            ToolDefinition(
                name="prepare_direct_order",
                description=(
                    "Prepare a discretionary BUY paper order anchored to a "
                    "fresh live quote (no strategy needed). Runs the "
                    "deterministic risk policy and creates an order intent "
                    "that still requires explicit human approval before "
                    "analyzer submission. Never submits or executes."
                ),
                input_model=DirectOrderInput,
                handler=lambda value: sandbox.prepare_direct_intent(
                    risk_service=direct_risk,
                    symbol=DirectOrderInput.model_validate(
                        value.model_dump()
                    ).symbol,
                    side=DirectOrderInput.model_validate(
                        value.model_dump()
                    ).side,
                    quantity=DirectOrderInput.model_validate(
                        value.model_dump()
                    ).quantity,
                    exchange=DirectOrderInput.model_validate(
                        value.model_dump()
                    ).exchange,
                    product=DirectOrderInput.model_validate(
                        value.model_dump()
                    ).product,
                    order_type=DirectOrderInput.model_validate(
                        value.model_dump()
                    ).order_type,
                    limit_price=DirectOrderInput.model_validate(
                        value.model_dump()
                    ).limit_price,
                ),
                side_effects=(
                    "creates a manual signal, risk decision, and a "
                    "pending-approval order intent"
                ),
                required_role="researcher",
                retry_safe=False,
                capabilities=ToolCapabilityMetadata(
                    actions=("order_preparation",),
                    execution_modes=("paper",),
                    required_providers=("openalgo",),
                    requires_approval=True,
                    risk_level="high",
                ),
            )
        )

        def _portfolio_quote(symbol: str) -> float:
            quote_client = OpenAlgoClient(openalgo_base_url, openalgo_api_key)
            data = quote_client.quote(symbol=symbol, exchange="NSE").get(
                "data"
            ) or {}
            return float(data["ltp"])

        tools.append(
            ToolDefinition(
                name="mark_portfolio_to_market",
                description=(
                    "Mark a virtual paper portfolio's open positions "
                    "against live OpenAlgo quotes: per-position unrealized "
                    "P&L, market value, and total equity."
                ),
                input_model=PortfolioIdInput,
                handler=lambda value: portfolios.mark_to_market(
                    PortfolioIdInput.model_validate(
                        value.model_dump()
                    ).portfolio_id,
                    _portfolio_quote,
                ),
                side_effects="read-only OpenAlgo quote requests",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("monitor", "analyze"),
                    execution_modes=("paper",),
                    required_providers=("openalgo",),
                    risk_level="low",
                ),
            )
        )
        tools.append(
            ToolDefinition(
                name="run_technical_screen",
                description=(
                    "Scan a stock universe with live OpenAlgo candles for a "
                    "technical condition: rsi_below/rsi_above <threshold>, "
                    "price_above_ema/price_below_ema <period>, or "
                    "volume_spike <multiplier>. Set universe='nifty50' to scan "
                    "the NIFTY 50, or leave it empty to scan the saved "
                    "watchlist. Unfetchable symbols are reported as skipped."
                ),
                input_model=TechnicalScreenInput,
                handler=lambda value: _screener(db_path).scan(
                    condition=TechnicalScreenInput.model_validate(
                        value.model_dump()
                    ).condition,
                    threshold=TechnicalScreenInput.model_validate(
                        value.model_dump()
                    ).threshold,
                    period=TechnicalScreenInput.model_validate(
                        value.model_dump()
                    ).period,
                    interval=TechnicalScreenInput.model_validate(
                        value.model_dump()
                    ).interval,
                    universe=TechnicalScreenInput.model_validate(
                        value.model_dump()
                    ).universe,
                ),
                side_effects="read-only OpenAlgo history requests",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("screen", "analyze"),
                    execution_modes=("research",),
                    required_providers=("openalgo",),
                    risk_level="low",
                ),
            )
        )
        tools.append(
            ToolDefinition(
                name="get_option_chain",
                description=(
                    "Fetch the live option chain from OpenAlgo for an "
                    "underlying (default nearest expiry) and compute "
                    "deterministic analytics: ATM strike and premiums, "
                    "straddle cost, put-call OI ratio, and max-OI strikes."
                ),
                input_model=OptionChainInput,
                handler=lambda value: option_analytics.chain_snapshot(
                    underlying=OptionChainInput.model_validate(
                        value.model_dump()
                    ).underlying,
                    exchange=OptionChainInput.model_validate(
                        value.model_dump()
                    ).exchange,
                    expiry_date=OptionChainInput.model_validate(
                        value.model_dump()
                    ).expiry_date,
                    strike_count=OptionChainInput.model_validate(
                        value.model_dump()
                    ).strike_count,
                ),
                side_effects="read-only OpenAlgo option-chain request",
                retry_safe=True,
                capabilities=ToolCapabilityMetadata(
                    actions=("quote", "analyze"),
                    asset_classes=("options",),
                    execution_modes=("research",),
                    required_providers=("openalgo",),
                    risk_level="low",
                ),
            )
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
                    name="approve_pending_order",
                    description=(
                        "Approve and submit a pending order when the user "
                        "explicitly says to approve it (optionally by "
                        "intent_id). Only the user's explicit approval "
                        "triggers this; submission still passes all broker "
                        "and analyzer/live gates."
                    ),
                    input_model=ApprovePendingOrderInput,
                    handler=lambda value: sandbox.approve_from_chat(
                        actor="chat_user",
                        intent_id=ApprovePendingOrderInput.model_validate(
                            value.model_dump()
                        ).intent_id,
                    ),
                    side_effects=(
                        "approves an order and submits it to the broker"
                    ),
                    required_role="approver",
                    retry_safe=False,
                    capabilities=ToolCapabilityMetadata(
                        actions=("approve",),
                        execution_modes=("paper", "live"),
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


def _dataset_for_request(
    db_path: Path,
    *,
    symbol: str | None = None,
    exchange: str | None = None,
    asset_class: str | None = None,
    interval: str | None = None,
    raise_on_missing: bool = True,
) -> str | None:
    con = connect(db_path)
    try:
        filters = ["quality_status NOT IN ('rejected', 'empty')"]
        values: list[Any] = []
        if symbol:
            filters.append("UPPER(symbol) = UPPER(?)")
            values.append(symbol)
        if exchange:
            filters.append("UPPER(exchange) = UPPER(?)")
            values.append(exchange)
        if asset_class:
            filters.append("data_type = ?")
            values.append(f"{asset_class.lower()}_ohlcv")
        if interval:
            filters.append("interval = ?")
            values.append(interval)
        row = con.execute(
            "SELECT dataset_id FROM data_catalog WHERE "
            + " AND ".join(filters)
            + " ORDER BY updated_at DESC LIMIT 1",
            values,
        ).fetchone()
    finally:
        con.close()
    if row is None and symbol and raise_on_missing:
        scope = " ".join(
            value
            for value in (symbol.upper(), (exchange or "").upper(), interval or "")
            if value
        )
        raise ValueError(
            f"No stored data matches {scope}."
        )
    return row[0] if row else None


def _latest_dataset_id(db_path: Path) -> str | None:
    """Compatibility helper for callers that intentionally omit a symbol."""
    return _dataset_for_request(db_path)




