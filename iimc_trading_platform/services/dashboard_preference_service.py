from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect


ALLOWED_DASHBOARD_WIDGETS = {
    "research",
    "assets",
    "backtests",
    "openalgo",
    "risk",
    "execution",
    "news",
}
DEFAULT_DASHBOARD_WIDGETS = [
    "research",
    "assets",
    "backtests",
    "openalgo",
    "risk",
    "execution",
]


class DashboardPreferenceService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def get(self, principal_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT widgets_json, auto_refresh, updated_at
                FROM dashboard_preferences
                WHERE principal_id = ?
                """,
                [principal_id],
            ).fetchone()
        finally:
            con.close()
        if not row:
            return {
                "principal_id": principal_id,
                "widgets": list(DEFAULT_DASHBOARD_WIDGETS),
                "auto_refresh": False,
                "source": "default",
                "updated_at": None,
            }
        return {
            "principal_id": principal_id,
            "widgets": sanitize_widgets(json.loads(row[0])),
            "auto_refresh": bool(row[1]),
            "source": "stored",
            "updated_at": row[2],
        }

    def update(
        self,
        principal_id: str,
        *,
        widgets: list[str],
        auto_refresh: bool,
    ) -> dict[str, Any]:
        clean_widgets = sanitize_widgets(widgets)
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO dashboard_preferences VALUES (?, ?, ?, ?)
                ON CONFLICT (principal_id) DO UPDATE SET
                    widgets_json = EXCLUDED.widgets_json,
                    auto_refresh = EXCLUDED.auto_refresh,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    principal_id,
                    json.dumps(clean_widgets, sort_keys=True),
                    auto_refresh,
                    now,
                ],
            )
        finally:
            con.close()
        return {
            "principal_id": principal_id,
            "widgets": clean_widgets,
            "auto_refresh": auto_refresh,
            "source": "stored",
            "updated_at": now,
        }


def sanitize_widgets(widgets: list[str]) -> list[str]:
    clean: list[str] = []
    for widget in widgets:
        if widget not in ALLOWED_DASHBOARD_WIDGETS:
            raise ValueError(f"Unsupported dashboard widget: {widget}")
        if widget not in clean:
            clean.append(widget)
    return clean


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
