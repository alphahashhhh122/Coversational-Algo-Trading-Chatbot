from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.alert_service import AlertService


class AlertServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "alerts.duckdb"
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        initialize_database(self.db_path)
        con = connect(self.db_path)
        try:
            con.execute("UPDATE alert_rules SET enabled = FALSE")
            con.execute(
                """
                UPDATE alert_rules SET enabled = TRUE
                WHERE rule_key = 'failed_tasks_24h'
                """
            )
        finally:
            con.close()
        self.service = AlertService(self.db_path, self.artifacts)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_alert_is_deduplicated_acknowledged_and_resolved(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO work_tasks VALUES (
                    'task_failed', 'test', NULL, '{}', 'failed',
                    NULL, 'RuntimeError', 'failure', 'test',
                    1, 1, ?, NULL, NULL, ?, ?, ?, ?
                )
                """,
                [now, now, now, now, now],
            )
        finally:
            con.close()

        first = self.service.evaluate(actor="test")
        second = self.service.evaluate(actor="test")
        alerts = self.service.list()["alerts"]

        self.assertEqual(first["active_count"], 1)
        self.assertEqual(second["active_count"], 1)
        self.assertEqual(len(alerts), 1)
        acknowledged = self.service.acknowledge(
            alerts[0]["alert_id"],
            actor="operator",
            reason="Investigating worker failure",
        )
        self.assertEqual(acknowledged["status"], "acknowledged")

        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM work_tasks WHERE task_id = 'task_failed'"
            )
        finally:
            con.close()
        resolved = self.service.evaluate(actor="test")

        self.assertEqual(resolved["active_count"], 0)
        self.assertEqual(resolved["acknowledged_count"], 0)
        history = self.service.list(
            include_resolved=True
        )["alerts"]
        self.assertEqual(history[0]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
