from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..evaluator import ResponseEvaluator
from ..orchestration import Orchestrator
from ..tools.registry import build_default_tool_registry


class AiEvaluationService:
    def __init__(
        self,
        db_path: Path,
        artifacts_dir: Path,
        cases_path: Path,
    ) -> None:
        self.db_path = db_path
        self.artifacts_dir = artifacts_dir
        self.cases_path = cases_path

    def run(
        self,
        *,
        orchestrator: Orchestrator,
        model: str | None,
        created_by: str,
    ) -> dict[str, Any]:
        raw_cases = self.cases_path.read_bytes()
        cases = [
            json.loads(line)
            for line in raw_cases.decode("utf-8").splitlines()
            if line.strip()
        ]
        self._validate_case_ids(cases)
        case_set_sha256 = hashlib.sha256(raw_cases).hexdigest()
        eval_run_id = f"eval_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        registry = build_default_tool_registry(
            self.db_path,
            artifacts_dir=self.artifacts_dir,
        )
        evaluator = ResponseEvaluator()
        results = []
        for case in cases:
            case_started = time.perf_counter()
            try:
                if case["case_type"] == "routing":
                    actual, passed = self._evaluate_routing(
                        case,
                        orchestrator,
                        registry,
                    )
                elif case["case_type"] == "response":
                    actual, passed = self._evaluate_response(
                        case,
                        evaluator,
                    )
                else:
                    raise ValueError(
                        f"Unknown case type: {case['case_type']}"
                    )
                error_message = None
            except Exception as exc:
                actual = {"exception_type": type(exc).__name__}
                passed = False
                error_message = str(exc)
            results.append(
                {
                    "case_id": case["case_id"],
                    "case_type": case["case_type"],
                    "category": case["category"],
                    "role": case.get("role"),
                    "passed": passed,
                    "expected": case.get("expected", {}),
                    "actual": actual,
                    "error_message": error_message,
                    "duration_ms": round(
                        (time.perf_counter() - case_started) * 1000,
                        3,
                    ),
                }
            )
        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        passed_count = sum(1 for item in results if item["passed"])
        failed_count = len(results) - passed_count
        report = {
            "eval_run_id": eval_run_id,
            "case_set_sha256": case_set_sha256,
            "orchestration_mode": orchestrator.mode,
            "model": model,
            "status": "passed" if failed_count == 0 else "failed",
            "case_count": len(results),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "pass_rate": round(
                passed_count / len(results) if results else 0.0,
                6,
            ),
            "category_scores": self._category_scores(results),
            "started_at": started_at,
            "finished_at": finished_at,
            "results": results,
        }
        report_dir = self.artifacts_dir / "evaluations"
        report_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = report_dir / f"{eval_run_id}.json"
        artifact_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        self._persist(
            report,
            artifact_path=artifact_path,
            created_by=created_by,
        )
        return {
            key: value
            for key, value in report.items()
            if key != "results"
        } | {
            "artifact_path": str(artifact_path.resolve()),
            "failed_cases": [
                item["case_id"]
                for item in results
                if not item["passed"]
            ],
        }

    def list(self, limit: int = 50) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT eval_run_id, case_set_sha256, case_count,
                       passed_count, failed_count, pass_rate,
                       orchestration_mode, model, status, artifact_path,
                       created_by, started_at, finished_at
                FROM ai_eval_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "evaluations": [
                {
                    "eval_run_id": row[0],
                    "case_set_sha256": row[1],
                    "case_count": row[2],
                    "passed_count": row[3],
                    "failed_count": row[4],
                    "pass_rate": row[5],
                    "orchestration_mode": row[6],
                    "model": row[7],
                    "status": row[8],
                    "artifact_path": row[9],
                    "created_by": row[10],
                    "started_at": row[11],
                    "finished_at": row[12],
                }
                for row in rows
            ]
        }

    @staticmethod
    def _evaluate_routing(
        case: dict[str, Any],
        orchestrator: Orchestrator,
        registry,
    ) -> tuple[dict[str, Any], bool]:
        role = case.get("role", "viewer")
        allowed = registry.allowed_for_role(role)
        active_registry = registry.subset(allowed)
        decision = orchestrator.select_tool(
            case["message"],
            case.get("history", []),
            active_registry,
        )
        schema_valid = True
        schema_error = None
        if decision.tool_name:
            try:
                active_registry.get(decision.tool_name).validate(
                    decision.arguments
                )
            except Exception as exc:
                schema_valid = False
                schema_error = str(exc)
        expected = case["expected"]
        expected_tools = expected.get("tools", [])
        tool_matches = (
            decision.tool_name in expected_tools
            if expected_tools
            else decision.tool_name is None
        )
        arguments_match = _contains_subset(
            decision.arguments,
            expected.get("arguments_subset", {}),
        )
        forbidden_tools = set(expected.get("forbidden_tools", []))
        authorization_passed = (
            not forbidden_tools.intersection(allowed)
            and decision.tool_name not in forbidden_tools
        )
        actual = {
            "tool_name": decision.tool_name,
            "arguments": decision.arguments,
            "direct_response": decision.direct_response,
            "schema_valid": schema_valid,
            "schema_error": schema_error,
            "authorization_passed": authorization_passed,
        }
        return (
            actual,
            tool_matches
            and arguments_match
            and schema_valid
            and authorization_passed
            and all(
                fragment in (decision.direct_response or "")
                for fragment in expected.get(
                    "direct_response_contains",
                    [],
                )
            )
        )

    @staticmethod
    def _evaluate_response(
        case: dict[str, Any],
        evaluator: ResponseEvaluator,
    ) -> tuple[dict[str, Any], bool]:
        value = evaluator.evaluate(**case["input"])
        expected = case["expected"]
        warnings_match = set(
            expected.get("warnings_include", [])
        ).issubset(value.warnings)
        answer_match = all(
            fragment in value.answer
            for fragment in expected.get("answer_contains", [])
        )
        actual = {
            "passed": value.passed,
            "warnings": value.warnings,
            "answer": value.answer,
            "evidence_ids": value.evidence_ids,
        }
        return (
            actual,
            value.passed == expected["passed"]
            and warnings_match
            and answer_match,
        )

    @staticmethod
    def _category_scores(
        results: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[bool]] = defaultdict(list)
        for item in results:
            grouped[item["category"]].append(item["passed"])
        return {
            category: {
                "case_count": len(values),
                "passed_count": sum(values),
                "pass_rate": round(sum(values) / len(values), 6),
            }
            for category, values in sorted(grouped.items())
        }

    @staticmethod
    def _validate_case_ids(cases: list[dict[str, Any]]) -> None:
        case_ids = [case["case_id"] for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("AI evaluation case IDs must be unique")

    def _persist(
        self,
        report: dict[str, Any],
        *,
        artifact_path: Path,
        created_by: str,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                INSERT INTO ai_eval_runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    report["eval_run_id"],
                    report["case_set_sha256"],
                    report["case_count"],
                    report["passed_count"],
                    report["failed_count"],
                    report["pass_rate"],
                    report["orchestration_mode"],
                    report["model"],
                    report["status"],
                    str(artifact_path.resolve()),
                    created_by,
                    report["started_at"],
                    report["finished_at"],
                ],
            )
            for item in report["results"]:
                con.execute(
                    """
                    INSERT INTO ai_eval_results VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        report["eval_run_id"],
                        item["case_id"],
                        item["case_type"],
                        item["category"],
                        item["role"],
                        item["passed"],
                        json.dumps(item["expected"], sort_keys=True),
                        json.dumps(
                            item["actual"],
                            sort_keys=True,
                            default=str,
                        ),
                        item["error_message"],
                        item["duration_ms"],
                    ],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()


def _contains_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return actual == expected
    return actual == expected
