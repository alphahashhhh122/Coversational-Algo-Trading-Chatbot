from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    answer: str
    warnings: list[str]
    evidence_ids: list[str]


class ResponseEvaluator:
    GUARANTEE_PATTERNS = [
        r"\bguaranteed?\b",
        r"\brisk[- ]free\b",
        r"\bwill definitely profit\b",
        r"\b100%\s+(return|profit|gain)\b",
    ]

    def evaluate(
        self,
        *,
        answer: str,
        tool_name: str | None,
        tool_result: dict[str, Any] | None,
        tool_call_id: str | None,
    ) -> EvaluationResult:
        warnings: list[str] = []
        evidence_ids = [
            value
            for value in [
                tool_call_id,
                _string_value(tool_result, "run_id"),
                _string_value(tool_result, "dataset_id"),
                _string_value(tool_result, "intent_id"),
                _string_value(tool_result, "approval_id"),
                _string_value(tool_result, "order_id"),
                _string_value(tool_result, "broker_order_id"),
                _string_value(tool_result, "experiment_id"),
                _string_value(tool_result, "portfolio_id"),
                _string_value(tool_result, "task_id"),
                _string_value(
                    tool_result,
                    "reconciliation_snapshot_id",
                ),
            ]
            if value
        ]
        evidence_ids.extend(_retrieval_evidence(tool_result))

        if any(
            re.search(pattern, answer, flags=re.IGNORECASE)
            for pattern in self.GUARANTEE_PATTERNS
        ):
            warnings.append("financial_guarantee_language")

        if tool_name and (tool_result is None or tool_call_id is None):
            warnings.append("missing_tool_evidence")

        # Evidence identifiers are returned in evidence_ids (persisted and
        # shown in the UI evidence panel) rather than injected into the
        # conversational answer text.

        if tool_name == "run_backtest" and tool_result:
            warnings.extend(_validate_backtest_metrics(answer, tool_result))
            if "historical" not in answer.lower():
                answer += (
                    "\n\nThis is a historical simulation, not a guarantee of "
                    "future performance."
                )

        if warnings:
            answer = _safe_replacement(tool_name, tool_result, evidence_ids)
        return EvaluationResult(
            passed=not warnings,
            answer=answer,
            warnings=warnings,
            evidence_ids=evidence_ids,
        )


def _validate_backtest_metrics(
    answer: str,
    result: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    checks = {
        "net_pnl": _first_number(result, metrics, "net_pnl"),
        "max_drawdown": _first_number(result, metrics, "max_drawdown"),
        "return_pct": _first_number(result, metrics, "return_pct"),
        "total_trades": _first_number(result, metrics, "total_trades"),
        "winning_trades": _first_number(result, metrics, "winning_trades"),
        "losing_trades": _first_number(result, metrics, "losing_trades"),
        "gross_profit": _first_number(result, metrics, "gross_profit"),
        "gross_loss": _first_number(result, metrics, "gross_loss"),
        "total_fees": _first_number(result, metrics, "total_fees"),
        "sharpe_ratio": _first_number(result, metrics, "sharpe_ratio"),
        "profit_factor": _first_number(result, metrics, "profit_factor"),
    }
    for name, expected in checks.items():
        if expected is None:
            continue
        match = _metric_match(answer, name)
        if match:
            claimed = float(match.group(1).replace(",", ""))
            if abs(claimed - float(expected)) > max(
                0.01,
                abs(float(expected)) * 0.005,
            ):
                warnings.append(f"ungrounded_metric:{name}")
    return warnings


def _first_number(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    key: str,
) -> float | int | None:
    for payload in (primary, secondary):
        value = payload.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return value
        try:
            return float(str(value).replace(",", ""))
        except ValueError:
            continue
    return None


def _metric_match(answer: str, name: str) -> re.Match[str] | None:
    aliases = {
        "net_pnl": [r"net\s*p\s*&?\s*l", r"net\s*pnl", r"p\s*&?\s*l"],
        "max_drawdown": [r"max(?:imum)?\s*drawdown", r"drawdown"],
        "return_pct": [r"return\s*(?:pct|percent|%)?", r"return_pct"],
        "total_trades": [r"total\s*trades", r"closed\s*trades", r"trades"],
        "winning_trades": [r"winning\s*trades", r"wins"],
        "losing_trades": [r"losing\s*trades", r"losses"],
        "gross_profit": [r"gross\s*profit"],
        "gross_loss": [r"gross\s*loss"],
        "total_fees": [r"total\s*fees", r"fees"],
        "sharpe_ratio": [r"sharpe(?:\s*ratio)?"],
        "profit_factor": [r"profit\s*factor"],
    }
    labels = aliases.get(name, [name.replace("_", r"[\s_]*")])
    value_pattern = r"\s*[:=]?\s*[\u20b9$]?\s*(-?[\d,]+(?:\.\d+)?)"
    for label in labels:
        match = re.search(rf"\b{label}\b{value_pattern}", answer, flags=re.IGNORECASE)
        if match:
            return match
    return None


def _safe_replacement(
    tool_name: str | None,
    tool_result: dict[str, Any] | None,
    evidence_ids: list[str],
) -> str:
    if tool_name == "run_backtest" and tool_result:
        answer = (
            f"Backtest {tool_result.get('run_id')} completed. "
            f"Net P&L: {tool_result.get('net_pnl')}; "
            f"max drawdown: {tool_result.get('max_drawdown')}; "
            f"return: {tool_result.get('return_pct')}%; "
            f"closed trades: {tool_result.get('total_trades')}."
        )
    else:
        answer = (
            "The response was replaced because it did not satisfy the "
            "platform's grounding or safety checks."
        )
        if tool_result is not None:
            answer += (
                " Verified tool result: "
                f"{json.dumps(tool_result, default=str)}"
            )
    return answer


def _string_value(
    payload: dict[str, Any] | None,
    key: str,
) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return str(value) if value else None


def _retrieval_evidence(
    payload: dict[str, Any] | None,
) -> list[str]:
    if payload is None:
        return []
    values: list[str] = []
    retrieval_id = payload.get("retrieval_id")
    if retrieval_id:
        values.append(str(retrieval_id))
    for match in payload.get("matches", []):
        if isinstance(match, dict):
            for key in ("document_id", "chunk_id"):
                value = match.get(key)
                if value:
                    values.append(str(value))
    return list(dict.fromkeys(values))
