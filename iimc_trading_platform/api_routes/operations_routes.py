"""Operations: jobs, tasks, alerts, backups, retention, health probes.

Lifted out of ``create_app``. The handler bodies are unchanged; what was
an implicit closure over the application's service objects is now a
signature that names them.
"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException
from ..api_models import (
    AlertAcknowledgementRequest,
    RetentionExecuteRequest,
    RetentionPreviewRequest,
)
from ..config import public_config
from ..services import (
    Principal,
    foundation_health,
    operational_summary,
    production_readiness,
)
from fastapi.responses import PlainTextResponse
from typing import Any


def register(
    app: FastAPI,
    *,
    active_config: Any,
    admin: Any,
    alert_service: Any,
    approver: Any,
    backup_service: Any,
    chat_service: Any,
    job_service: Any,
    retention_service: Any,
    storage_migration_service: Any,
    task_service: Any,
    tool_registry: Any,
    viewer: Any,
) -> None:
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
    @app.get("/settings")
    def settings(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        """Redacted runtime configuration for the Settings view."""
        return public_config(active_config)
    @app.get("/health")
    def health() -> dict[str, Any]:
        return foundation_health(active_config)
    @app.get("/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}
    @app.get("/ready")
    def ready() -> dict[str, Any]:
        result = json.loads(
            json.dumps(production_readiness(active_config), default=str)
        )
        if result["status"] != "ready":
            raise HTTPException(status_code=503, detail=result)
        return result
    @app.get("/tools")
    def tools(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return {
            "orchestration_mode": chat_service.orchestrator.mode,
            "tools": tool_registry.list_tools(),
        }
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
