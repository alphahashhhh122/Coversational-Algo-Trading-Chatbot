from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.job_service import JobService


class JobServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.duckdb"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_due_job_runs_and_schedules_next_execution(self) -> None:
        service = JobService(
            self.db_path,
            {"ok": lambda payload: {"value": payload["value"]}},
        )
        job_id = service.register(
            name="successful_job",
            job_type="ok",
            schedule_seconds=60,
            payload={"value": 7},
        )

        results = service.run_due("worker_1")

        self.assertEqual(results[0]["status"], "succeeded")
        self.assertEqual(results[0]["result"], {"value": 7})
        self.assertEqual(service.run_due("worker_1"), [])
        jobs = service.list_jobs()["jobs"]
        self.assertEqual(jobs[0]["job_id"], job_id)
        self.assertEqual(jobs[0]["last_status"], "succeeded")

    def test_repeated_failure_disables_job_at_retry_limit(self) -> None:
        def fail(payload):
            raise RuntimeError("source unavailable")

        service = JobService(self.db_path, {"fail": fail})
        job_id = service.register(
            name="failing_job",
            job_type="fail",
            schedule_seconds=60,
            max_retries=2,
        )

        first = service.run_now(job_id, "worker_1")
        second = service.run_now(job_id, "worker_1")

        self.assertEqual(first["attempt"], 1)
        self.assertEqual(second["attempt"], 2)
        con = connect(self.db_path)
        try:
            enabled = con.execute(
                "SELECT enabled FROM scheduled_jobs WHERE job_id = ?",
                [job_id],
            ).fetchone()[0]
        finally:
            con.close()
        self.assertFalse(enabled)
        with self.assertRaisesRegex(ValueError, "Enabled job not found"):
            service.run_now(job_id, "worker_1")


if __name__ == "__main__":
    unittest.main()
