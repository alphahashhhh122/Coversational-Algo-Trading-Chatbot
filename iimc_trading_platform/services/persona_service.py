from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..db import connect


class PersonaService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def list(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT
                    persona_id,
                    name,
                    description,
                    asset_classes_json,
                    strategy_bias_json,
                    risk_rules_json,
                    dashboard_focus_json,
                    prompt_guidance,
                    enabled,
                    created_at,
                    updated_at
                FROM strategy_personas
                WHERE enabled = TRUE
                ORDER BY name
                """
            ).fetchall()
        finally:
            con.close()
        return {"personas": [_persona_from_row(row) for row in rows]}

    def get(self, persona_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT
                    persona_id,
                    name,
                    description,
                    asset_classes_json,
                    strategy_bias_json,
                    risk_rules_json,
                    dashboard_focus_json,
                    prompt_guidance,
                    enabled,
                    created_at,
                    updated_at
                FROM strategy_personas
                WHERE persona_id = ? AND enabled = TRUE
                """,
                [persona_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError("Persona not found")
        return {"persona": _persona_from_row(row)}


def _persona_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "persona_id": row[0],
        "name": row[1],
        "description": row[2],
        "asset_classes": json.loads(row[3]),
        "strategy_bias": json.loads(row[4]),
        "risk_rules": json.loads(row[5]),
        "dashboard_focus": json.loads(row[6]),
        "prompt_guidance": row[7],
        "enabled": bool(row[8]),
        "created_at": row[9],
        "updated_at": row[10],
    }
