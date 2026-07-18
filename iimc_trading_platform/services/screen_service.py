"""Versioned fundamental screens evaluated deterministically.

Screens are stored configurations (never hardcoded code paths) whose
criteria reference ratio names produced by FundamentalsService. Running a
screen evaluates every symbol with imported statements and reports matches
with the actual values — nothing is fabricated for missing data.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .fundamentals_service import FundamentalsService

_OPS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}

DEFAULT_SCREENS: list[dict[str, Any]] = [
    {
        "name": "quality",
        "description": "Profitable, well-capitalized companies.",
        "criteria": [
            {"metric": "roe", "op": "gte", "value": 0.15},
            {"metric": "debt_to_equity", "op": "lte", "value": 1.0},
            {"metric": "net_margin", "op": "gte", "value": 0.08},
        ],
    },
    {
        "name": "growth",
        "description": "Expanding revenue and earnings.",
        "criteria": [
            {"metric": "revenue_growth", "op": "gte", "value": 0.10},
            {"metric": "earnings_growth", "op": "gte", "value": 0.10},
        ],
    },
    {
        "name": "low_leverage",
        "description": "Conservative balance sheets with liquidity.",
        "criteria": [
            {"metric": "debt_to_equity", "op": "lte", "value": 0.5},
            {"metric": "current_ratio", "op": "gte", "value": 1.5},
        ],
    },
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ScreenService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def ensure_defaults(self, created_by: str = "system_seed") -> None:
        for screen in DEFAULT_SCREENS:
            if not self._latest_definition(screen["name"]):
                self.save_definition(
                    name=screen["name"],
                    description=screen["description"],
                    criteria=screen["criteria"],
                    created_by=created_by,
                )

    def save_definition(
        self,
        *,
        name: str,
        description: str,
        criteria: list[dict[str, Any]],
        created_by: str,
    ) -> dict[str, Any]:
        name = name.strip().lower().replace(" ", "_")
        if not name:
            raise ValueError("Screen name is required")
        if not criteria:
            raise ValueError("Provide at least one criterion")
        for item in criteria:
            if item.get("op") not in _OPS:
                raise ValueError(
                    f"Unsupported operator {item.get('op')!r}; "
                    f"use one of {sorted(_OPS)}"
                )
            if not item.get("metric"):
                raise ValueError("Each criterion needs a 'metric'")
            float(item["value"])
        latest = self._latest_definition(name)
        version = (latest["version"] + 1) if latest else 1
        screen_id = f"screen_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO screen_definitions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    screen_id,
                    name,
                    version,
                    description,
                    json.dumps(criteria, sort_keys=True),
                    created_by,
                    utc_now(),
                ],
            )
        finally:
            con.close()
        return {"screen_id": screen_id, "name": name, "version": version}

    def list_definitions(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT name, MAX(version), ANY_VALUE(description)
                FROM screen_definitions
                GROUP BY name ORDER BY name
                """
            ).fetchall()
        finally:
            con.close()
        return {
            "screens": [
                {
                    "name": row[0],
                    "latest_version": row[1],
                    "description": row[2],
                }
                for row in rows
            ]
        }

    def run(self, name: str) -> dict[str, Any]:
        definition = self._latest_definition(name.strip().lower())
        if not definition:
            available = ", ".join(
                item["name"]
                for item in self.list_definitions()["screens"]
            ) or "none"
            raise ValueError(
                f"Screen not found: {name!r}. Available: {available}."
            )
        fundamentals = FundamentalsService(self.db_path)
        con = connect(self.db_path)
        try:
            symbols = [
                row[0]
                for row in con.execute(
                    "SELECT DISTINCT symbol FROM financial_statements "
                    "ORDER BY symbol"
                ).fetchall()
            ]
        finally:
            con.close()
        matches: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for symbol in symbols:
            analysis = fundamentals.analyze(symbol)
            ratio_values = {
                item["name"]: item["value"]
                for item in analysis["ratios"]
                if item["value"] is not None
            }
            failed: list[str] = []
            missing: list[str] = []
            for criterion in definition["criteria"]:
                value = ratio_values.get(criterion["metric"])
                if value is None:
                    missing.append(criterion["metric"])
                elif not _OPS[criterion["op"]](
                    value, float(criterion["value"])
                ):
                    failed.append(
                        f"{criterion['metric']}={value:.4f} not "
                        f"{criterion['op']} {criterion['value']}"
                    )
            entry = {
                "symbol": symbol,
                "values": {
                    criterion["metric"]: ratio_values.get(criterion["metric"])
                    for criterion in definition["criteria"]
                },
                "missing_metrics": missing,
                "failed_criteria": failed,
            }
            if not failed and not missing:
                matches.append(entry)
            else:
                excluded.append(entry)
        return {
            "screen": definition["name"],
            "version": definition["version"],
            "criteria": definition["criteria"],
            "universe_size": len(symbols),
            "matches": matches,
            "excluded": excluded,
            "no_synthetic_fallback": True,
        }

    def _latest_definition(self, name: str) -> dict[str, Any] | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT name, version, description, criteria_json
                FROM screen_definitions
                WHERE name = ? ORDER BY version DESC LIMIT 1
                """,
                [name],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {
            "name": row[0],
            "version": row[1],
            "description": row[2],
            "criteria": json.loads(row[3]),
        }
