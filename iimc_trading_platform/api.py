from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .api_models import (
    ApprovalDecisionRequest,
    AlertAcknowledgementRequest,
    AiEvaluationRequest,
    ChatRequest,
    ChatResponse,
    CreatePortfolioRequest,
    CustomStrategyBacktestRequest,
    DashboardPreferencesRequest,
    LoginRequest,
    LocalOhlcvDatasetInput,
    PortfolioControlRequest,
    PortfolioFillRequest,
    PortfolioRiskCheckRequest,
    RetentionExecuteRequest,
    RetentionPreviewRequest,
    ResearchBriefRequest,
    RobustnessExperimentRequest,
    RunComparisonRequest,
    SandboxActionRequest,
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
    operational_summary,
    PersonaService,
    PlatformDashboardService,
    Principal,
    PortfolioService,
    production_readiness,
    register_default_jobs,
    ResearchService,
    RobustnessService,
    ToolExecutionService,
    foundation_health,
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
    CreateCustomStrategySpecInput,
    DatasetDetailInput,
    DatasetFreshnessInput,
    InstrumentSearchInput,
    KnowledgeSearchInput,
    ListCustomStrategySpecsInput,
    OpenAlgoSnapshotInput,
    OptionSymbolInput,
    PrepareSandboxIntentInput,
    RunBacktestInput,
    RunCustomStrategySpecInput,
    RunIdInput,
    SymbolValidationInput,
    ToolRegistry,
    build_default_tool_registry,
)


