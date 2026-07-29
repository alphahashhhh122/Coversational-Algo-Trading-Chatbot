from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .api_models import (
    ChatRequest,
    ChatResponse,
    LoginRequest,
)
from .config import AppConfig, load_config
from .evaluator import ResponseEvaluator
from .infrastructure import (
    DuckDBAuditRepository,
    DuckDBToolCallRepository,
    OpenAlgoAuthenticationError,
    OpenAlgoClient,
    OpenAlgoError,
    OpenAlgoUnavailableError,
    initialize_database,
)
from .observability import configure_logging
from .progress import reporting_to
from .api_routes import (
    agent_platform,
    execution_routes,
    knowledge_routes,
    market_data_routes,
    operations_routes,
    platform_routes,
    research_routes,
)
from .telemetry import configure_telemetry
from .middleware import (
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    RequestContextMiddleware,
)
from .orchestration import build_orchestrator
from .services import (
    AuditService,
    AuthService,
    BackupService,
    CapabilityCoverageService,
    DashboardPreferenceService,
    build_job_service,
    build_task_service,
    EvidenceService,
    ExecutionReadinessService,
    InstrumentDiscoveryService,
    MarketNewsService,
    MarketDataIngestionService,
    OpenAlgoReadinessService,
    OpenAlgoHistoryImportService,
    PersonaService,
    PlatformDashboardService,
    Principal,
    PortfolioService,
    register_default_jobs,
    ResearchService,
    RobustnessService,
    ToolExecutionService,
)
from .services.chat_service import ChatService
from .services.ai_evaluation_service import AiEvaluationService
from .services.alert_service import AlertService
from .services.retrieval_evaluation_service import (
    RetrievalEvaluationService,
)
from .services.retention_service import RetentionService
from .services.storage_migration_service import StorageMigrationService
from .services.openalgo_service import OpenAlgoSnapshotService
from .services.backtest_service import BacktestService
from .services.conversation_service import ConversationService
from .services.sandbox_execution_service import SandboxExecutionService
from .services.tool_execution_service import ToolExecutionError
from .tools.registry import (
    build_default_tool_registry,
)



# How long a stream may stay silent before sending a keep-alive comment.
_SSE_HEARTBEAT_SECONDS = 15.0




def _evidence_dataset_id(evidence: list[dict[str, Any]]) -> str | None:
    """The dataset a scored run was measured on, when it names one."""
    for item in evidence:
        dataset_id = item.get("dataset_id")
        if dataset_id:
            return str(dataset_id)
    return None


