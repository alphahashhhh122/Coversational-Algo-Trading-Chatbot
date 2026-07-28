from __future__ import annotations

import argparse
import getpass
import json
import logging
import time
import uuid
from pathlib import Path

from .config import load_config, public_config
from .infrastructure import initialize_database, list_tables
from .observability import configure_logging
from .orchestration import build_orchestrator
from .services import (
    BackupService,
    CapabilityCoverageService,
    build_job_service,
    build_task_service,
    foundation_health,
    OpenAlgoReadinessService,
    register_default_jobs,
    verify_clean_foundation,
)
from .services.auth_service import AuthService
from .services.ai_evaluation_service import AiEvaluationService
from .services.alert_service import AlertService
from .services.retrieval_evaluation_service import (
    RetrievalEvaluationService,
)
from .services.retention_service import (
    PURGE_CONFIRMATION,
    RetentionService,
)
from .services.storage_migration_service import StorageMigrationService
from .tools import get_dataset_detail, list_datasets


def main() -> None:
    parser = argparse.ArgumentParser(description="IIMC trading platform CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize local development database.")
    subparsers.add_parser("doctor", help="Check local foundation health and safety.")
    subparsers.add_parser(
        "verify-foundation",
        help="Verify initialization and health using an isolated temporary database.",
    )
    subparsers.add_parser("list-tables", help="List database tables.")
    subparsers.add_parser("list-datasets", help="List cataloged datasets.")
    user_parser = subparsers.add_parser(
        "create-user",
        help="Create an authenticated platform user.",
    )
    user_parser.add_argument("username")
    user_parser.add_argument(
        "--role",
        choices=["viewer", "researcher", "approver", "admin"],
        required=True,
    )
    user_parser.add_argument(
        "--password",
        help="Omit this option to enter the password without shell history.",
    )
    subparsers.add_parser("list-jobs", help="List scheduled background jobs.")
    worker_parser = subparsers.add_parser(
        "run-worker",
        help="Run persistent scheduled jobs.",
    )
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument("--poll-seconds", type=int, default=5)
    task_worker_parser = subparsers.add_parser(
        "run-task-worker",
        help="Run durable one-off work tasks.",
    )
    task_worker_parser.add_argument("--once", action="store_true")
    task_worker_parser.add_argument("--poll-seconds", type=int, default=5)
    subparsers.add_parser(
        "backup-create",
        help="Create and verify a portable database backup.",
    )
    subparsers.add_parser("backup-list", help="List database backups.")
    verify_backup_parser = subparsers.add_parser(
        "backup-verify",
        help="Verify checksums and restore a backup into a temporary database.",
    )
    verify_backup_parser.add_argument("backup_id")
    restore_backup_parser = subparsers.add_parser(
        "backup-restore",
        help="Restore a verified backup into a new database path.",
    )
    restore_backup_parser.add_argument("backup_id")
    restore_backup_parser.add_argument("target_path")
    ai_eval_parser = subparsers.add_parser(
        "ai-eval",
        help="Run the versioned orchestration and response evaluation suite.",
    )
    ai_eval_parser.add_argument(
        "--mode",
        choices=["offline", "configured"],
        default="offline",
    )
    subparsers.add_parser(
        "retrieval-eval",
        help="Run the versioned governed-retrieval ranking benchmark.",
    )
    retention_preview_parser = subparsers.add_parser(
        "retention-preview",
        help="Preview expired operational records without deleting data.",
    )
    retention_preview_parser.add_argument(
        "--policy",
        action="append",
        dest="policies",
    )
    retention_run_parser = subparsers.add_parser(
        "retention-run",
        help="Delete expired operational records after explicit confirmation.",
    )
    retention_run_parser.add_argument(
        "--policy",
        action="append",
        dest="policies",
    )
    retention_run_parser.add_argument("--confirm", required=True)
    subparsers.add_parser(
        "alerts-evaluate",
        help="Evaluate operational alert rules and persist transitions.",
    )
    subparsers.add_parser(
        "alerts-list",
        help="List active and acknowledged operational alerts.",
    )
    subparsers.add_parser(
        "storage-plan",
        help="Generate and verify the PostgreSQL/object-storage migration plan.",
    )
    subparsers.add_parser(
        "storage-export-analytical",
        help="Export verified partitioned Parquet market history.",
    )
    platform_status_parser = subparsers.add_parser(
        "platform-status",
        help="Check local/provider readiness for one symbol.",
    )
    _add_readiness_args(platform_status_parser)
    subparsers.add_parser(
        "openalgo-check",
        help="Check OpenAlgo credentials, reachability, and analyzer state.",
    )
    subparsers.add_parser(
        "openalgo-monitor",
        help="Read OpenAlgo analyzer/funds/order/trade/position status.",
    )
    openalgo_readiness_parser = subparsers.add_parser(
        "openalgo-readiness",
        help="Check OpenAlgo readiness for one symbol without placing orders.",
    )
    _add_readiness_args(openalgo_readiness_parser)

    dataset_parser = subparsers.add_parser("dataset", help="Show dataset detail.")
    dataset_parser.add_argument("dataset_id")

    subparsers.add_parser("show-config", help="Print active config.")

    args = parser.parse_args()
    config = load_config()
    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    logger.info(
        "CLI command started",
        extra={"event": "cli_command_started", "command": args.command},
    )

    if args.command == "init-db":
        initialize_database(config.database_path)
        print(json.dumps({"status": "ok", "database_path": str(config.database_path)}))
    elif args.command == "doctor":
        print(json.dumps(foundation_health(config), indent=2, default=str))
    elif args.command == "verify-foundation":
        print(json.dumps(verify_clean_foundation(), indent=2, default=str))
    elif args.command == "list-tables":
        print(json.dumps({"tables": list_tables(config.database_path)}, indent=2))
    elif args.command == "list-datasets":
        print(json.dumps(list_datasets(config.database_path), indent=2, default=str))
    elif args.command == "dataset":
        print(
            json.dumps(
                get_dataset_detail(args.dataset_id, config.database_path),
                indent=2,
                default=str,
            )
        )
    elif args.command == "show-config":
        print(json.dumps(public_config(config), indent=2, default=str))
    elif args.command == "create-user":
        if not config.auth_secret:
            raise SystemExit("IIMC_AUTH_SECRET is not configured")
        initialize_database(config.database_path)
        password = args.password or getpass.getpass("Password: ")
        if not password:
            raise SystemExit("Password cannot be empty")
        user = AuthService(
            config.database_path,
            secret=config.auth_secret,
            session_ttl_minutes=config.session_ttl_minutes,
        ).create_user(
            username=args.username,
            password=password,
            role=args.role,
        )
        print(
            json.dumps(
                {
                    "user_id": user.user_id,
                    "username": user.username,
                    "role": user.role,
                },
                indent=2,
            )
        )
    elif args.command == "list-jobs":
        initialize_database(config.database_path)
        service = build_job_service(config)
        register_default_jobs(
            service,
            include_openalgo=bool(config.openalgo_api_key),
        )
        print(json.dumps(service.list_jobs(), indent=2, default=str))
    elif args.command == "run-worker":
        if args.poll_seconds < 1:
            raise SystemExit("--poll-seconds must be positive")
        initialize_database(config.database_path)
        service = build_job_service(config)
        register_default_jobs(
            service,
            include_openalgo=bool(config.openalgo_api_key),
        )
        worker_id = f"worker_{uuid.uuid4().hex[:10]}"
        while True:
            results = service.run_due(worker_id)
            if results:
                print(json.dumps(results, indent=2, default=str))
            if args.once:
                break
            time.sleep(args.poll_seconds)
    elif args.command == "run-task-worker":
        if args.poll_seconds < 1:
            raise SystemExit("--poll-seconds must be positive")
        initialize_database(config.database_path)
        service = build_task_service(config.database_path)
        worker_id = f"task_worker_{uuid.uuid4().hex[:10]}"
        while True:
            results = service.run_due(worker_id)
            if results:
                print(json.dumps(results, indent=2, default=str))
            if args.once:
                break
            time.sleep(args.poll_seconds)
    elif args.command == "backup-create":
        initialize_database(config.database_path)
        result = BackupService(
            config.database_path,
            config.artifacts_dir / "backups",
        ).create(created_by="cli")
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "backup-list":
        result = BackupService(
            config.database_path,
            config.artifacts_dir / "backups",
        ).list()
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "backup-verify":
        result = BackupService(
            config.database_path,
            config.artifacts_dir / "backups",
        ).verify(args.backup_id, verified_by="cli")
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "backup-restore":
        result = BackupService(
            config.database_path,
            config.artifacts_dir / "backups",
        ).restore(args.backup_id, Path(args.target_path))
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "ai-eval":
        configured_api_key = (
            config.groq_api_key
            if config.llm_provider == "groq"
            else config.openai_api_key
        )
        configured_model = (
            config.groq_model
            if config.llm_provider == "groq"
            else config.openai_model
        )
        if args.mode == "configured" and not configured_api_key:
            raise SystemExit(
                f"{config.llm_provider.upper()} API key is required for configured evaluation"
            )
        initialize_database(config.database_path)
        result = AiEvaluationService(
            config.database_path,
            config.artifacts_dir,
            Path(__file__).parent / "evals" / "ai_eval_cases.jsonl",
        ).run(
            orchestrator=build_orchestrator(
                api_key=configured_api_key if args.mode == "configured" else None,
                model=config.openai_model,
                provider=config.llm_provider,
                groq_api_key=(
                    config.groq_api_key
                    if args.mode == "configured"
                    else None
                ),
                groq_model=config.groq_model,
                groq_fallback_model=config.groq_fallback_model,
                require_real_llm=(args.mode == "configured"),
            ),
            model=configured_model if args.mode == "configured" else None,
            created_by="cli",
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "retrieval-eval":
        initialize_database(config.database_path)
        result = RetrievalEvaluationService(
            config.database_path,
            config.artifacts_dir,
            Path(__file__).parent
            / "evals"
            / "retrieval_eval_cases.jsonl",
        ).run(created_by="cli")
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "retention-preview":
        initialize_database(config.database_path)
        result = RetentionService(config.database_path).preview(
            actor="cli",
            policy_names=args.policies,
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "retention-run":
        if args.confirm != PURGE_CONFIRMATION:
            raise SystemExit(
                f"--confirm must equal {PURGE_CONFIRMATION}"
            )
        initialize_database(config.database_path)
        result = RetentionService(config.database_path).execute(
            actor="cli",
            confirmation=args.confirm,
            policy_names=args.policies,
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "alerts-evaluate":
        initialize_database(config.database_path)
        result = AlertService(
            config.database_path,
            config.artifacts_dir,
        ).evaluate(actor="cli")
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "alerts-list":
        initialize_database(config.database_path)
        result = AlertService(
            config.database_path,
            config.artifacts_dir,
        ).list()
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "storage-plan":
        initialize_database(config.database_path)
        result = StorageMigrationService(
            config.database_path,
            config.artifacts_dir,
        ).generate()
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "storage-export-analytical":
        initialize_database(config.database_path)
        result = StorageMigrationService(
            config.database_path,
            config.artifacts_dir,
        ).export_analytical_history()
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "platform-status":
        initialize_database(config.database_path)
        openalgo = OpenAlgoReadinessService(config)
        result = CapabilityCoverageService(
            config.database_path,
            openalgo,
        ).platform_status(
            symbol=args.symbol,
            exchange=args.exchange,
            asset_class=args.asset_class,
            interval=args.interval,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.command in {"openalgo-check", "openalgo-monitor"}:
        result = OpenAlgoReadinessService(config).monitor()
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "openalgo-readiness":
        result = OpenAlgoReadinessService(config).readiness(
            symbol=args.symbol,
            exchange=args.exchange,
            asset_class=args.asset_class,
            interval=args.interval,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        print(json.dumps(result, indent=2, default=str))


def _add_readiness_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--asset-class", required=True, dest="asset_class")
    parser.add_argument("--interval", required=True)
    parser.add_argument("--start-date", required=True, dest="start_date")
    parser.add_argument("--end-date", required=True, dest="end_date")


if __name__ == "__main__":
    main()