def create_app(config: AppConfig | None = None) -> FastAPI:
    active_config = config or load_config()
    configure_logging(active_config.log_level)
    logger = logging.getLogger(__name__)

    initialize_database(active_config.database_path)
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
            require_real_llm=active_config.require_real_llm,
        ),
        conversation_service,
        ResponseEvaluator(),
    )
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
    openalgo_readiness_service = OpenAlgoReadinessService(active_config)
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
    research_service = ResearchService(
        active_config.database_path,
        capability_coverage_service,
        market_news_service,
        execution_readiness_service,
    )
    register_default_jobs(
        job_service,
        include_openalgo=bool(active_config.openalgo_api_key),
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
            allow_methods=["GET", "POST"],
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
        StaticFiles(directory=frontend_dir),
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

    @app.get("/operations/summary")
    def operations_summary(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return operational_summary(active_config)

    @app.get("/operations/backups")
    def backups(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return backup_service.list()

    @app.post("/operations/backups")
    def create_backup(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return backup_service.create(created_by=principal.username)

    @app.get("/operations/backups/{backup_id}/verify")
    def verify_backup(
        backup_id: str,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return backup_service.verify(
            backup_id,
            verified_by=principal.username,
        )

    @app.get("/evaluations")
    def evaluations(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return ai_evaluation_service.list(limit)

    @app.post("/evaluations/run")
    def run_evaluation(
        request: AiEvaluationRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        configured_api_key = (
            active_config.groq_api_key
            if active_config.llm_provider == "groq"
            else active_config.openai_api_key
        )
        configured_model = (
            active_config.groq_model
            if active_config.llm_provider == "groq"
            else active_config.openai_model
        )
        if request.mode == "configured" and not configured_api_key:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{active_config.llm_provider.upper()} API key is "
                    "required for configured evaluation"
                ),
            )
        orchestrator = build_orchestrator(
            api_key=configured_api_key if request.mode == "configured" else None,
            model=active_config.openai_model,
            provider=active_config.llm_provider,
            groq_api_key=(
                active_config.groq_api_key
                if request.mode == "configured"
                else None
            ),
            groq_model=active_config.groq_model,
            require_real_llm=(request.mode == "configured"),
        )
        return ai_evaluation_service.run(
            orchestrator=orchestrator,
            model=configured_model if request.mode == "configured" else None,
            created_by=principal.username,
        )

    @app.get("/evaluations/retrieval")
    def retrieval_evaluations(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return retrieval_evaluation_service.list(limit)

    @app.post("/evaluations/retrieval/run")
    def run_retrieval_evaluation(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return retrieval_evaluation_service.run(
            created_by=principal.username,
        )

    @app.get("/operations/retention")
    def retention(
        limit: int = 50,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return {
            **retention_service.policies(),
            **retention_service.recent_runs(limit),
        }

    @app.post("/operations/retention/preview")
    def preview_retention(
        request: RetentionPreviewRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return retention_service.preview(
            actor=principal.username,
            policy_names=request.policy_names,
        )

    @app.post("/operations/retention/execute")
    def execute_retention(
        request: RetentionExecuteRequest,
        principal: Principal = Depends(admin),
    ) -> dict[str, Any]:
        return retention_service.execute(
            actor=principal.username,
            confirmation=request.confirmation,
            policy_names=request.policy_names,
        )

    @app.get("/operations/alerts")
    def alerts(
        include_resolved: bool = False,
        limit: int = 100,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 500",
            )
        return alert_service.list(
            include_resolved=include_resolved,
            limit=limit,
        )

    @app.post("/operations/alerts/evaluate")
    def evaluate_alerts(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return alert_service.evaluate(actor=principal.username)

    @app.post("/operations/alerts/{alert_id}/acknowledge")
    def acknowledge_alert(
        alert_id: str,
        request: AlertAcknowledgementRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return alert_service.acknowledge(
            alert_id,
            actor=principal.username,
            reason=request.reason,
        )

    @app.post("/operations/storage-plan")
    def storage_plan(
        principal: Principal = Depends(admin),
    ) -> dict[str, Any]:
        return storage_migration_service.generate()

    @app.post("/operations/storage-export-analytical")
    def storage_export_analytical(
        principal: Principal = Depends(admin),
    ) -> dict[str, Any]:
        return storage_migration_service.export_analytical_history()

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(
        principal: Principal = Depends(approver),
    ) -> str:
        summary = operational_summary(active_config)
        return "\n".join(
            [
                "# TYPE iimc_enabled_jobs gauge",
                f"iimc_enabled_jobs {summary['enabled_jobs']}",
                "# TYPE iimc_failed_job_runs counter",
                f"iimc_failed_job_runs {summary['failed_job_runs']}",
                "# TYPE iimc_stale_assessments gauge",
                (
                    "iimc_stale_assessments "
                    f"{summary['stale_assessments']}"
                ),
                "# TYPE iimc_pending_approvals gauge",
                (
                    "iimc_pending_approvals "
                    f"{summary['pending_approvals']}"
                ),
                "# TYPE iimc_uncertain_submissions gauge",
                (
                    "iimc_uncertain_submissions "
                    f"{summary['uncertain_submissions']}"
                ),
                "# TYPE iimc_active_tasks gauge",
                f"iimc_active_tasks {summary['active_tasks']}",
                "# TYPE iimc_failed_tasks gauge",
                f"iimc_failed_tasks {summary['failed_tasks']}",
                "# TYPE iimc_active_critical_alerts gauge",
                (
                    "iimc_active_critical_alerts "
                    f"{summary['active_critical_alerts']}"
                ),
                "# TYPE iimc_active_warning_alerts gauge",
                (
                    "iimc_active_warning_alerts "
                    f"{summary['active_warning_alerts']}"
                ),
                "",
            ]
        )

    @app.get("/jobs")
    def jobs(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return job_service.list_jobs()

    @app.get("/jobs/runs")
    def job_runs(
        limit: int = 100,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 500",
            )
        return job_service.recent_runs(limit)

    @app.post("/jobs/{job_id}/run")
    def run_job(
        job_id: str,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return job_service.run_now(
            job_id,
            worker_id=f"api:{principal.username}",
        )

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

    @app.get("/health")
    def health() -> dict[str, Any]:
        return foundation_health(active_config)

    @app.get("/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready")
    def ready() -> dict[str, Any]:
        result = production_readiness(active_config)
        if result["status"] != "ready":
            raise HTTPException(status_code=503, detail=result)
        return result

    @app.get("/tools")
    def tools(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return {
            "orchestration_mode": chat_service.orchestrator.mode,
            "tools": tool_registry.list_tools(),
        }

    @app.get("/platform/summary")
    def platform_summary(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.summary()

    @app.get("/platform/dashboard")
    def platform_dashboard(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.summary()

    @app.get("/platform/dashboard/summary")
    def platform_dashboard_summary(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.summary()

    @app.get("/platform/dashboard/preferences")
    def dashboard_preferences(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return dashboard_preference_service.get(principal.user_id)

    @app.put("/platform/dashboard/preferences")
    def update_dashboard_preferences(
        request: DashboardPreferencesRequest,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return dashboard_preference_service.update(
                principal.user_id,
                widgets=request.widgets,
                auto_refresh=request.auto_refresh,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/platform/operator-review")
    def platform_operator_review(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.operator_review()

    @app.get("/platform/status")
    def platform_status(
        symbol: str = "NIFTY",
        exchange: str = "NFO",
        asset_class: str = "options",
        interval: str = "5m",
        start_date: str = "2026-04-23",
        end_date: str = "2026-05-23",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return capability_coverage_service.platform_status(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

    @app.get("/platform/execution/readiness")
    def platform_execution_readiness(
        symbol: str = "NIFTY",
        exchange: str = "NFO",
        asset_class: str = "options",
        interval: str = "5m",
        start_date: str = "2026-04-23",
        end_date: str = "2026-05-23",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execution_readiness_service.readiness(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

    @app.get("/platform/symbol/readiness")
    def platform_symbol_readiness(
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return capability_coverage_service.platform_status(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

    @app.get("/platform/research/context")
    def platform_research_context(
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return research_service.research_context(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

    @app.get("/platform/research/briefs")
    def platform_research_briefs(
        limit: int = 20,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 100",
            )
        return research_service.list_briefs(limit=limit)

    @app.post("/platform/research/briefs")
    def create_platform_research_brief(
        request: ResearchBriefRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return research_service.create_brief(
            **request.model_dump(),
            created_by=principal.username,
        )

    @app.get("/platform/openalgo/monitor")
    def platform_openalgo_monitor(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return openalgo_readiness_service.monitor()

    @app.get("/platform/instruments/search")
    def platform_instrument_search(
        query: str,
        exchange: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = InstrumentSearchInput(
                query=query,
                exchange=exchange,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return instrument_discovery_service.search(**payload.model_dump())

    @app.get("/platform/instruments/symbol")
    def platform_instrument_symbol(
        symbol: str,
        exchange: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = SymbolValidationInput(
                symbol=symbol,
                exchange=exchange,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return instrument_discovery_service.validate_symbol(
            **payload.model_dump()
        )

    @app.get("/platform/instruments/optionsymbol")
    def platform_option_symbol(
        underlying: str,
        exchange: str,
        expiry_date: str,
        option_type: str,
        offset: str = "ATM",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = OptionSymbolInput(
                underlying=underlying,
                exchange=exchange,
                expiry_date=expiry_date,
                offset=offset,
                option_type=option_type,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return instrument_discovery_service.resolve_option_symbol(
            **payload.model_dump()
        )

    @app.post("/platform/backtest/run", response_model=None)
    def platform_backtest_run(
        request: RunBacktestInput,
        response: Response,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            result = execute_tool(
                "run_backtest",
                request.model_dump(mode="json"),
            )
        except HTTPException as exc:
            response.status_code = int(exc.status_code)
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "ok": False,
                "status": "backtest_failed",
                "safe_failure": True,
                "message": (
                    detail.get("message")
                    if isinstance(detail, dict)
                    else str(exc.detail)
                ),
                "no_synthetic_fallback": True,
                "data_source": "real",
                "visible_in_openalgo": False,
                "detail": exc.detail,
            }
        return {
            "ok": True,
            "status": "completed",
            "safe_failure": False,
            "message": "IIMC historical backtest completed.",
            "no_synthetic_fallback": True,
            "data_source": "real",
            "visible_in_openalgo": False,
            **result,
        }

    @app.get("/datasets")
    def datasets(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return execute_tool("list_datasets", {})

    @app.post("/datasets/ohlcv")
    def import_local_ohlcv_dataset(
        request: LocalOhlcvDatasetInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            result = market_data_ingestion_service.import_ohlcv(
                **request.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        ).record(
            entity_type="dataset",
            entity_id=result["dataset_id"],
            action="local_ohlcv_imported",
            actor=principal.username,
            payload={
                "asset_class": result["asset_class"],
                "row_count": result["row_count"],
                "source_sha256": result["source_sha256"],
            },
        )
        return {**result, "audit_id": audit.audit_id}

    @app.get("/datasets/{dataset_id}")
    def dataset_detail(
        dataset_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = DatasetDetailInput(dataset_id=dataset_id).model_dump(
            mode="json"
        )
        return execute_tool("get_dataset_detail", payload)

    @app.get("/datasets/{dataset_id}/freshness")
    def dataset_freshness(
        dataset_id: str,
        purpose: str = "historical_research",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = DatasetFreshnessInput(
                dataset_id=dataset_id,
                purpose=purpose,
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return execute_tool("assess_dataset_freshness", payload)

    @app.get("/strategies")
    def strategies(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return execute_tool("list_strategies", {})

    @app.get("/custom-strategy-specs")
    def custom_strategy_specs(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = ListCustomStrategySpecsInput(limit=limit).model_dump(
            mode="json"
        )
        return execute_tool("list_custom_strategy_specs", payload)

    @app.post("/custom-strategy-specs")
    def create_custom_strategy_spec(
        request: CreateCustomStrategySpecInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        payload["created_by"] = principal.username
        return execute_tool("create_custom_strategy_spec", payload)

    @app.post("/custom-strategy-specs/{spec_id}/backtest")
    def run_custom_strategy_spec(
        spec_id: str,
        request: CustomStrategyBacktestRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = RunCustomStrategySpecInput(
            spec_id=spec_id,
            **request.model_dump(mode="json"),
        ).model_dump(mode="json")
        return execute_tool("run_custom_strategy_spec", payload)

    @app.get("/personas")
    def personas(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return persona_service.list()

    @app.get("/personas/{persona_id}")
    def persona_detail(
        persona_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return persona_service.get(persona_id)

    @app.get("/knowledge/documents")
    def knowledge_documents(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execute_tool("list_knowledge_documents", {})

    @app.post("/knowledge/search")
    def knowledge_search(
        request: KnowledgeSearchInput,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execute_tool(
            "search_knowledge",
            request.model_dump(mode="json"),
        )

    @app.get("/market-news/status")
    def market_news_status(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return market_news_service.status()

    @app.get("/market-news/latest")
    def market_news_latest(
        limit: int = 20,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return market_news_service.latest(limit)

    @app.post("/market-news/fetch", response_model=None)
    def market_news_fetch(
        response: Response,
        query: str | None = None,
        symbol: str | None = None,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        result = market_news_service.fetch(query=query, symbol=symbol)
        if not result.get("ok"):
            response.status_code = 409
        return result

    @app.get("/openalgo/snapshots")
    def openalgo_snapshots(
        limit: int = 100,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return openalgo_snapshot_service.list(limit)

    @app.get("/openalgo/{snapshot_type}")
    def openalgo_snapshot(
        snapshot_type: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if not active_config.openalgo_api_key:
            raise HTTPException(
                status_code=503,
                detail={
                    "ok": False,
                    "status": "credential_required",
                    "safe_failure": True,
                    "message": "OPENALGO_API_KEY is not configured",
                    "no_synthetic_fallback": True,
                },
            )
        try:
            payload = OpenAlgoSnapshotInput(
                snapshot_type=snapshot_type
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return execute_tool("get_openalgo_snapshot", payload)

    @app.post("/sandbox/intents")
    def prepare_sandbox_intent(
        request: PrepareSandboxIntentInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = request.model_dump()
        payload["requested_by"] = principal.username
        return sandbox_service.prepare_intent(**payload)

    @app.get("/sandbox/intents")
    def sandbox_intents(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return sandbox_service.list_intents(limit)

    @app.get("/sandbox/intents/{intent_id}")
    def sandbox_intent(
        intent_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return sandbox_service.get_intent(intent_id)

    @app.get("/approvals/pending")
    def pending_approvals(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return sandbox_service.list_pending_approvals()

    @app.post("/approvals/{approval_id}/decision")
    def decide_approval(
        approval_id: str,
        request: ApprovalDecisionRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return sandbox_service.decide(
            approval_id,
            approved=request.approved,
            decided_by=principal.username,
            reason=request.reason,
        )

    @app.post("/sandbox/intents/{intent_id}/submit")
    def submit_sandbox_intent(
        intent_id: str,
        request: SandboxActionRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return sandbox_service.submit(
            intent_id,
            actor=principal.username,
        )

    @app.post("/sandbox/intents/{intent_id}/reconcile")
    def reconcile_sandbox_intent(
        intent_id: str,
        request: SandboxActionRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return sandbox_service.reconcile(
            intent_id,
            actor=principal.username,
        )

    @app.post("/sandbox/intents/{intent_id}/cancel")
    def cancel_sandbox_intent(
        intent_id: str,
        request: SandboxActionRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return sandbox_service.cancel(
            intent_id,
            actor=principal.username,
        )

    @app.post("/backtests")
    def run_backtest(
        request: RunBacktestInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return execute_tool(
            "run_backtest",
            request.model_dump(mode="json"),
        )

    @app.get("/runs")
    def runs(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return {
            "runs": BacktestService(
                active_config.database_path
            ).list_runs(limit)
        }

    @app.post("/runs/compare")
    def compare_runs(
        request: RunComparisonRequest,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return evidence_service.compare_runs(request.run_ids)

    @app.post("/experiments/robustness")
    def run_robustness_experiment(
        request: RobustnessExperimentRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return robustness_service.run(
            **request.model_dump(),
            requested_by=principal.username,
        )

    @app.post("/experiments/robustness/submit")
    def submit_robustness_experiment(
        request: RobustnessExperimentRequest,
        background_tasks: BackgroundTasks,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        task = task_service.submit(
            task_type="robustness_experiment",
            payload={
                **request.model_dump(),
                "requested_by": principal.username,
            },
            requested_by=principal.username,
        )
        background_tasks.add_task(
            task_service.run_due,
            f"api:{principal.username}",
            1,
        )
        return task

    @app.get("/tasks")
    def tasks(
        limit: int = 100,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 500",
            )
        return task_service.list(limit)

    @app.get("/tasks/{task_id}")
    def task_detail(
        task_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return task_service.get(task_id)

    @app.post("/tasks/run-due")
    def run_due_tasks(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return {
            "tasks": task_service.run_due(
                f"api:{principal.username}",
                limit=5,
            )
        }

    @app.get("/experiments/robustness")
    def list_robustness_experiments(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return robustness_service.list(limit)

    @app.get("/experiments/robustness/{experiment_id}")
    def robustness_experiment(
        experiment_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return robustness_service.get(experiment_id)

    @app.post("/experiments/robustness/{experiment_id}/reports")
    def create_robustness_report(
        experiment_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return evidence_service.create_robustness_report(
            experiment_id,
            created_by=principal.username,
        )

    @app.get("/runs/{run_id}")
    def run_detail(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_backtest_result", payload)

    @app.get("/runs/{run_id}/performance")
    def run_performance(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_performance", payload)

    @app.get("/runs/{run_id}/risk")
    def run_risk(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_risk_decisions", payload)

    @app.get("/runs/{run_id}/orders")
    def run_orders(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_order_timeline", payload)

    @app.get("/runs/{run_id}/timeline")
    def run_timeline(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return evidence_service.run_timeline(run_id)

    @app.post("/runs/{run_id}/reports")
    def create_run_report(
        run_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return evidence_service.create_run_report(
            run_id,
            created_by=principal.username,
        )

    @app.get("/reports")
    def reports(
        limit: int = 100,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 500",
            )
        return evidence_service.list_reports(limit)

    @app.get("/reports/{report_id}")
    def report_detail(
        report_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return evidence_service.get_report(report_id)

    @app.post("/portfolios")
    def create_portfolio(
        request: CreatePortfolioRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return portfolio_service.create(
            **request.model_dump(),
            created_by=principal.username,
        )

    @app.get("/portfolios")
    def portfolios(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return portfolio_service.list()

    @app.get("/portfolios/{portfolio_id}")
    def portfolio_snapshot(
        portfolio_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return portfolio_service.get(portfolio_id)

    @app.post("/portfolios/{portfolio_id}/risk-check")
    def portfolio_risk_check(
        portfolio_id: str,
        request: PortfolioRiskCheckRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return portfolio_service.evaluate_and_reserve(
            portfolio_id=portfolio_id,
            **request.model_dump(),
        )

    @app.post("/portfolios/{portfolio_id}/fills")
    def portfolio_fill(
        portfolio_id: str,
        request: PortfolioFillRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return portfolio_service.apply_fill(
            portfolio_id=portfolio_id,
            **request.model_dump(),
        )

    @app.post("/portfolio-risk/reservations/{reservation_id}/release")
    def release_portfolio_reservation(
        reservation_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return portfolio_service.release_reservation(reservation_id)

    @app.post("/portfolios/{portfolio_id}/trading-control")
    def set_portfolio_trading_control(
        portfolio_id: str,
        request: PortfolioControlRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return portfolio_service.set_trading_enabled(
            portfolio_id=portfolio_id,
            enabled=request.enabled,
            reason=request.reason,
            changed_by=principal.username,
        )

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

    app.state.telemetry = configure_telemetry(active_config, app)
    return app
