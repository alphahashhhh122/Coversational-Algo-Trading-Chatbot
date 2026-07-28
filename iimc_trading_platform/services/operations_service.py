from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db import connect
from ..infrastructure.openalgo import OpenAlgoClient
from .backup_service import BackupService
from .alert_service import AlertService
from .freshness_service import FreshnessService
from .job_service import JobService
from .knowledge_service import KnowledgeService
from .market_news_service import MarketNewsService
from .openalgo_service import OpenAlgoSnapshotService
from .robustness_service import RobustnessService
from .retrieval_evaluation_service import RetrievalEvaluationService
from .retention_service import RetentionService
from .task_service import TaskService


CURATED_DOCUMENTS = [
    Path("README.md"),
    Path("docs/ARCHITECTURE.md"),
    Path("docs/DATA_DOMAINS.md"),
    Path("docs/SECURITY_AND_SECRETS.md"),
    Path("docs/OPENALGO_SANDBOX_BRIDGE.md"),
    Path("docs/OPERATOR_RUNBOOK.md"),
    Path("docs/OPERATIONS_FAILURE_RUNBOOK.md"),
    Path("docs/PRODUCTION_READINESS.md"),
]


def build_job_service(config: AppConfig) -> JobService:
    freshness = FreshnessService(config.database_path)
    # Handlers are defined before the JobService exists, but only *run*
    # afterwards, so the one handler that needs to enqueue work reads it
    # from here at call time.
    _built: dict[str, Any] = {}
    knowledge = KnowledgeService(config.database_path)
    backups = BackupService(
        config.database_path,
        config.artifacts_dir / "backups",
    )
    retrieval_evaluations = RetrievalEvaluationService(
        config.database_path,
        config.artifacts_dir,
        Path(__file__).parent.parent
        / "evals"
        / "retrieval_eval_cases.jsonl",
    )
    retention = RetentionService(config.database_path)
    alerts = AlertService(config.database_path, config.artifacts_dir)

    def freshness_sweep(payload: dict[str, Any]) -> dict[str, Any]:
        con = connect(config.database_path)
        try:
            dataset_ids = [
                row[0]
                for row in con.execute(
                    "SELECT dataset_id FROM data_catalog ORDER BY dataset_id"
                ).fetchall()
            ]
        finally:
            con.close()
        assessments = []
        for dataset_id in dataset_ids:
            assessments.append(
                freshness.assess(dataset_id, "historical_research")
            )
            assessments.append(
                freshness.assess(dataset_id, "current_market")
            )
        return {
            "dataset_count": len(dataset_ids),
            "assessment_count": len(assessments),
            "stale_count": sum(
                1 for item in assessments if item["status"] == "stale"
            ),
        }

    def knowledge_sync(payload: dict[str, Any]) -> dict[str, Any]:
        indexed = 0
        for path in CURATED_DOCUMENTS:
            if path.exists():
                knowledge.index_text(
                    title=path.stem.replace("_", " "),
                    source_uri=str(path.resolve()),
                    text=path.read_text(encoding="utf-8"),
                    document_type=path.suffix.removeprefix(".") or "text",
                    metadata={"corpus": "curated_project_docs"},
                )
                indexed += 1
        return {"indexed_documents": indexed}

    def backup_restore_verification(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        available = [
            item
            for item in backups.list()["backups"]
            if item.get("status") == "available"
        ]
        if not available:
            return {
                "status": "no_backup",
                "verified": False,
            }
        return backups.verify(
            available[0]["backup_id"],
            verified_by="scheduled_job",
        )

    def governed_retrieval_evaluation(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        knowledge_sync({})
        return retrieval_evaluations.run(
            created_by="scheduled_job",
        )

    def retention_preview(payload: dict[str, Any]) -> dict[str, Any]:
        return retention.preview(actor="scheduled_job")

    def alert_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
        return alerts.evaluate(actor="scheduled_job")

    handlers = {
        "freshness_sweep": freshness_sweep,
        "knowledge_sync": knowledge_sync,
        "backup_restore_verification": backup_restore_verification,
        "governed_retrieval_evaluation": (
            governed_retrieval_evaluation
        ),
        "retention_preview": retention_preview,
        "alert_evaluation": alert_evaluation,
    }
    if config.openalgo_api_key:
        snapshots = OpenAlgoSnapshotService(
            config.database_path,
            OpenAlgoClient(
                config.openalgo_base_url,
                config.openalgo_api_key,
            ),
        )

        def openalgo_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
            captured = []
            for snapshot_type in payload.get(
                "types",
                ["funds", "positionbook", "orderbook", "tradebook"],
            ):
                captured.append(snapshots.capture(snapshot_type))
            return {
                "snapshot_ids": [
                    item["snapshot_id"] for item in captured
                ]
            }

        handlers["openalgo_snapshot"] = openalgo_snapshot

        from .price_alert_service import PriceAlertService

        price_alerts = PriceAlertService(
            config.database_path,
            OpenAlgoClient(
                config.openalgo_base_url,
                config.openalgo_api_key,
            ),
        )

        def price_alert_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
            return price_alerts.evaluate()

        handlers["price_alert_evaluation"] = price_alert_evaluation

        from .screener_service import ScreenerService
        from .watch_service import WatchService

        watch_service = WatchService(
            config.database_path,
            ScreenerService(
                config.database_path,
                OpenAlgoClient(
                    config.openalgo_base_url,
                    config.openalgo_api_key,
                ),
            ),
        )

        def watch_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
            return watch_service.evaluate()

        handlers["watch_evaluation"] = watch_evaluation

        # Universe backfill takes small, resumable bites so a scheduled run
        # never blocks for long and an expired token just means "retry later".
        from .openalgo_history_import_service import OpenAlgoHistoryImportService
        from .universe_backfill_service import UniverseBackfillService

        backfill = UniverseBackfillService(
            config.database_path, OpenAlgoHistoryImportService(config)
        )

        def universe_backfill(payload: dict[str, Any]) -> dict[str, Any]:
            return backfill.run(
                universe=payload.get("universe", "nifty50"),
                interval=payload.get("interval", "D"),
                max_symbols=int(payload.get("max_symbols", 3)),
            )

        handlers["universe_backfill"] = universe_backfill

    # The arena needs no broker credentials — it runs on stored data through a
    # simulated ledger — so its tick is always available.
    from .arena_service import ArenaService
    from .backtest_service import BacktestService as _ArenaBacktests
    from ..tools.registry import _dataset_for_request as _resolve_dataset

    arena = ArenaService(
        config.database_path,
        _ArenaBacktests(config.database_path, allow_live_trading=False),
    )

    def arena_daily_tick(payload: dict[str, Any]) -> dict[str, Any]:
        """Tick every open season; days without data are recorded as missing."""
        results = []
        for season in arena.list_seasons()["seasons"]:
            if season["status"] != "open":
                continue
            dataset_id = _resolve_dataset(
                config.database_path,
                symbol=season["symbol"],
                exchange=season["exchange"],
                raise_on_missing=False,
            )
            results.append(arena.tick(season["season_id"], dataset_id=dataset_id))
        return {"seasons_ticked": len(results), "results": results}

    handlers["arena_daily_tick"] = arena_daily_tick

    # Autonomy: the supervisor re-runs agents on a timer and reports drift.
    # It only ever flags - it retires nothing and trades nothing.
    from .supervisor_service import SupervisorService

    def agent_supervisor_sweep(payload: dict[str, Any]) -> dict[str, Any]:
        from ..agents.base import AgentTask
        from ..agents.roster import build_founding_roster
        from ..services.agent_evaluation_service import AgentEvaluationService
        from ..services.agent_registry_service import AgentRegistryService
        from ..tools.registry import build_default_tool_registry

        registry = build_default_tool_registry(
            config.database_path,
            allow_live_trading=False,
            openalgo_base_url=config.openalgo_base_url,
            openalgo_api_key=config.openalgo_api_key,
            artifacts_dir=config.artifacts_dir,
            app_config=config,
        )
        roster = {a.name: a for a in build_founding_roster(registry)}
        agent_registry = AgentRegistryService(config.database_path)
        evaluation = AgentEvaluationService(config.database_path)

        def _run(name: str, symbol: str) -> dict[str, Any]:
            agent = roster[name]
            task = AgentTask(task_type="scheduled", symbol=symbol)
            result = agent.run(task)
            run_id = agent_registry.record_run(agent, task, result)
            card = evaluation.score_run(
                {
                    "status": result.status,
                    "findings": result.findings,
                    "evidence": result.evidence,
                },
                agent.category,
            )
            evaluation.record_score(
                agent_id=agent.agent_id,
                version=agent.version,
                run_id=run_id,
                scorecard=card,
            )
            return {"status": result.status, "run_id": run_id}

        def _candles(symbol: str) -> list[dict[str, Any]]:
            dataset_id = _resolve_dataset(
                config.database_path, symbol=symbol, exchange="NSE",
                raise_on_missing=False,
            )
            if not dataset_id:
                return []
            _ds, candles = _ArenaBacktests(
                config.database_path, allow_live_trading=False
            ).load_dataset_candles(dataset_id)
            return candles

        supervisor = SupervisorService(
            config.database_path,
            run_agent=_run,
            freshness=freshness,
            enqueue_refresh=lambda dataset_id: _built["jobs"].register(
                name=f"refresh_{dataset_id}"[:80],
                job_type="freshness_sweep",
                schedule_seconds=3600,
            ),
            load_candles=_candles,
        )
        agents = payload.get("agents") or [
            name for name in ("strategy_validator", "market_researcher")
            if name in roster
        ]
        return supervisor.sweep(agents, payload.get("symbol", "RELIANCE"))

    handlers["agent_supervisor_sweep"] = agent_supervisor_sweep

    # The digest reads what the supervisor already found and composes one
    # brief. It runs after the sweep on a longer cycle, so a day's findings
    # arrive as a single readable summary rather than a stream of alerts.
    from .daily_digest_service import DailyDigestService

    def daily_digest(payload: dict[str, Any]) -> dict[str, Any]:
        from .agent_evaluation_service import AgentEvaluationService
        from .data_health_service import DataHealthService
        from .supervisor_service import SupervisorService as _Supervisor

        digest = DailyDigestService(
            config.database_path,
            evaluation=AgentEvaluationService(config.database_path),
            supervisor=_Supervisor(config.database_path),
            data_health=DataHealthService(config.database_path),
        )
        result = digest.generate(symbol=payload.get("symbol"))
        return {
            "digest_id": result["digest_id"],
            "headline": result["headline"],
            "sections": len(result["sections"]),
        }

    handlers["daily_digest"] = daily_digest
    if config.market_news_provider and config.market_news_api_url:
        news = MarketNewsService(config)

        def market_news_refresh(payload: dict[str, Any]) -> dict[str, Any]:
            result = news.fetch(
                query=payload.get("query")
                or "NIFTY Indian stock market outlook",
                symbol=payload.get("symbol"),
            )
            return {
                "fetch_id": result.get("fetch_id"),
                "status": result.get("status"),
                "article_count": len(result.get("articles", [])),
            }

        handlers["market_news_refresh"] = market_news_refresh
    _built["jobs"] = JobService(config.database_path, handlers)
    return _built["jobs"]


def build_task_service(db_path: Path) -> TaskService:
    robustness = RobustnessService(db_path)

    def robustness_experiment(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = dict(payload)
        requested_by = str(request.pop("requested_by", "task_worker"))
        return robustness.run(
            **request,
            requested_by=requested_by,
        )

    return TaskService(
        db_path,
        {"robustness_experiment": robustness_experiment},
    )


def register_default_jobs(
    service: JobService,
    *,
    include_openalgo: bool,
    include_market_news: bool = False,
) -> list[str]:
    job_ids = [
        service.register(
            name="dataset_freshness_sweep",
            job_type="freshness_sweep",
            schedule_seconds=900,
        ),
        service.register(
            name="governed_knowledge_sync",
            job_type="knowledge_sync",
            schedule_seconds=3600,
        ),
        service.register(
            name="daily_backup_restore_verification",
            job_type="backup_restore_verification",
            schedule_seconds=86400,
        ),
        service.register(
            name="daily_governed_retrieval_evaluation",
            job_type="governed_retrieval_evaluation",
            schedule_seconds=86400,
        ),
        service.register(
            name="daily_retention_preview",
            job_type="retention_preview",
            schedule_seconds=86400,
        ),
        service.register(
            name="operational_alert_evaluation",
            job_type="alert_evaluation",
            schedule_seconds=60,
        ),
        # Autonomous agent sweep: re-run key agents and report drift.
        service.register(
            name="agent_supervisor_sweep",
            job_type="agent_supervisor_sweep",
            schedule_seconds=21600,  # every 6 hours
        ),
        service.register(
            name="arena_daily_tick",
            job_type="arena_daily_tick",
            schedule_seconds=86400,
        ),
        # One brief a day: what changed, what's stale, what degraded.
        service.register(
            name="daily_digest",
            job_type="daily_digest",
            schedule_seconds=86400,
        ),
    ]
    if include_openalgo:
        # Backfill needs broker credentials, so it is scheduled only alongside
        # the other broker-dependent jobs — registering it unconditionally
        # would reference a handler that does not exist.
        job_ids.append(
            service.register(
                name="universe_backfill",
                job_type="universe_backfill",
                schedule_seconds=3600,
            )
        )
        job_ids.append(
            service.register(
                name="openalgo_account_snapshot",
                job_type="openalgo_snapshot",
                schedule_seconds=30,
                payload={
                    "types": [
                        "funds",
                        "positionbook",
                        "orderbook",
                        "tradebook",
                    ]
                },
                max_retries=5,
            )
        )
        job_ids.append(
            service.register(
                name="price_alert_evaluation",
                job_type="price_alert_evaluation",
                schedule_seconds=60,
                max_retries=5,
            )
        )
    if include_market_news:
        job_ids.append(
            service.register(
                name="market_news_refresh",
                job_type="market_news_refresh",
                schedule_seconds=1800,
                max_retries=3,
            )
        )
    return job_ids


def operational_summary(config: AppConfig) -> dict[str, Any]:
    con = connect(config.database_path)
    try:
        values = con.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM scheduled_jobs WHERE enabled = TRUE),
              (SELECT COUNT(*) FROM job_runs WHERE status = 'failed'),
              (SELECT COUNT(*) FROM freshness_assessments WHERE status = 'stale'),
              (SELECT COUNT(*) FROM approval_requests WHERE status = 'pending'),
              (SELECT COUNT(*) FROM order_intents
               WHERE status = 'submission_uncertain'),
              (SELECT COUNT(*) FROM work_tasks
               WHERE status IN ('queued', 'retry', 'running')),
              (SELECT COUNT(*) FROM work_tasks WHERE status = 'failed'),
              (SELECT COUNT(*) FROM operational_alerts
               WHERE status IN ('active', 'acknowledged')
                 AND severity = 'critical'),
              (SELECT COUNT(*) FROM operational_alerts
               WHERE status IN ('active', 'acknowledged')
                 AND severity = 'warning')
            """
        ).fetchone()
    finally:
        con.close()
    return {
        "enabled_jobs": values[0],
        "failed_job_runs": values[1],
        "stale_assessments": values[2],
        "pending_approvals": values[3],
        "uncertain_submissions": values[4],
        "active_tasks": values[5],
        "failed_tasks": values[6],
        "active_critical_alerts": values[7],
        "active_warning_alerts": values[8],
    }
