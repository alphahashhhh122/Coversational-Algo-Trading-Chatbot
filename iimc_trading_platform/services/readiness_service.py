from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import AppConfig
from ..db import connect
from .health_service import foundation_health
from .operations_service import operational_summary


def production_readiness(config: AppConfig) -> dict:
    health = foundation_health(config)
    production = config.environment == "production"
    operational = (
        _operational_readiness(config)
        if health["checks"]["database_accessible"]
        else {}
    )
    checks = {
        "foundation_healthy": health["status"] == "healthy",
        "live_trading_disabled": not config.allow_live_trading,
        "authentication_configured": (
            not config.auth_required
            or bool(config.auth_secret)
        ),
        "production_auth_required": (
            config.environment != "production"
            or config.auth_required
        ),
        "production_secret_present": (
            not production
            or bool(config.auth_secret)
        ),
        "production_openai_configured": (
            not production
            or bool(config.openai_api_key)
        ),
        "production_telemetry_configured": (
            not production
            or (
                config.otel_enabled
                and bool(config.otel_exporter_otlp_endpoint)
            )
        ),
        "allowed_hosts_restricted": (
            not production
            or (
                bool(config.allowed_hosts)
                and "*" not in config.allowed_hosts
            )
        ),
        "database_parent_writable": _parent_writable(
            config.database_path
        ),
        "artifacts_directory_writable": _directory_writable(
            config.artifacts_dir
        ),
        "admin_account_present": (
            not production
            or bool(operational.get("admin_account_present"))
        ),
        "configured_ai_evaluation_passed": (
            not production
            or bool(
                operational.get(
                    "configured_ai_evaluation_passed"
                )
            )
        ),
        "retrieval_evaluation_passed": (
            not production
            or bool(
                operational.get("retrieval_evaluation_passed")
            )
        ),
        "recent_verified_backup": (
            not production
            or bool(operational.get("recent_verified_backup"))
        ),
        "alert_evaluation_present": (
            not production
            or bool(operational.get("alert_evaluation_present"))
        ),
        "no_active_critical_alerts": (
            not production
            or operational.get("active_critical_alerts", 1) == 0
        ),
    }
    ready = all(checks.values())
    return {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
        "operations": (
            operational_summary(config)
            if health["checks"]["database_accessible"]
            else None
        ),
        "operational_evidence": operational,
        "capabilities": {
            "openai_orchestration": bool(config.openai_api_key),
            "openalgo_analyzer": bool(config.openalgo_api_key),
            "live_trading": config.allow_live_trading,
            "distributed_tracing": (
                config.otel_enabled
                and bool(config.otel_exporter_otlp_endpoint)
            ),
        },
    }


def _parent_writable(path: Path) -> bool:
    parent = path.parent
    return (
        parent.exists()
        and parent.is_dir()
        and os.access(parent, os.W_OK)
    )


def _directory_writable(path: Path) -> bool:
    return (
        path.exists()
        and path.is_dir()
        and os.access(path, os.W_OK)
    )


def _operational_readiness(config: AppConfig) -> dict:
    con = connect(config.database_path)
    try:
        values = con.execute(
            """
            SELECT
              EXISTS(
                SELECT 1 FROM app_users
                WHERE role = 'admin' AND active = TRUE
              ),
              COALESCE((
                SELECT status = 'passed'
                       AND orchestration_mode = 'openai_responses'
                       AND model = ?
                FROM ai_eval_runs
                WHERE orchestration_mode = 'openai_responses'
                ORDER BY started_at DESC
                LIMIT 1
              ), FALSE),
              COALESCE((
                SELECT status = 'passed'
                FROM retrieval_eval_runs
                ORDER BY started_at DESC
                LIMIT 1
              ), FALSE),
              EXISTS(
                SELECT 1 FROM alert_evaluation_runs
              ),
              (
                SELECT COUNT(*) FROM operational_alerts
                WHERE status IN ('active', 'acknowledged')
                  AND severity = 'critical'
              )
            """,
            [config.openai_model],
        ).fetchone()
        backup = con.execute(
            """
            SELECT backup_id, verified_at
            FROM backup_verifications
            WHERE status = 'succeeded'
            ORDER BY verified_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()
    recent_verified_backup = False
    latest_backup_id = None
    latest_backup_verified_at = None
    if backup:
        latest_backup_id = backup[0]
        latest_backup_verified_at = backup[1]
        archive_exists = (
            config.artifacts_dir
            / "backups"
            / f"{backup[0]}.zip"
        ).exists()
        recent_verified_backup = (
            archive_exists
            and backup[1]
            >= datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(hours=48)
        )
    return {
        "admin_account_present": values[0],
        "configured_ai_evaluation_passed": values[1],
        "retrieval_evaluation_passed": values[2],
        "alert_evaluation_present": values[3],
        "active_critical_alerts": values[4],
        "recent_verified_backup": recent_verified_backup,
        "latest_backup_id": latest_backup_id,
        "latest_backup_verified_at": latest_backup_verified_at,
    }
