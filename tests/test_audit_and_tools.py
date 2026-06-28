from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.domain import ToolCallStatus
from iimc_trading_platform.infrastructure import (
    DuckDBAuditRepository,
    DuckDBToolCallRepository,
    initialize_database,
)
from iimc_trading_platform.services import AuditService, ToolExecutionService
from iimc_trading_platform.services.tool_execution_service import (
    ToolExecutionError,
)


class AuditAndToolExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.duckdb"
        initialize_database(self.db_path)
        self.audit_repository = DuckDBAuditRepository(self.db_path)
        self.tool_repository = DuckDBToolCallRepository(self.db_path)
        self.audit_service = AuditService(self.audit_repository)
        self.tool_service = ToolExecutionService(
            self.tool_repository,
            self.audit_service,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_successful_tool_call_is_persisted_and_audited(self) -> None:
        tool_call_id, result = self.tool_service.execute(
            tool_name="add",
            request={"left": 2, "right": 3},
            handler=lambda: {"value": 5},
            session_id="session_1",
        )

        stored = self.tool_repository.get(tool_call_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, ToolCallStatus.SUCCEEDED)
        self.assertEqual(result, {"value": 5})

        history = self.audit_service.history("tool_call", tool_call_id)
        self.assertEqual([event.action for event in history], ["started", "succeeded"])

    def test_failed_tool_call_is_persisted_and_audited(self) -> None:
        def fail() -> None:
            raise ValueError("invalid request")

        with self.assertRaisesRegex(
            ToolExecutionError,
            "invalid request",
        ) as captured:
            self.tool_service.execute(
                tool_name="failing_tool",
                request={"value": "bad"},
                handler=fail,
            )

        self.assertIsInstance(captured.exception.cause, ValueError)
        history = self.audit_repository.list_for_entity(
            "tool_call",
            captured.exception.tool_call_id,
        )
        self.assertEqual([event.action for event in history], ["started", "failed"])

        stored = self.tool_repository.get(history[0].entity_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, ToolCallStatus.FAILED)
        self.assertEqual(stored.error_message, "invalid request")

    def _latest_id(self) -> str:
        from iimc_trading_platform.db import connect

        con = connect(self.db_path)
        try:
            return con.execute(
                """
                SELECT tool_call_id
                FROM tool_calls
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()[0]
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
