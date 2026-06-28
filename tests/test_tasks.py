from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.task_service import TaskService


class TaskServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tasks.duckdb"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_task_is_idempotent_claimed_and_persisted(self) -> None:
        service = TaskService(
            self.db_path,
            {"double": lambda payload: {"value": payload["value"] * 2}},
        )
        first = service.submit(
            task_type="double",
            payload={"value": 3},
            requested_by="test",
            idempotency_key="double:3",
        )
        repeated = service.submit(
            task_type="double",
            payload={"value": 3},
            requested_by="test",
            idempotency_key="double:3",
        )
        results = service.run_due("worker-1")

        self.assertEqual(first["task_id"], repeated["task_id"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "succeeded")
        self.assertEqual(results[0]["result"], {"value": 6})

    def test_failure_retries_then_becomes_terminal(self) -> None:
        service = TaskService(
            self.db_path,
            {
                "fail": lambda payload: (
                    (_ for _ in ()).throw(ValueError("expected failure"))
                )
            },
        )
        task = service.submit(
            task_type="fail",
            payload={},
            requested_by="test",
            max_attempts=1,
        )
        result = service.run_due("worker-1")[0]

        self.assertEqual(result["task_id"], task["task_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "ValueError")

    def test_stale_final_attempt_is_marked_failed(self) -> None:
        service = TaskService(
            self.db_path,
            {"noop": lambda payload: {"ok": True}},
        )
        task = service.submit(
            task_type="noop",
            payload={},
            requested_by="test",
            max_attempts=1,
        )
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE work_tasks
                SET status = 'running', attempt = 1,
                    locked_at = TIMESTAMP '2000-01-01 00:00:00',
                    locked_by = 'dead-worker'
                WHERE task_id = ?
                """,
                [task["task_id"]],
            )
        finally:
            con.close()

        self.assertEqual(service.run_due("recovery-worker"), [])
        recovered = service.get(task["task_id"])
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error_type"], "StaleTaskError")


if __name__ == "__main__":
    unittest.main()