class _RevalidatingStaticFiles(StaticFiles):
    """Static assets the browser must re-check before reusing.

    ``index.html`` cache-busts its entry points with ``?v=``, but an ES module's
    ``import`` specifiers carry no version — so editing ``modules/agents.js``
    used to leave browsers running the previous copy with no way to notice, and
    the only symptom was a fix that appeared not to have worked.

    ``no-cache`` does not mean "don't store"; it means "revalidate first". The
    browser still keeps the file and still gets a 304 when nothing changed, so
    the cost is one conditional request per asset — irrelevant for a locally
    served research tool, and it removes a whole class of stale-asset
    confusion.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _agent_run_events(
    agent: Any,
    task: Any,
    run: Callable[[Any, Any], dict[str, Any]],
    *,
    heartbeat_seconds: float = _SSE_HEARTBEAT_SECONDS,
) -> Iterator[str]:
    """Narrate one agent run as Server-Sent Events.

    The run happens on a worker thread while this generator drains its progress
    queue, because the work is synchronous and would otherwise produce nothing
    until it finished — which is the silence being fixed.

    Chosen over WebSockets deliberately: progress is one-directional, SSE works
    with the existing sync handlers, browsers reconnect on their own, and it
    adds no dependency.
    """

    events: queue.Queue[dict[str, Any] | None] = queue.Queue()
    outcome: dict[str, Any] = {}

    def work() -> None:
        # The sink is installed *inside* the worker, so this stream only ever
        # sees progress from its own run.
        try:
            with reporting_to(events.put):
                outcome["payload"] = run(agent, task)
        except Exception as exc:  # noqa: BLE001 - reported to the client
            outcome["error"] = str(exc)[:300]
        finally:
            events.put(None)  # sentinel: the work is over, one way or another

    worker = threading.Thread(target=work, daemon=True, name="agent-run-stream")
    worker.start()
    yield _sse(
        "started",
        {"agent": getattr(agent, "name", None), "symbol": getattr(task, "symbol", None)},
    )
    while True:
        try:
            event = events.get(timeout=heartbeat_seconds)
        except queue.Empty:
            # An SSE comment. Keeps intermediaries from closing an idle
            # connection during a long, quiet step.
            yield ": keep-alive\n\n"
            continue
        if event is None:
            break
        yield _sse("progress", event)
    worker.join(timeout=1.0)
    if "error" in outcome:
        yield _sse("failed", {"error": outcome["error"]})
    else:
        yield _sse("result", outcome.get("payload", {}))


def create_app(config: AppConfig | None = None) -> FastAPI:
    active_config = config or load_config()
    configure_logging(active_config.log_level)
    logger = logging.getLogger(__name__)

    initialize_database(active_config.database_path)

    # A backtest only exists inside the process running it, so anything still
    # marked "running" belongs to a process that is gone. Close those out now
    # rather than letting the UI claim they are in progress forever.
    _stranded = BacktestService(
        active_config.database_path, allow_live_trading=False
    ).reconcile_interrupted_runs()["interrupted"]
    if _stranded:
        logger.info(
            "Closed out runs left behind by a previous process",
            extra={"event": "runs_reconciled", "run_count": len(_stranded)},
        )

    if active_config.auth_required and not active_config.auth_secret:
        raise RuntimeError(
            "IIMC_AUTH_SECRET is required when authentication is enabled"
        )
    auth_service = (
        AuthService(
            active_config.database_path,
            secret=active_config.auth_secret,
            session_ttl_minutes=active_config.session_ttl_minutes,
        )
        if active_config.auth_secret
        else None
    )
    tool_registry = build_default_tool_registry(
        active_config.database_path,
        allow_live_trading=active_config.allow_live_trading,
        openalgo_base_url=active_config.openalgo_base_url,
        openalgo_api_key=active_config.openalgo_api_key,
        artifacts_dir=active_config.artifacts_dir,
        app_config=active_config,
    )
    tool_execution_service = ToolExecutionService(
        DuckDBToolCallRepository(active_config.database_path),
        AuditService(DuckDBAuditRepository(active_config.database_path)),
    )
    conversation_service = ConversationService(active_config.database_path)
    chat_service = ChatService(
        tool_registry,
        tool_execution_service,
        build_orchestrator(
            api_key=active_config.openai_api_key,
            model=active_config.openai_model,
            provider=active_config.llm_provider,
            groq_api_key=active_config.groq_api_key,
            groq_model=active_config.groq_model,
            groq_fallback_model=active_config.groq_fallback_model,
            require_real_llm=active_config.require_real_llm,
        ),
        conversation_service,
        ResponseEvaluator(),
    )
    # --- ATL agent kernel: founding roster + registry --------------------
    from .agents.roster import build_founding_roster
    from .agents.base import AgentTask as _AgentTask
    from .services.agent_registry_service import AgentRegistryService

    from .services.agent_evaluation_service import AgentEvaluationService

    agent_registry = AgentRegistryService(active_config.database_path)
    agent_evaluation = AgentEvaluationService(active_config.database_path)

    from .services.arena_service import ArenaService
    from .services.backtest_service import BacktestService as _ArenaBacktests
    from .tools.registry import _dataset_for_request as _resolve_dataset

    arena_service = ArenaService(
        active_config.database_path,
        _ArenaBacktests(
            active_config.database_path,
            strategy_plugin_dir=active_config.strategy_plugin_dir,
            allow_live_trading=False,  # the arena never trades, by construction
        ),
    )

    from .services.authored_agent_service import AuthoredAgentService
    from .services.committee_service import CommitteeService
    from .services.custom_strategy_service import (
        CustomStrategyService as _AuthoringSpecs,
    )
    from .services.strategy_optimizer_service import (
        StrategyOptimizerService as _AuthoringOptimizer,
    )

    def _authoring_dataset(symbol: str | None, exchange: str) -> str | None:
        if not symbol:
            return None
        return _resolve_dataset(
            active_config.database_path,
            symbol=symbol,
            exchange=exchange,
            raise_on_missing=False,
        )

    authored_agents = AuthoredAgentService(
        active_config.database_path,
        _AuthoringSpecs(active_config.database_path),
        _AuthoringOptimizer(
            _ArenaBacktests(
                active_config.database_path,
                strategy_plugin_dir=active_config.strategy_plugin_dir,
                allow_live_trading=False,
            )
        ),
        _authoring_dataset,
    )

    def _committee_member_runner(
        member: str, symbol: str, exchange: str
    ) -> dict[str, Any]:
        agent = _agents_by_key.get(member)
        if agent is None:
            raise ValueError(f"unknown committee member {member!r}")
        return agent.run(
            _AgentTask(task_type="committee", symbol=symbol, exchange=exchange)
        ).findings

    committee = CommitteeService(_committee_member_runner)

    from .services.contest_service import ContestService
    from .services.freshness_service import FreshnessService
    from .services.data_health_service import DataHealthService
    from .services.supervisor_service import SupervisorService
    from .services.universe_backfill_service import UniverseBackfillService

    data_health_service = DataHealthService(active_config.database_path)

    def _supervisor_run_agent(name: str, symbol: str) -> dict[str, Any]:
        agent = _agents_by_key.get(name)
        if agent is None:
            raise ValueError(f"unknown agent {name!r}")
        task = _AgentTask(task_type="scheduled", symbol=symbol)
        result = agent.run(task)
        run_id = agent_registry.record_run(agent, task, result)
        card = agent_evaluation.score_run(
            {
                "status": result.status,
                "findings": result.findings,
                "evidence": result.evidence,
            },
            agent.category,
        )
        agent_evaluation.record_score(
            agent_id=agent.agent_id,
            version=agent.version,
            run_id=run_id,
            scorecard=card,
        )
        return {"status": result.status, "run_id": run_id}

    def _enqueue_dataset_refresh(dataset_id: str) -> None:
        """The supervisor's single permitted action.

        Queues a data refresh through the existing job system. Fetching market
        data is the one corrective step that carries no financial consequence,
        so it needs no human in the loop; everything else stays a flag.
        """
        job_service.register(
            name=f"refresh_{dataset_id}"[:80],
            job_type="freshness_sweep",
            schedule_seconds=3600,
        )

    def _supervisor_candles(symbol: str) -> list[dict[str, Any]]:
        """Stored history for the swept symbol, or nothing.

        Returning an empty list when there is no data keeps the regime check
        silent rather than guessing at a regime it cannot observe.
        """
        dataset_id = _resolve_dataset(
            active_config.database_path, symbol=symbol, exchange="NSE",
            raise_on_missing=False,
        )
        if not dataset_id:
            return []
        _ds, candles = _ArenaBacktests(
            active_config.database_path,
            strategy_plugin_dir=active_config.strategy_plugin_dir,
            allow_live_trading=False,
        ).load_dataset_candles(dataset_id)
        return candles

    supervisor_service = SupervisorService(
        active_config.database_path,
        run_agent=_supervisor_run_agent,
        freshness=FreshnessService(active_config.database_path),
        enqueue_refresh=_enqueue_dataset_refresh,
        load_candles=_supervisor_candles,
    )

    from .services.daily_digest_service import DailyDigestService

    digest_service = DailyDigestService(
        active_config.database_path,
        evaluation=agent_evaluation,
        supervisor=supervisor_service,
        data_health=data_health_service,
        committee=lambda symbol: committee.run(symbol),
    )

    contest_service = ContestService(
        active_config.database_path,
        _ArenaBacktests(
            active_config.database_path,
            strategy_plugin_dir=active_config.strategy_plugin_dir,
            allow_live_trading=False,
        ),
    )

    def _arena_datasets_for(season_id: str) -> dict[str, str | None]:
        """One dataset per season symbol; None where nothing is stored.

        A None is passed through so the tick records that leg as missing
        rather than inventing an equity curve for it.
        """
        seasons = arena_service.list_seasons()["seasons"]
        season = next(
            (s for s in seasons if s["season_id"] == season_id), None
        )
        if season is None:
            return {}
        return {
            symbol: _resolve_dataset(
                active_config.database_path,
                symbol=symbol,
                exchange=season["exchange"],
                raise_on_missing=False,
            )
            for symbol in season.get("symbols") or [season["symbol"]]
        }

    def _arena_dataset_for(season_id: str) -> str | None:
        """The stored dataset a season's symbol resolves to, or None.

        None is passed straight through to the tick, which records the day as
        ``data_missing`` rather than inventing an equity curve.
        """
        seasons = arena_service.list_seasons()["seasons"]
        season = next((s for s in seasons if s["season_id"] == season_id), None)
        if season is None:
            return None
        return _resolve_dataset(
            active_config.database_path,
            symbol=season["symbol"],
            exchange=season["exchange"],
            raise_on_missing=False,
        )
    def _committee_for_roster(symbol: str, exchange: str) -> dict[str, Any]:
        # Late-bound: the committee is constructed below, and its members never
        # include the committee itself, so registering it as an agent cannot
        # recurse.
        return committee.run(symbol, exchange)

    _agent_roster = build_founding_roster(
        tool_registry,
        chat_runner=lambda message: {
            "answer": chat_service.answer(message).answer,
        },
        committee_runner=_committee_for_roster,
    )
    agent_registry.sync_roster(_agent_roster)
    _agents_by_key: dict[str, Any] = {}
    for _agent in _agent_roster:
        _agents_by_key[_agent.agent_id] = _agent
        _agents_by_key[_agent.name] = _agent
    openalgo_readiness_service = OpenAlgoReadinessService(active_config)
    sandbox_service = SandboxExecutionService(
        active_config.database_path,
        AuditService(
            DuckDBAuditRepository(active_config.database_path)
        ),
        (
            OpenAlgoClient(
                active_config.openalgo_base_url,
                active_config.openalgo_api_key,
            )
            if active_config.openalgo_api_key
            else None
        ),
        require_approval=active_config.require_paper_approval,
        allow_live_trading=active_config.allow_live_trading,
        provider_readiness=openalgo_readiness_service.monitor,
        max_signal_age_minutes=active_config.paper_signal_max_age_minutes,
    )
    from .services.risk_service import RiskService as _DirectRiskService

    direct_risk_service = _DirectRiskService(
        active_config.database_path,
        allow_live_trading=active_config.allow_live_trading,
    )
    evidence_service = EvidenceService(
        active_config.database_path,
        active_config.artifacts_dir,
    )
    robustness_service = RobustnessService(active_config.database_path)
    portfolio_service = PortfolioService(active_config.database_path)
    job_service = build_job_service(active_config)
    task_service = build_task_service(active_config.database_path)
    backup_service = BackupService(
        active_config.database_path,
        active_config.artifacts_dir / "backups",
    )
    ai_evaluation_service = AiEvaluationService(
        active_config.database_path,
        active_config.artifacts_dir,
        Path(__file__).parent / "evals" / "ai_eval_cases.jsonl",
    )
    retrieval_evaluation_service = RetrievalEvaluationService(
        active_config.database_path,
        active_config.artifacts_dir,
        Path(__file__).parent / "evals" / "retrieval_eval_cases.jsonl",
    )
    retention_service = RetentionService(active_config.database_path)
    alert_service = AlertService(
        active_config.database_path,
        active_config.artifacts_dir,
    )
    storage_migration_service = StorageMigrationService(
        active_config.database_path,
        active_config.artifacts_dir,
    )
    openalgo_snapshot_service = OpenAlgoSnapshotService(
        active_config.database_path,
        (
            OpenAlgoClient(
                active_config.openalgo_base_url,
                active_config.openalgo_api_key,
            )
            if active_config.openalgo_api_key
            else None
        ),
    )
    platform_dashboard_service = PlatformDashboardService(active_config)
    persona_service = PersonaService(active_config.database_path)
    dashboard_preference_service = DashboardPreferenceService(
        active_config.database_path
    )
    market_data_ingestion_service = MarketDataIngestionService(
        active_config.database_path
    )
    openalgo_history_import_service = OpenAlgoHistoryImportService(
        active_config
    )
    # Backfill needs the history importer, so it is built after it.
    universe_backfill = UniverseBackfillService(
        active_config.database_path, openalgo_history_import_service
    )
    capability_coverage_service = CapabilityCoverageService(
        active_config.database_path,
        openalgo_readiness_service,
    )
    execution_readiness_service = ExecutionReadinessService(
        active_config,
        capability_coverage_service,
        openalgo_readiness_service,
    )
    market_news_service = MarketNewsService(active_config)
    instrument_discovery_service = InstrumentDiscoveryService(active_config)
    from .services.screener_service import ScreenerService
    from .services.watch_service import WatchService

    watch_service = WatchService(
        active_config.database_path,
        ScreenerService(
            active_config.database_path,
            (
                OpenAlgoClient(
                    active_config.openalgo_base_url,
                    active_config.openalgo_api_key,
                )
                if active_config.openalgo_api_key
                else None
            ),
        ),
    )
    research_service = ResearchService(
        active_config.database_path,
        capability_coverage_service,
        market_news_service,
        execution_readiness_service,
    )
    register_default_jobs(
        job_service,
        include_openalgo=bool(active_config.openalgo_api_key),
        include_market_news=bool(
            active_config.market_news_provider
            and active_config.market_news_api_url
        ),
    )

    app = FastAPI(
        title=active_config.app_name,
        version="0.2.0",
        description="Audited conversational trading research platform API.",
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=active_config.max_request_bytes,
    )
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=active_config.rate_limit_per_minute,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(active_config.allowed_hosts),
    )
    if active_config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(active_config.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID", "X-Trace-ID"],
        )
    frontend_dir = Path(__file__).parent / "frontend"
    app.mount(
        "/static",
        _RevalidatingStaticFiles(directory=frontend_dir),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    def workspace() -> FileResponse:
        return FileResponse(frontend_dir / "index.html")

    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc: ValueError):
        from fastapi.responses import JSONResponse

        status_code = (
            404 if "not found" in str(exc).lower() else 409
        )
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": {
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                }
            },
        )

    @app.exception_handler(OpenAlgoError)
    async def openalgo_error_handler(request, exc: OpenAlgoError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=(
                503
                if isinstance(exc, OpenAlgoUnavailableError)
                else 502
            ),
            content={
                "detail": {
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                }
            },
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(request, exc: PermissionError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                }
            },
        )

    def current_principal(
        authorization: str | None = Header(default=None),
    ) -> Principal:
        if not active_config.auth_required:
            return Principal(
                user_id="local_development_user",
                username="local_development",
                role="admin",
                authenticated=False,
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Bearer authentication is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            return auth_service.authenticate(
                authorization.removeprefix("Bearer ").strip()
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=401,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    def viewer(
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        AuthService.require_role(principal, "viewer")
        return principal

    def researcher(
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        AuthService.require_role(principal, "researcher")
        return principal

    def approver(
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        AuthService.require_role(principal, "approver")
        return principal

    def admin(
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        AuthService.require_role(principal, "admin")
        return principal

    @app.post("/auth/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        if auth_service is None:
            raise HTTPException(
                status_code=503,
                detail="Authentication service is not configured",
            )
        try:
            result = auth_service.login(
                request.username,
                request.password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {
            **result,
            "user": asdict(result["user"]),
        }

    @app.get("/auth/me")
    def me(
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        return asdict(principal)

    @app.post("/auth/logout")
    def logout(
        authorization: str | None = Header(default=None),
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        if (
            auth_service is not None
            and authorization
            and authorization.startswith("Bearer ")
        ):
            auth_service.logout(
                authorization.removeprefix("Bearer ").strip()
            )
        return {"status": "logged_out", "user_id": principal.user_id}





















    def execute_tool(
        tool_name: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            tool_call_id, result = tool_execution_service.execute(
                tool_name=tool_name,
                request=payload,
                handler=lambda: tool_registry.call(tool_name, payload),
                session_id=session_id,
            )
        except ToolExecutionError as exc:
            cause = exc.cause
            if isinstance(cause, OpenAlgoUnavailableError):
                status_code = 503
            elif isinstance(cause, OpenAlgoAuthenticationError):
                status_code = 502
            elif (
                isinstance(cause, ValueError)
                and "not found" in str(cause).lower()
            ):
                status_code = 404
            else:
                status_code = 400
            detail = {
                "message": str(cause),
                "error_type": type(cause).__name__,
                "tool_call_id": exc.tool_call_id,
            }
            if tool_name == "run_backtest":
                detail.update(
                    {
                        "no_synthetic_fallback": True,
                        "synthetic_result_created": False,
                    }
                )
            raise HTTPException(
                status_code=status_code,
                detail=detail,
            ) from exc
        return {"tool_call_id": tool_call_id, **result}






















    agent_platform.register(
        app,
        agents_by_key=_agents_by_key,
        agent_registry=agent_registry,
        agent_evaluation=agent_evaluation,
        authored_agents=authored_agents,
        committee=committee,
        supervisor_service=supervisor_service,
        digest_service=digest_service,
        contest_service=contest_service,
        arena_service=arena_service,
        arena_datasets_for=_arena_datasets_for,
        active_config=active_config,
        viewer=viewer,
        researcher=researcher,
        agent_run_events=_agent_run_events,
        evidence_dataset_id=_evidence_dataset_id,
    )



















































    @app.get("/personas")
    def personas(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return persona_service.list()

    @app.get("/personas/{persona_id}")
    def persona_detail(
        persona_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return persona_service.get(persona_id)




















































    @app.get("/sessions/{session_id}/messages")
    def session_messages(
        session_id: str,
        limit: int = 20,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return {
            "session_id": session_id,
            "messages": conversation_service.history(session_id, limit),
        }



    @app.post("/chat", response_model=ChatResponse)
    def chat(
        request: ChatRequest,
        background_tasks: BackgroundTasks,
        principal: Principal = Depends(viewer),
    ) -> ChatResponse:
        try:
            result = chat_service.answer(
                request.message,
                request.session_id,
                allowed_tool_names=tool_registry.allowed_for_role(
                    principal.role
                ),
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        except Exception as exc:
            logger.exception(
                "Chat request failed safely",
                extra={
                    "event": "chat_request_failed",
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": (
                        "Chat orchestration failed before a trading action "
                        "could be completed."
                    ),
                    "error_type": type(exc).__name__,
                    "cause": str(exc)[:500],
                    "safe_failure": True,
                    "order_submitted": False,
                },
            ) from exc
        logger.info(
            "Chat request handled",
            extra={
                "event": "chat_request_handled",
                "intent": result.intent,
                "session_id": result.session_id,
                "tool_call_count": len(result.tool_calls),
            },
        )
        if (
            result.intent == "run_robustness_experiment"
            and result.data.get("task_id")
        ):
            background_tasks.add_task(
                task_service.run_due,
                f"chat:{principal.username}",
                1,
            )
        return ChatResponse(**asdict(result))

    # Route groups. Registered last, once every service object above
    # exists — each register() names the ones its handlers need.
    platform_routes.register(
        app,
        capability_coverage_service=capability_coverage_service,
        dashboard_preference_service=dashboard_preference_service,
        execute_tool=execute_tool,
        execution_readiness_service=execution_readiness_service,
        instrument_discovery_service=instrument_discovery_service,
        openalgo_readiness_service=openalgo_readiness_service,
        platform_dashboard_service=platform_dashboard_service,
        research_service=research_service,
        researcher=researcher,
        viewer=viewer,
    )
    market_data_routes.register(
        app,
        active_config=active_config,
        data_health_service=data_health_service,
        execute_tool=execute_tool,
        market_data_ingestion_service=market_data_ingestion_service,
        market_news_service=market_news_service,
        openalgo_history_import_service=openalgo_history_import_service,
        researcher=researcher,
        universe_backfill=universe_backfill,
        viewer=viewer,
    )
    execution_routes.register(
        app,
        active_config=active_config,
        approver=approver,
        direct_risk_service=direct_risk_service,
        execute_tool=execute_tool,
        openalgo_snapshot_service=openalgo_snapshot_service,
        portfolio_service=portfolio_service,
        researcher=researcher,
        sandbox_service=sandbox_service,
        viewer=viewer,
        watch_service=watch_service,
    )
    research_routes.register(
        app,
        active_config=active_config,
        ai_evaluation_service=ai_evaluation_service,
        approver=approver,
        evidence_service=evidence_service,
        execute_tool=execute_tool,
        researcher=researcher,
        retrieval_evaluation_service=retrieval_evaluation_service,
        robustness_service=robustness_service,
        task_service=task_service,
        viewer=viewer,
    )
    operations_routes.register(
        app,
        active_config=active_config,
        admin=admin,
        alert_service=alert_service,
        approver=approver,
        backup_service=backup_service,
        chat_service=chat_service,
        job_service=job_service,
        retention_service=retention_service,
        storage_migration_service=storage_migration_service,
        task_service=task_service,
        tool_registry=tool_registry,
        viewer=viewer,
    )
    knowledge_routes.register(
        app,
        active_config=active_config,
        execute_tool=execute_tool,
        researcher=researcher,
        tool_registry=tool_registry,
        viewer=viewer,
    )

    app.state.telemetry = configure_telemetry(active_config, app)
    return app
