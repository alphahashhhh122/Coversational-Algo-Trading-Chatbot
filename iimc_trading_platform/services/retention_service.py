from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..db import connect


PURGE_CONFIRMATION = "PURGE_EXPIRED_OPERATIONAL_DATA"


class RetentionService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def policies(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT policy_name, retention_days, enabled,
                       automatic_execution, description, updated_at
                FROM retention_policies
                ORDER BY policy_name
                """
            ).fetchall()
        finally:
            con.close()
        return {
            "policies": [
                {
                    "policy_name": row[0],
                    "retention_days": row[1],
                    "enabled": row[2],
                    "automatic_execution": row[3],
                    "description": row[4],
                    "updated_at": row[5],
                }
                for row in rows
            ],
            "protected_data": [
                "market and source data",
                "strategy runs and manifests",
                "signals and risk decisions",
                "orders, fills, and performance",
                "portfolios, positions, reservations, and ledgers",
                "reports and AI/retrieval evaluation evidence",
                "audit events",
            ],
        }

    def preview(
        self,
        *,
        actor: str,
        policy_names: list[str] | None = None,
        reference_time: datetime | None = None,
    ) -> dict[str, Any]:
        active_time = reference_time or _utc_now()
        policies = self._selected_policies(policy_names)
        con = connect(self.db_path)
        try:
            counts = {
                policy["policy_name"]: self._count(
                    con,
                    policy["policy_name"],
                    active_time
                    - timedelta(days=policy["retention_days"]),
                )
                for policy in policies
            }
        finally:
            con.close()
        return self._record_run(
            mode="preview",
            policies=policies,
            candidate_counts=counts,
            deleted_counts={},
            actor=actor,
            status="completed",
        )

    def execute(
        self,
        *,
        actor: str,
        confirmation: str,
        policy_names: list[str] | None = None,
        reference_time: datetime | None = None,
    ) -> dict[str, Any]:
        if confirmation != PURGE_CONFIRMATION:
            raise ValueError(
                f"Confirmation must equal {PURGE_CONFIRMATION}"
            )
        active_time = reference_time or _utc_now()
        policies = self._selected_policies(policy_names)
        retention_run_id = f"retention_{uuid.uuid4().hex[:12]}"
        started_at = _utc_now()
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            candidates = {}
            deleted = {}
            for policy in policies:
                policy_name = policy["policy_name"]
                cutoff = active_time - timedelta(
                    days=policy["retention_days"]
                )
                candidates[policy_name] = self._count(
                    con,
                    policy_name,
                    cutoff,
                )
                deleted[policy_name] = self._delete(
                    con,
                    policy_name,
                    cutoff,
                )
            finished_at = _utc_now()
            con.execute(
                """
                INSERT INTO retention_runs VALUES (
                    ?, 'execute', ?, ?, ?, ?, 'completed', ?, ?
                )
                """,
                [
                    retention_run_id,
                    json.dumps(
                        [item["policy_name"] for item in policies]
                    ),
                    json.dumps(candidates, sort_keys=True),
                    json.dumps(deleted, sort_keys=True),
                    actor,
                    started_at,
                    finished_at,
                ],
            )
            con.execute(
                """
                INSERT INTO audit_events VALUES (
                    ?, 'retention_run', ?, 'operational_data_purged',
                    ?, ?, ?
                )
                """,
                [
                    f"audit_{uuid.uuid4().hex}",
                    retention_run_id,
                    actor,
                    json.dumps(
                        {
                            "policies": [
                                item["policy_name"]
                                for item in policies
                            ],
                            "candidate_counts": candidates,
                            "deleted_counts": deleted,
                        },
                        sort_keys=True,
                    ),
                    finished_at,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return {
            "retention_run_id": retention_run_id,
            "mode": "execute",
            "status": "completed",
            "policy_names": [
                item["policy_name"] for item in policies
            ],
            "candidate_counts": candidates,
            "deleted_counts": deleted,
            "actor": actor,
            "started_at": started_at,
            "finished_at": finished_at,
        }

    def recent_runs(self, limit: int = 50) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT retention_run_id, mode, policy_names_json,
                       candidate_counts_json, deleted_counts_json,
                       actor, status, started_at, finished_at
                FROM retention_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "runs": [
                {
                    "retention_run_id": row[0],
                    "mode": row[1],
                    "policy_names": json.loads(row[2]),
                    "candidate_counts": json.loads(row[3]),
                    "deleted_counts": json.loads(row[4]),
                    "actor": row[5],
                    "status": row[6],
                    "started_at": row[7],
                    "finished_at": row[8],
                }
                for row in rows
            ]
        }

    def _selected_policies(
        self,
        policy_names: list[str] | None,
    ) -> list[dict[str, Any]]:
        available = {
            item["policy_name"]: item
            for item in self.policies()["policies"]
            if item["enabled"]
        }
        selected_names = (
            list(dict.fromkeys(policy_names))
            if policy_names
            else sorted(available)
        )
        unknown = [
            name for name in selected_names if name not in available
        ]
        if unknown:
            raise ValueError(
                "Unknown or disabled retention policies: "
                + ", ".join(unknown)
            )
        return [available[name] for name in selected_names]

    def _record_run(
        self,
        *,
        mode: str,
        policies: list[dict[str, Any]],
        candidate_counts: dict[str, Any],
        deleted_counts: dict[str, Any],
        actor: str,
        status: str,
    ) -> dict[str, Any]:
        retention_run_id = f"retention_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        policy_names = [
            item["policy_name"] for item in policies
        ]
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO retention_runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    retention_run_id,
                    mode,
                    json.dumps(policy_names),
                    json.dumps(candidate_counts, sort_keys=True),
                    json.dumps(deleted_counts, sort_keys=True),
                    actor,
                    status,
                    now,
                    now,
                ],
            )
        finally:
            con.close()
        return {
            "retention_run_id": retention_run_id,
            "mode": mode,
            "status": status,
            "policy_names": policy_names,
            "candidate_counts": candidate_counts,
            "deleted_counts": deleted_counts,
            "actor": actor,
            "started_at": now,
            "finished_at": now,
        }

    @staticmethod
    def _count(
        con,
        policy_name: str,
        cutoff: datetime,
    ) -> dict[str, int]:
        if policy_name == "expired_auth_sessions":
            return {
                "auth_sessions": con.execute(
                    """
                    SELECT COUNT(*) FROM auth_sessions
                    WHERE expires_at < ?
                       OR (revoked_at IS NOT NULL AND revoked_at < ?)
                    """,
                    [cutoff, cutoff],
                ).fetchone()[0]
            }
        if policy_name == "inactive_chat_history":
            sessions = con.execute(
                """
                SELECT session_id FROM chat_sessions
                WHERE updated_at < ?
                """,
                [cutoff],
            ).fetchall()
            session_ids = [row[0] for row in sessions]
            message_count = _count_for_ids(
                con,
                "chat_messages",
                "session_id",
                session_ids,
            )
            return {
                "chat_sessions": len(session_ids),
                "chat_messages": message_count,
            }
        simple = {
            "completed_tool_calls": (
                "tool_calls",
                "status IN ('succeeded', 'failed', 'cancelled') "
                "AND COALESCE(finished_at, created_at) < ?",
            ),
            "openalgo_snapshots": (
                "openalgo_snapshots",
                "captured_at < ?",
            ),
            "retrieval_events": (
                "retrieval_events",
                "created_at < ?",
            ),
            "job_runs": (
                "job_runs",
                "COALESCE(finished_at, started_at) < ?",
            ),
            "terminal_work_tasks": (
                "work_tasks",
                "status IN ('succeeded', 'failed') "
                "AND COALESCE(finished_at, updated_at) < ?",
            ),
        }
        table, condition = simple[policy_name]
        return {
            table: con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {condition}",
                [cutoff],
            ).fetchone()[0]
        }

    @staticmethod
    def _delete(
        con,
        policy_name: str,
        cutoff: datetime,
    ) -> dict[str, int]:
        candidates = RetentionService._count(
            con,
            policy_name,
            cutoff,
        )
        if policy_name == "expired_auth_sessions":
            con.execute(
                """
                DELETE FROM auth_sessions
                WHERE expires_at < ?
                   OR (revoked_at IS NOT NULL AND revoked_at < ?)
                """,
                [cutoff, cutoff],
            )
            return candidates
        if policy_name == "inactive_chat_history":
            session_ids = [
                row[0]
                for row in con.execute(
                    """
                    SELECT session_id FROM chat_sessions
                    WHERE updated_at < ?
                    """,
                    [cutoff],
                ).fetchall()
            ]
            _delete_for_ids(
                con,
                "chat_messages",
                "session_id",
                session_ids,
            )
            _delete_for_ids(
                con,
                "chat_sessions",
                "session_id",
                session_ids,
            )
            return candidates
        simple = {
            "completed_tool_calls": (
                "tool_calls",
                "status IN ('succeeded', 'failed', 'cancelled') "
                "AND COALESCE(finished_at, created_at) < ?",
            ),
            "openalgo_snapshots": (
                "openalgo_snapshots",
                "captured_at < ?",
            ),
            "retrieval_events": (
                "retrieval_events",
                "created_at < ?",
            ),
            "job_runs": (
                "job_runs",
                "COALESCE(finished_at, started_at) < ?",
            ),
            "terminal_work_tasks": (
                "work_tasks",
                "status IN ('succeeded', 'failed') "
                "AND COALESCE(finished_at, updated_at) < ?",
            ),
        }
        table, condition = simple[policy_name]
        con.execute(
            f"DELETE FROM {table} WHERE {condition}",
            [cutoff],
        )
        return candidates


def _count_for_ids(
    con,
    table: str,
    column: str,
    values: list[str],
) -> int:
    if not values:
        return 0
    placeholders = ", ".join("?" for _ in values)
    return con.execute(
        f"SELECT COUNT(*) FROM {table} "
        f"WHERE {column} IN ({placeholders})",
        values,
    ).fetchone()[0]


def _delete_for_ids(
    con,
    table: str,
    column: str,
    values: list[str],
) -> None:
    if not values:
        return
    placeholders = ", ".join("?" for _ in values)
    con.execute(
        f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
        values,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
