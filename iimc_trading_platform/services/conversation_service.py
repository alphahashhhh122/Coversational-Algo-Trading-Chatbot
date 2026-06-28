from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConversationService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def ensure_session(self, session_id: str) -> None:
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO chat_sessions (
                    session_id, title, user_id, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [session_id, None, None, "active", now, now],
            )
            con.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
                [now, session_id],
            )
        finally:
            con.close()

    def append(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO chat_messages (
                    message_id, session_id, role, content,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    message_id,
                    session_id,
                    role,
                    content,
                    json.dumps(metadata or {}, sort_keys=True, default=str),
                    utc_now(),
                ],
            )
        finally:
            con.close()
        return message_id

    def history(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT role, content, metadata_json, created_at
                FROM (
                    SELECT role, content, metadata_json, created_at
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                ORDER BY created_at
                """,
                [session_id, limit],
            ).fetchall()
        finally:
            con.close()
        return [
            {
                "role": row[0],
                "content": row[1],
                "metadata": json.loads(row[2]),
                "created_at": row[3],
            }
            for row in rows
        ]

