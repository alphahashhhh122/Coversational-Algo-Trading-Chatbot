from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..domain import ExecutionMode
from ..strategies.rule_spec import rule_spec_capabilities, validate_rule_spec
from .market_data_ingestion_service import SUPPORTED_ASSET_CLASSES
from .backtest_service import BacktestService


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
        feature_inputs: list[dict[str, Any]] | None = None,
        risk: dict[str, Any] | None = None,
        position_side: str = "long",
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
            "feature_inputs": feature_inputs or [],
            "risk": risk or {},
            "position_side": position_side.lower(),
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
                "This governed spec can be backtested by the native rule-spec "
                "runtime when validation passes. Unsupported primitives remain "
                "requires_review; arbitrary LLM-generated code is not executed."
            ),
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            **rule_spec_capabilities(),
            "data_contracts": {
                "rule_backtesting": {
                    "required_fields": [
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                    ],
                    "supported_asset_classes": sorted(
                        SUPPORTED_ASSET_CLASSES
                    ),
                    "storage_paths": ["market_ohlcv", "options_ohlcv"],
                },
                "plain_options_ohlcv": {
                    "supported": True,
                    "notes": (
                        "Plain option premium OHLCV can run rule strategies; "
                        "IV/OI/expiry/strike surface features require the "
                        "specialized options ingestion workflow."
                    ),
                },
                "governed_feature_series": {
                    "supported": True,
                    "storage_path": "market_features",
                    "point_in_time_alignment": "asof only",
                    "required_feature_input": [
                        "name",
                        "dataset_id",
                        "feature_name",
                        "max_age_hours",
                    ],
                    "examples": [
                        "iv_skew",
                        "open_interest",
                        "earnings_surprise",
                        "news_sentiment",
                    ],
                },
                "unsupported_without_new_implementation": [
                    "arbitrary Python or generated strategy code",
                    "raw unstructured documents without a governed numeric "
                    "feature transform",
                ],
            },
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

    def get_spec(self, spec_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT spec_id, name, description, status, spec_json,
                       validation_json, missing_capabilities_json, created_by,
                       created_at, updated_at
                FROM custom_strategy_specs
                WHERE spec_id = ?
                """,
                [spec_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(f"Custom strategy spec not found: {spec_id}")
        return _spec_from_row(row)

    def run_backtest(
        self,
        *,
        spec_id: str,
        dataset_id: str,
        execution_mode: ExecutionMode = ExecutionMode.RESEARCH,
        requested_quantity: int = 1,
        starting_equity: float = 1_000_000.0,
        fee_bps: float = 1.0,
        slippage_bps: float = 0.0,
        instrument: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.get_spec(spec_id)
        validation = self._validate_spec(record["spec"])
        if validation != record["validation"]:
            self._refresh_validation(spec_id, validation)
        if validation["missing_capabilities"]:
            raise ValueError(
                "Custom strategy spec requires review before execution: "
                f"{validation['missing_capabilities']}"
            )
        result = BacktestService(self.db_path).run(
            strategy_name="rule_spec",
            dataset_id=dataset_id,
            parameters={"spec": record["spec"]},
            execution_mode=execution_mode,
            requested_quantity=requested_quantity,
            starting_equity=starting_equity,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            instrument=instrument,
        )
        return {
            **result,
            "custom_strategy_spec_id": spec_id,
            "strategy": "rule_spec",
            "execution_policy": (
                "Executed by the native deterministic rule-spec runtime; "
                "no generated code was evaluated."
            ),
        }

    def _validate_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        validation = validate_rule_spec(spec)
        missing = list(validation["missing_capabilities"])
        for feature in validation.get("external_feature_inputs", []):
            dataset_id = feature.get("dataset_id")
            if not isinstance(dataset_id, str) or not dataset_id:
                continue
            if not self._feature_dataset_exists(dataset_id):
                missing.append(
                    {
                        "kind": "feature_dataset",
                        "value": dataset_id,
                        "reason": "Import this governed feature-series dataset before execution.",
                    }
                )
        return {
            **validation,
            "well_formed": not missing,
            "missing_capabilities": missing,
            "requires_human_review": bool(missing),
            "can_execute_without_new_code": not missing,
        }

    def _feature_dataset_exists(self, dataset_id: str) -> bool:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT 1
                FROM data_catalog
                WHERE dataset_id = ?
                  AND storage_table = 'market_features'
                  AND quality_status = 'clean'
                """,
                [dataset_id],
            ).fetchone()
        finally:
            con.close()
        return row is not None

    def _refresh_validation(
        self,
        spec_id: str,
        validation: dict[str, Any],
    ) -> None:
        missing = validation["missing_capabilities"]
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE custom_strategy_specs
                SET status = ?, validation_json = ?, missing_capabilities_json = ?,
                    updated_at = ?
                WHERE spec_id = ?
                """,
                [
                    "draft_executable" if not missing else "requires_review",
                    json.dumps(validation, sort_keys=True),
                    json.dumps(missing, sort_keys=True),
                    utc_now(),
                    spec_id,
                ],
            )
        finally:
            con.close()

    def validate_spec(
        self,
        *,
        name: str,
        description: str,
        symbol: str,
        timeframe: str,
        indicators: list[dict[str, Any]],
        entry_rules: list[dict[str, Any]],
        exit_rules: list[dict[str, Any]],
        feature_inputs: list[dict[str, Any]] | None = None,
        risk: dict[str, Any] | None = None,
        position_side: str = "long",
    ) -> dict[str, Any]:
        spec = {
            "name": name,
            "description": description,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "indicators": indicators,
            "entry_rules": entry_rules,
            "exit_rules": exit_rules,
            "feature_inputs": feature_inputs or [],
            "risk": risk or {},
            "position_side": position_side.lower(),
        }
        validation = self._validate_spec(spec)
        return {
            "spec": spec,
            "validation": validation,
            "missing_capabilities": validation["missing_capabilities"],
            "can_execute_without_new_code": validation[
                "can_execute_without_new_code"
            ],
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
