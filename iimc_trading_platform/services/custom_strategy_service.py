from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect


SUPPORTED_INDICATORS = {
    "EMA",
    "SMA",
    "RSI",
    "ROC",
}

SUPPORTED_OPERATORS = {
    ">",
    "<",
    ">=",
    "<=",
    "==",
    "crosses_above",
    "crosses_below",
}

SUPPORTED_DATA_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "price",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CustomStrategyService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def create_spec(
        self,
        *,
        name: str,
        description: str,
        symbol: str,
        timeframe: str,
        indicators: list[dict[str, Any]],
        entry_rules: list[dict[str, Any]],
        exit_rules: list[dict[str, Any]],
        risk: dict[str, Any] | None = None,
        created_by: str = "chat_user",
    ) -> dict[str, Any]:
        spec = {
            "name": name,
            "description": description,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "indicators": indicators,
            "entry_rules": entry_rules,
            "exit_rules": exit_rules,
            "risk": risk or {},
        }
        validation = self._validate_spec(spec)
        missing = validation["missing_capabilities"]
        status = "draft_executable" if not missing else "requires_review"
        spec_id = f"custom_{uuid.uuid4().hex[:12]}"
        now = utc_now()

        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO custom_strategy_specs (
                    spec_id, name, description, status, spec_json,
                    validation_json, missing_capabilities_json, created_by,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    spec_id,
                    name,
                    description,
                    status,
                    json.dumps(spec, sort_keys=True),
                    json.dumps(validation, sort_keys=True),
                    json.dumps(missing, sort_keys=True),
                    created_by,
                    now,
                    now,
                ],
            )
        finally:
            con.close()

        return {
            "spec_id": spec_id,
            "status": status,
            "spec": spec,
            "validation": validation,
            "missing_capabilities": missing,
            "execution_policy": (
                "This is a validated draft spec. It may be backtested only "
                "after mapping to supported primitives or reviewed strategy "
                "plugin registration; arbitrary LLM-generated code is not "
                "executed."
            ),
        }

    def list_specs(self, limit: int = 50) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT spec_id, name, description, status, spec_json,
                       validation_json, missing_capabilities_json, created_by,
                       created_at, updated_at
                FROM custom_strategy_specs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {"custom_strategy_specs": [_spec_from_row(row) for row in rows]}

    def _validate_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        missing: list[dict[str, str]] = []
        indicators = spec["indicators"]
        entry_rules = spec["entry_rules"]
        exit_rules = spec["exit_rules"]

        for indicator in indicators:
            indicator_type = str(indicator["type"]).upper()
            source = str(indicator.get("source", "close")).lower()
            if indicator_type not in SUPPORTED_INDICATORS:
                missing.append(
                    {
                        "kind": "indicator",
                        "value": indicator_type,
                        "reason": "Indicator is not supported by the rule-spec runtime.",
                    }
                )
            if source not in SUPPORTED_DATA_FIELDS:
                missing.append(
                    {
                        "kind": "data_field",
                        "value": source,
                        "reason": "Required source field is not in supported OHLCV fields.",
                    }
                )

        for rule in [*entry_rules, *exit_rules]:
            operator = str(rule["operator"]).lower()
            if operator not in SUPPORTED_OPERATORS:
                missing.append(
                    {
                        "kind": "operator",
                        "value": operator,
                        "reason": "Rule operator is not supported by the rule-spec runtime.",
                    }
                )

        return {
            "well_formed": True,
            "supported_indicators": sorted(SUPPORTED_INDICATORS),
            "supported_operators": sorted(SUPPORTED_OPERATORS),
            "supported_data_fields": sorted(SUPPORTED_DATA_FIELDS),
            "missing_capabilities": missing,
            "requires_human_review": bool(missing),
            "can_execute_without_new_code": not missing,
        }


def _spec_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "spec_id": row[0],
        "name": row[1],
        "description": row[2],
        "status": row[3],
        "spec": json.loads(row[4]),
        "validation": json.loads(row[5]),
        "missing_capabilities": json.loads(row[6]),
        "created_by": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }
