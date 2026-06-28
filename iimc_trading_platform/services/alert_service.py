from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .backup_service import BackupService


class AlertService:
    def __init__(self, db_path: Path, artifacts_dir: Path) -> None:
        self.db_path = db_path
        self.backups = BackupService(
            db_path,
            artifacts_dir / "backups",
        )

    def evaluate(self, *, actor: str) -> dict[str, Any]:
        now = _utc_now()
        observations = self._observations(now)
        rules = self._rules()
        evaluation_id = f"alert_eval_{uuid.uuid4().hex[:12]}"
        activated = []
        resolved = []
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            for rule in rules:
                rule_key = rule["rule_key"]
                observation = observations[rule_key]
                violated = observation["value"] > rule["threshold_value"]
                current = con.execute(
                    """
                    SELECT alert_id, status
                    FROM operational_alerts
                    WHERE rule_key = ?
                      AND status IN ('active', 'acknowledged')
                    ORDER BY first_seen_at DESC
                    LIMIT 1
                    """,
                    [rule_key],
                ).fetchone()
                if violated:
                    message = self._message(
                        rule,
                        observation["value"],
                    )
                    if current:
                        con.execute(
                            """
                            UPDATE operational_alerts
                            SET observed_value = ?, threshold_value = ?,
                                severity = ?, message = ?, details_json = ?,
                                last_seen_at = ?
                            WHERE alert_id = ?
                            """,
                            [
                                observation["value"],
                                rule["threshold_value"],
                                rule["severity"],
                                message,
                                json.dumps(
                                    observation["details"],
                                    sort_keys=True,
                                    default=str,
                                ),
                                now,
                                current[0],
                            ],
                        )
                        activated.append(current[0])
                    else:
                        alert_id = f"alert_{uuid.uuid4().hex[:12]}"
                        con.execute(
                            """
                            INSERT INTO operational_alerts VALUES (
                                ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?,
                                NULL, NULL, NULL, NULL
                            )
                            """,
                            [
                                alert_id,
                                rule_key,
                                rule["severity"],
                                observation["value"],
                                rule["threshold_value"],
                                message,
                                json.dumps(
                                    observation["details"],
                                    sort_keys=True,
                                    default=str,
                                ),
                                now,
                                now,
                            ],
                        )
                        activated.append(alert_id)
                elif current:
                    con.execute(
                        """
                        UPDATE operational_alerts
                        SET status = 'resolved', observed_value = ?,
                            last_seen_at = ?, resolved_at = ?
                        WHERE alert_id = ?
                        """,
                        [
                            observation["value"],
                            now,
                            now,
                            current[0],
                        ],
                    )
                    resolved.append(current[0])
            counts = con.execute(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status = 'active'),
                  COUNT(*) FILTER (WHERE status = 'acknowledged'),
                  COUNT(*) FILTER (WHERE status = 'resolved')
                FROM operational_alerts
                """
            ).fetchone()
            con.execute(
                """
                INSERT INTO alert_evaluation_runs VALUES (
                    ?, ?, ?, ?, ?, 'completed', ?, ?
                )
                """,
                [
                    evaluation_id,
                    json.dumps(
                        observations,
                        sort_keys=True,
                        default=str,
                    ),
                    counts[0],
                    counts[1],
                    len(resolved),
                    actor,
                    now,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return {
            "evaluation_id": evaluation_id,
            "observations": observations,
            "active_count": counts[0],
            "acknowledged_count": counts[1],
            "new_or_updated_alert_ids": activated,
            "resolved_alert_ids": resolved,
            "evaluated_at": now,
        }

    def list(
        self,
        *,
        include_resolved: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        where = "" if include_resolved else "WHERE a.status != 'resolved'"
        con = connect(self.db_path)
        try:
            rows = con.execute(
                f"""
                SELECT a.alert_id, a.rule_key, a.status, a.severity,
                       a.observed_value, a.threshold_value, a.message,
                       a.details_json, a.first_seen_at, a.last_seen_at,
                       a.acknowledged_by, a.acknowledged_at,
                       a.acknowledgement_reason, a.resolved_at,
                       r.description, r.runbook_uri
                FROM operational_alerts AS a
                JOIN alert_rules AS r ON r.rule_key = a.rule_key
                {where}
                ORDER BY
                  CASE a.severity
                    WHEN 'critical' THEN 0
                    WHEN 'warning' THEN 1
                    ELSE 2
                  END,
                  a.last_seen_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "alerts": [
                {
                    "alert_id": row[0],
                    "rule_key": row[1],
                    "status": row[2],
                    "severity": row[3],
                    "observed_value": row[4],
                    "threshold_value": row[5],
                    "message": row[6],
                    "details": json.loads(row[7]),
                    "first_seen_at": row[8],
                    "last_seen_at": row[9],
                    "acknowledged_by": row[10],
                    "acknowledged_at": row[11],
                    "acknowledgement_reason": row[12],
                    "resolved_at": row[13],
                    "description": row[14],
                    "runbook_uri": row[15],
                }
                for row in rows
            ]
        }

    def acknowledge(
        self,
        alert_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        con = connect(self.db_path)
        try:
            changed = con.execute(
                """
                UPDATE operational_alerts
                SET status = 'acknowledged', acknowledged_by = ?,
                    acknowledged_at = ?, acknowledgement_reason = ?
                WHERE alert_id = ? AND status = 'active'
                RETURNING alert_id
                """,
                [actor, now, reason, alert_id],
            ).fetchone()
        finally:
            con.close()
        if changed is None:
            raise ValueError(
                "Active alert not found or already acknowledged"
            )
        return next(
            item
            for item in self.list(
                include_resolved=True,
                limit=500,
            )["alerts"]
            if item["alert_id"] == alert_id
        )

    def _rules(self) -> list[dict[str, Any]]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT rule_key, severity, threshold_value,
                       description, runbook_uri
                FROM alert_rules
                WHERE enabled = TRUE
                ORDER BY rule_key
                """
            ).fetchall()
        finally:
            con.close()
        return [
            {
                "rule_key": row[0],
                "severity": row[1],
                "threshold_value": row[2],
                "description": row[3],
                "runbook_uri": row[4],
            }
            for row in rows
        ]

    def _observations(
        self,
        now: datetime,
    ) -> dict[str, dict[str, Any]]:
        day_ago = now - timedelta(hours=24)
        stale_lock = now - timedelta(minutes=15)
        overdue_approval = now - timedelta(minutes=30)
        con = connect(self.db_path)
        try:
            values = con.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM order_intents
                   WHERE status = 'submission_uncertain'),
                  (SELECT COUNT(*) FROM work_tasks
                   WHERE status = 'running' AND locked_at < ?),
                  (SELECT COUNT(*) FROM work_tasks
                   WHERE status = 'failed' AND updated_at >= ?),
                  (SELECT COUNT(*) FROM job_runs
                   WHERE status = 'failed' AND started_at >= ?),
                  (
                    SELECT COUNT(*) FROM (
                      SELECT dataset_id, status,
                             ROW_NUMBER() OVER (
                               PARTITION BY dataset_id
                               ORDER BY created_at DESC
                             ) AS rank
                      FROM freshness_assessments
                      WHERE purpose = 'current_market'
                    ) AS latest
                    WHERE rank = 1 AND status = 'stale'
                  ),
                  (SELECT COUNT(*) FROM approval_requests
                   WHERE status = 'pending' AND created_at < ?),
                  COALESCE((
                    SELECT CASE WHEN status = 'failed' THEN 1 ELSE 0 END
                    FROM ai_eval_runs
                    ORDER BY started_at DESC
                    LIMIT 1
                  ), 0),
                  COALESCE((
                    SELECT CASE WHEN status = 'failed' THEN 1 ELSE 0 END
                    FROM retrieval_eval_runs
                    ORDER BY started_at DESC
                    LIMIT 1
                  ), 0)
                """,
                [stale_lock, day_ago, day_ago, overdue_approval],
            ).fetchone()
        finally:
            con.close()
        available_backups = [
            item
            for item in self.backups.list()["backups"]
            if item.get("status") == "available"
        ]
        if available_backups:
            created_at = datetime.fromisoformat(
                available_backups[0]["created_at"]
            ).replace(tzinfo=None)
            backup_age_hours = max(
                0.0,
                (now - created_at).total_seconds() / 3600,
            )
            backup_details = {
                "backup_id": available_backups[0]["backup_id"],
                "created_at": available_backups[0]["created_at"],
            }
        else:
            backup_age_hours = 1_000_000.0
            backup_details = {"reason": "no_available_backup"}
        keys = [
            "uncertain_broker_submissions",
            "stale_running_tasks",
            "failed_tasks_24h",
            "failed_jobs_24h",
            "stale_current_market_datasets",
            "overdue_pending_approvals",
            "failed_ai_evaluation",
            "failed_retrieval_evaluation",
        ]
        observations = {
            key: {"value": float(value), "details": {}}
            for key, value in zip(keys, values)
        }
        observations["backup_age_hours"] = {
            "value": round(backup_age_hours, 3),
            "details": backup_details,
        }
        return observations

    @staticmethod
    def _message(
        rule: dict[str, Any],
        observed_value: float,
    ) -> str:
        return (
            f"{rule['description']}: observed {observed_value:g}, "
            f"threshold {rule['threshold_value']:g}."
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
