from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..infrastructure.openalgo import OpenAlgoClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OpenAlgoSnapshotService:
    def __init__(
        self,
        db_path: Path,
        client: OpenAlgoClient | None = None,
    ) -> None:
        self.db_path = db_path
        self.client = client

    def capture(self, snapshot_type: str) -> dict[str, Any]:
        if self.client is None:
            raise ValueError(
                "OpenAlgo credentials are required to capture a snapshot"
            )
        response = self.client.account_snapshot(snapshot_type)
        snapshot_id = f"oas_{uuid.uuid4().hex[:12]}"
        captured_at = utc_now()
        data = response.get("data")
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO openalgo_snapshots (
                    snapshot_id, snapshot_type, source_table,
                    payload_json, captured_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    snapshot_id,
                    snapshot_type,
                    f"openalgo_api:{snapshot_type}",
                    json.dumps(data, sort_keys=True, default=str),
                    captured_at,
                ],
            )
        finally:
            con.close()
        return {
            "snapshot_id": snapshot_id,
            "snapshot_type": snapshot_type,
            "source": "openalgo",
            "captured_at": captured_at,
            "data": data,
        }

    def list(self, limit: int = 100) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT snapshot_id, snapshot_type, source_table,
                       payload_json, captured_at
                FROM openalgo_snapshots
                ORDER BY captured_at DESC, snapshot_id DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "configured": self.client is not None,
            "snapshots": [
                {
                    "snapshot_id": row[0],
                    "snapshot_type": row[1],
                    "source": row[2],
                    "data": json.loads(row[3]),
                    "captured_at": row[4],
                }
                for row in rows
            ],
        }
