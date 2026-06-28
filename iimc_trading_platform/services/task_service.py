from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..db import connect


TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskService:
    def __init__(
        self,
        db_path: Path,
        handlers: dict[str, TaskHandler],
    ) -> None:
        self.db_path = db_path
        self.handlers = handlers

    def submit(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        requested_by: str,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        if task_type not in self.handlers:
            raise ValueError(f"Unknown task type: {task_type}")
        if max_attempts < 1 or max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        now = utc_now()
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            if idempotency_key:
                existing = con.execute(
                    """
                    SELECT task_id
                    FROM work_tasks
                    WHERE idempotency_key = ?
                    """,
                    [idempotency_key],
                ).fetchone()
                if existing:
                    return self.get(existing[0])
            con.execute(
                """
                INSERT INTO work_tasks VALUES (
                    ?, ?, ?, ?, 'queued', NULL, NULL, NULL, ?, 0, ?, ?,
                    NULL, NULL, ?, NULL, NULL, ?
                )
                """,
                [
                    task_id,
                    task_type,
                    idempotency_key,
                    json.dumps(payload, sort_keys=True, default=str),
                    requested_by,
                    max_attempts,
                    now,
                    now,
                    now,
                ],
            )
        finally:
            con.close()
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT task_id, task_type, idempotency_key, payload_json,
                       status, result_json, error_type, error_message,
                       requested_by, attempt, max_attempts, next_attempt_at,
                       locked_at, locked_by, created_at, started_at,
                       finished_at, updated_at
                FROM work_tasks
                WHERE task_id = ?
                """,
                [task_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        return {
            "task_id": row[0],
            "task_type": row[1],
            "idempotency_key": row[2],
            "payload": json.loads(row[3]),
            "status": row[4],
            "result": json.loads(row[5]) if row[5] else None,
            "error_type": row[6],
            "error_message": row[7],
            "requested_by": row[8],
            "attempt": row[9],
            "max_attempts": row[10],
            "next_attempt_at": row[11],
            "locked_at": row[12],
            "locked_by": row[13],
            "created_at": row[14],
            "started_at": row[15],
            "finished_at": row[16],
            "updated_at": row[17],
        }

    def list(self, limit: int = 100) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT task_id
                FROM work_tasks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {"tasks": [self.get(row[0]) for row in rows]}

    def run_due(
        self,
        worker_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        results = []
        for _ in range(limit):
            task = self._claim(worker_id)
            if task is None:
                break
            results.append(self._execute(task, worker_id))
        return results

    def _claim(self, worker_id: str) -> dict[str, Any] | None:
        now = utc_now()
        stale_before = now - timedelta(minutes=15)
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                UPDATE work_tasks
                SET status = 'failed',
                    error_type = 'StaleTaskError',
                    error_message = 'Worker lock expired after final attempt',
                    locked_at = NULL, locked_by = NULL,
                    finished_at = ?, updated_at = ?
                WHERE status = 'running' AND locked_at < ?
                  AND attempt >= max_attempts
                """,
                [now, now, stale_before],
            )
            con.execute(
                """
                UPDATE work_tasks
                SET status = 'retry', locked_at = NULL, locked_by = NULL,
                    updated_at = ?
                WHERE status = 'running' AND locked_at < ?
                  AND attempt < max_attempts
                """,
                [now, stale_before],
            )
            row = con.execute(
                """
                SELECT task_id
                FROM work_tasks
                WHERE status IN ('queued', 'retry')
                  AND next_attempt_at <= ?
                  AND attempt < max_attempts
                  AND locked_at IS NULL
                ORDER BY created_at
                LIMIT 1
                """,
                [now],
            ).fetchone()
            if row is None:
                con.execute("COMMIT")
                return None
            claimed = con.execute(
                """
                UPDATE work_tasks
                SET status = 'running', locked_at = ?, locked_by = ?,
                    started_at = COALESCE(started_at, ?),
                    attempt = attempt + 1, updated_at = ?
                WHERE task_id = ? AND locked_at IS NULL
                RETURNING task_id
                """,
                [now, worker_id, now, now, row[0]],
            ).fetchone()
            con.execute("COMMIT")
            if claimed is None:
                return None
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return self.get(row[0])

    def _execute(
        self,
        task: dict[str, Any],
        worker_id: str,
    ) -> dict[str, Any]:
        handler = self.handlers[task["task_type"]]
        try:
            result = handler(task["payload"])
        except Exception as exc:
            now = utc_now()
            final = task["attempt"] >= task["max_attempts"]
            delay_seconds = min(300, 2 ** task["attempt"])
            con = connect(self.db_path)
            try:
                con.execute(
                    """
                    UPDATE work_tasks
                    SET status = ?, error_type = ?, error_message = ?,
                        next_attempt_at = ?, locked_at = NULL,
                        locked_by = NULL, finished_at = ?, updated_at = ?
                    WHERE task_id = ? AND locked_by = ?
                    """,
                    [
                        "failed" if final else "retry",
                        type(exc).__name__,
                        str(exc),
                        now + timedelta(seconds=delay_seconds),
                        now if final else None,
                        now,
                        task["task_id"],
                        worker_id,
                    ],
                )
            finally:
                con.close()
            return self.get(task["task_id"])
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE work_tasks
                SET status = 'succeeded', result_json = ?,
                    error_type = NULL, error_message = NULL,
                    locked_at = NULL, locked_by = NULL,
                    finished_at = ?, updated_at = ?
                WHERE task_id = ? AND locked_by = ?
                """,
                [
                    json.dumps(result, sort_keys=True, default=str),
                    now,
                    now,
                    task["task_id"],
                    worker_id,
                ],
            )
        finally:
            con.close()
        return self.get(task["task_id"])
