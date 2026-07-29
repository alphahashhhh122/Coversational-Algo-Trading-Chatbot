from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..db import connect


JobHandler = Callable[[dict[str, Any]], dict[str, Any]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobService:
    def __init__(
        self,
        db_path: Path,
        handlers: dict[str, JobHandler],
    ) -> None:
        self.db_path = db_path
        self.handlers = handlers

    def register(
        self,
        *,
        name: str,
        job_type: str,
        schedule_seconds: int,
        payload: dict[str, Any] | None = None,
        max_retries: int = 3,
        enabled: bool = True,
        con: Any = None,
    ) -> str:
        """Register or update one scheduled job.

        ``con`` lets a caller registering several jobs share one connection.
        Opening a DuckDB connection costs ~0.17s, so the nine default jobs were
        spending about 1.5s of every application startup — and every test that
        builds an app — on nine connections to write nine rows.
        """
        if job_type not in self.handlers:
            raise ValueError(f"Unknown job type: {job_type}")
        if schedule_seconds < 10:
            raise ValueError("Job schedule must be at least 10 seconds")
        now = utc_now()
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        owned = con is None
        con = con or connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO scheduled_jobs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (name) DO UPDATE SET
                    job_type = EXCLUDED.job_type,
                    schedule_seconds = EXCLUDED.schedule_seconds,
                    payload_json = EXCLUDED.payload_json,
                    enabled = EXCLUDED.enabled,
                    max_retries = EXCLUDED.max_retries,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    job_id,
                    name,
                    job_type,
                    schedule_seconds,
                    json.dumps(payload or {}, sort_keys=True, default=str),
                    enabled,
                    max_retries,
                    now,
                    None,
                    None,
                    now,
                    now,
                ],
            )
            stored = con.execute(
                "SELECT job_id FROM scheduled_jobs WHERE name = ?",
                [name],
            ).fetchone()[0]
        finally:
            # Only close what this call opened; a shared connection belongs to
            # the caller.
            if owned:
                con.close()
        return stored

    def list_jobs(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT j.job_id, j.name, j.job_type, j.schedule_seconds,
                       j.enabled, j.max_retries, j.next_run_at, j.locked_at,
                       j.locked_by, r.status, r.finished_at, r.error_message
                FROM scheduled_jobs AS j
                LEFT JOIN LATERAL (
                    SELECT status, finished_at, error_message
                    FROM job_runs
                    WHERE job_id = j.job_id
                    ORDER BY started_at DESC
                    LIMIT 1
                ) AS r ON TRUE
                ORDER BY j.name
                """
            ).fetchall()
        finally:
            con.close()
        return {
            "jobs": [
                {
                    "job_id": row[0],
                    "name": row[1],
                    "job_type": row[2],
                    "schedule_seconds": row[3],
                    "enabled": row[4],
                    "max_retries": row[5],
                    "next_run_at": row[6],
                    "locked_at": row[7],
                    "locked_by": row[8],
                    "last_status": row[9],
                    "last_finished_at": row[10],
                    "last_error": row[11],
                }
                for row in rows
            ]
        }

    def run_due(self, worker_id: str, limit: int = 10) -> list[dict[str, Any]]:
        results = []
        for _ in range(limit):
            claimed = self._claim_next(worker_id)
            if claimed is None:
                break
            results.append(self._execute_claimed(claimed, worker_id))
        return results

    def run_now(self, job_id: str, worker_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT job_id, job_type, payload_json, schedule_seconds,
                       max_retries
                FROM scheduled_jobs
                WHERE job_id = ? AND enabled = TRUE
                """,
                [job_id],
            ).fetchone()
            if row is None:
                raise ValueError(f"Enabled job not found: {job_id}")
            con.execute(
                """
                UPDATE scheduled_jobs
                SET locked_at = ?, locked_by = ?, updated_at = ?
                WHERE job_id = ?
                """,
                [utc_now(), worker_id, utc_now(), job_id],
            )
        finally:
            con.close()
        return self._execute_claimed(row, worker_id)

    def recent_runs(self, limit: int = 100) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT r.job_run_id, j.name, r.status, r.attempt,
                       r.worker_id, r.result_json, r.error_type,
                       r.error_message, r.started_at, r.finished_at
                FROM job_runs AS r
                JOIN scheduled_jobs AS j ON j.job_id = r.job_id
                ORDER BY r.started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "runs": [
                {
                    "job_run_id": row[0],
                    "job_name": row[1],
                    "status": row[2],
                    "attempt": row[3],
                    "worker_id": row[4],
                    "result": json.loads(row[5]) if row[5] else None,
                    "error_type": row[6],
                    "error_message": row[7],
                    "started_at": row[8],
                    "finished_at": row[9],
                }
                for row in rows
            ]
        }

    def _claim_next(self, worker_id: str):
        now = utc_now()
        stale_lock = now - timedelta(minutes=15)
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            row = con.execute(
                """
                SELECT job_id, job_type, payload_json, schedule_seconds,
                       max_retries
                FROM scheduled_jobs
                WHERE enabled = TRUE
                  AND next_run_at <= ?
                  AND (locked_at IS NULL OR locked_at < ?)
                ORDER BY next_run_at, job_id
                LIMIT 1
                """,
                [now, stale_lock],
            ).fetchone()
            if row is not None:
                con.execute(
                    """
                    UPDATE scheduled_jobs
                    SET locked_at = ?, locked_by = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    [now, worker_id, now, row[0]],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return row

    def _execute_claimed(self, row, worker_id: str) -> dict[str, Any]:
        job_id, job_type, payload_json, schedule_seconds, max_retries = row
        attempt = self._next_attempt(job_id)
        job_run_id = f"jobrun_{uuid.uuid4().hex[:12]}"
        started_at = utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO job_runs VALUES (
                    ?, ?, 'running', ?, ?, NULL, NULL, NULL, ?, NULL
                )
                """,
                [job_run_id, job_id, attempt, worker_id, started_at],
            )
        finally:
            con.close()
        try:
            result = self.handlers[job_type](json.loads(payload_json))
        except Exception as exc:
            backoff = min(schedule_seconds, 30 * (2 ** min(attempt - 1, 6)))
            self._finish(
                job_id=job_id,
                job_run_id=job_run_id,
                status="failed",
                next_run_at=utc_now() + timedelta(seconds=backoff),
                error=exc,
                disable=attempt >= max_retries,
            )
            return {
                "job_run_id": job_run_id,
                "job_id": job_id,
                "status": "failed",
                "attempt": attempt,
                "error_type": type(exc).__name__,
            }
        self._finish(
            job_id=job_id,
            job_run_id=job_run_id,
            status="succeeded",
            next_run_at=utc_now() + timedelta(seconds=schedule_seconds),
            result=result,
        )
        return {
            "job_run_id": job_run_id,
            "job_id": job_id,
            "status": "succeeded",
            "attempt": attempt,
            "result": result,
        }

    def _next_attempt(self, job_id: str) -> int:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT status, attempt
                FROM job_runs
                WHERE job_id = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                [job_id],
            ).fetchone()
        finally:
            con.close()
        if row and row[0] == "failed":
            return int(row[1]) + 1
        return 1

    def _finish(
        self,
        *,
        job_id: str,
        job_run_id: str,
        status: str,
        next_run_at: datetime,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
        disable: bool = False,
    ) -> None:
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                UPDATE job_runs
                SET status = ?, result_json = ?, error_type = ?,
                    error_message = ?, finished_at = ?
                WHERE job_run_id = ?
                """,
                [
                    status,
                    (
                        json.dumps(result, sort_keys=True, default=str)
                        if result is not None
                        else None
                    ),
                    type(error).__name__ if error else None,
                    str(error) if error else None,
                    now,
                    job_run_id,
                ],
            )
            con.execute(
                """
                UPDATE scheduled_jobs
                SET next_run_at = ?, locked_at = NULL, locked_by = NULL,
                    enabled = CASE WHEN ? THEN FALSE ELSE enabled END,
                    updated_at = ?
                WHERE job_id = ?
                """,
                [next_run_at, disable, now, job_id],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
