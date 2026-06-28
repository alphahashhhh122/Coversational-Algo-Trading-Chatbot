from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.retention_service import (
    PURGE_CONFIRMATION,
    RetentionService,
)


class RetentionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "retention.duckdb"
        initialize_database(self.db_path)
        self.service = RetentionService(self.db_path)
        old = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=365)
        )
        recent = datetime.now(timezone.utc).replace(tzinfo=None)
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO chat_sessions VALUES
                ('old_session', 'Old', NULL, 'active', ?, ?),
                ('recent_session', 'Recent', NULL, 'active', ?, ?)
                """,
                [old, old, recent, recent],
            )
            con.execute(
                """
                INSERT INTO chat_messages VALUES
                ('old_message', 'old_session', 'user', 'old', '{}', ?),
                ('recent_message', 'recent_session', 'user', 'new', '{}', ?)
                """,
                [old, recent],
            )
            con.execute(
                """
                INSERT INTO strategy_definitions VALUES (
                    'protected_strategy', 'protected_strategy', '1.0.0',
                    'Must not be purged', '{}', TRUE, ?
                )
                """,
                [old],
            )
        finally:
            con.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_preview_is_non_destructive(self) -> None:
        preview = self.service.preview(
            actor="test",
            policy_names=["inactive_chat_history"],
        )

        self.assertEqual(
            preview["candidate_counts"]["inactive_chat_history"],
            {"chat_sessions": 1, "chat_messages": 1},
        )
        con = connect(self.db_path)
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM chat_sessions"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 2)

    def test_execute_requires_confirmation_and_preserves_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "Confirmation"):
            self.service.execute(
                actor="test",
                confirmation="wrong",
                policy_names=["inactive_chat_history"],
            )

        result = self.service.execute(
            actor="admin",
            confirmation=PURGE_CONFIRMATION,
            policy_names=["inactive_chat_history"],
        )

        self.assertEqual(
            result["deleted_counts"]["inactive_chat_history"],
            {"chat_sessions": 1, "chat_messages": 1},
        )
        con = connect(self.db_path)
        try:
            sessions = con.execute(
                "SELECT session_id FROM chat_sessions"
            ).fetchall()
            strategy_count = con.execute(
                """
                SELECT COUNT(*) FROM strategy_definitions
                WHERE strategy_id = 'protected_strategy'
                """
            ).fetchone()[0]
            audit_count = con.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE entity_id = ?
                """,
                [result["retention_run_id"]],
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(sessions, [("recent_session",)])
        self.assertEqual(strategy_count, 1)
        self.assertEqual(audit_count, 1)


if __name__ == "__main__":
    unittest.main()
