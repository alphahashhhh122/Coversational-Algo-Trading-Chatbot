from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..domain import ExecutionMode, RiskOutcome


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class RiskPolicy:
    policy_id: str = "default_research_policy"
    version: str = "1.0.0"
    max_quantity: int = 50
    max_order_value: float = 2_000_000.0
    max_position_value: float = 4_000_000.0
    max_loss_per_trade: float = 12_500.0
    max_daily_loss: float = 50_000.0
    stop_loss_pct: float = 0.0075
    min_confidence: float = 0.0
    allowed_symbols: tuple[str, ...] = ()
    allowed_modes: tuple[ExecutionMode, ...] = (
        ExecutionMode.RESEARCH,
        ExecutionMode.SANDBOX,
        ExecutionMode.SEMI_AUTO,
    )

    @classmethod
    def from_env(cls) -> "RiskPolicy":
        """Build the default policy with optional IIMC_RISK_* env overrides."""
        defaults = cls()
        return cls(
            max_quantity=int(
                os.getenv("IIMC_RISK_MAX_QUANTITY", defaults.max_quantity)
            ),
            max_order_value=float(
                os.getenv(
                    "IIMC_RISK_MAX_ORDER_VALUE", defaults.max_order_value
                )
            ),
            max_position_value=float(
                os.getenv(
                    "IIMC_RISK_MAX_POSITION_VALUE",
                    defaults.max_position_value,
                )
            ),
            max_loss_per_trade=float(
                os.getenv(
                    "IIMC_RISK_MAX_LOSS_PER_TRADE",
                    defaults.max_loss_per_trade,
                )
            ),
            max_daily_loss=float(
                os.getenv("IIMC_RISK_MAX_DAILY_LOSS", defaults.max_daily_loss)
            ),
            stop_loss_pct=float(
                os.getenv("IIMC_RISK_STOP_LOSS_PCT", defaults.stop_loss_pct)
            ),
        )


@dataclass(frozen=True)
class RiskEvaluation:
    decision_id: str
    outcome: RiskOutcome
    approved: bool
    requested_quantity: int
    approved_quantity: int
    reason: str
    checks: dict[str, Any]
    policy_version: str


class RiskService:
    def __init__(
        self,
        db_path: Path,
        policy: RiskPolicy | None = None,
        allow_live_trading: bool = False,
    ) -> None:
        self.db_path = db_path
        self.policy = policy or RiskPolicy.from_env()
        if allow_live_trading and policy is None:
            self.policy = replace(
                self.policy,
                allowed_modes=(
                    *self.policy.allowed_modes,
                    ExecutionMode.LIVE,
                ),
            )
        self.allow_live_trading = allow_live_trading
        self._ensure_policy_stored()

    def evaluate(
        self,
        *,
        run_id: str,
        signal_id: str,
        signal_type: str,
        symbol: str,
        price: float,
        requested_quantity: int,
        confidence: float,
        execution_mode: ExecutionMode,
    ) -> RiskEvaluation:
        checks: dict[str, Any] = {}

        if execution_mode == ExecutionMode.LIVE and not self.allow_live_trading:
            return self._reject(
                run_id,
                signal_id,
                requested_quantity,
                "Live trading is disabled by configuration",
                {"live_mode_check": {"passed": False}},
            )

        if execution_mode not in self.policy.allowed_modes:
            return self._reject(
                run_id,
                signal_id,
                requested_quantity,
                f"Execution mode {execution_mode.value!r} is not allowed",
                {"execution_mode_check": {"passed": False}},
            )
        checks["execution_mode_check"] = {
            "passed": True,
            "mode": execution_mode.value,
        }

        if signal_type == "exit":
            return self._store(
                run_id=run_id,
                signal_id=signal_id,
                outcome=RiskOutcome.APPROVED,
                requested_quantity=requested_quantity,
                approved_quantity=requested_quantity,
                reason="Exit approved for an open position",
                checks={
                    **checks,
                    "exit_check": {"passed": True},
                },
            )

        if requested_quantity <= 0:
            return self._reject(
                run_id,
                signal_id,
                requested_quantity,
                "Requested quantity must be positive",
                {**checks, "quantity_check": {"passed": False}},
            )

        if self.policy.allowed_symbols and symbol not in self.policy.allowed_symbols:
            return self._reject(
                run_id,
                signal_id,
                requested_quantity,
                f"Symbol {symbol!r} is not permitted by the policy",
                {**checks, "symbol_check": {"passed": False}},
            )
        checks["symbol_check"] = {"passed": True, "symbol": symbol}

        if confidence < self.policy.min_confidence:
            return self._reject(
                run_id,
                signal_id,
                requested_quantity,
                "Signal confidence is below the policy threshold",
                {
                    **checks,
                    "confidence_check": {
                        "passed": False,
                        "confidence": confidence,
                        "minimum": self.policy.min_confidence,
                    },
                },
            )
        checks["confidence_check"] = {
            "passed": True,
            "confidence": confidence,
            "minimum": self.policy.min_confidence,
        }

        approved_quantity = min(requested_quantity, self.policy.max_quantity)
        checks["quantity_check"] = {
            "passed": approved_quantity > 0,
            "requested": requested_quantity,
            "approved": approved_quantity,
            "limit": self.policy.max_quantity,
        }

        max_quantity_by_notional = int(self.policy.max_order_value // price)
        approved_quantity = min(approved_quantity, max_quantity_by_notional)
        checks["notional_check"] = {
            "passed": approved_quantity > 0,
            "order_value": price * max(approved_quantity, 0),
            "limit": self.policy.max_order_value,
        }

        risk_per_unit = price * self.policy.stop_loss_pct
        max_quantity_by_loss = (
            int(self.policy.max_loss_per_trade // risk_per_unit)
            if risk_per_unit > 0
            else 0
        )
        approved_quantity = min(approved_quantity, max_quantity_by_loss)
        checks["loss_per_trade_check"] = {
            "passed": approved_quantity > 0,
            "estimated_loss": risk_per_unit * max(approved_quantity, 0),
            "limit": self.policy.max_loss_per_trade,
            "stop_loss_pct": self.policy.stop_loss_pct,
        }

        realized_daily_loss = self._realized_loss(run_id)
        checks["daily_loss_check"] = {
            "passed": realized_daily_loss < self.policy.max_daily_loss,
            "realized_loss": realized_daily_loss,
            "limit": self.policy.max_daily_loss,
        }
        if realized_daily_loss >= self.policy.max_daily_loss:
            return self._reject(
                run_id,
                signal_id,
                requested_quantity,
                "Daily loss limit has been reached",
                checks,
            )

        if approved_quantity <= 0:
            return self._reject(
                run_id,
                signal_id,
                requested_quantity,
                "Minimum quantity exceeds notional or loss limits",
                checks,
            )

        outcome = (
            RiskOutcome.APPROVED
            if approved_quantity == requested_quantity
            else RiskOutcome.RESIZED
        )
        reason = (
            "All risk checks passed"
            if outcome == RiskOutcome.APPROVED
            else (
                f"Quantity resized from {requested_quantity} "
                f"to {approved_quantity}"
            )
        )
        return self._store(
            run_id=run_id,
            signal_id=signal_id,
            outcome=outcome,
            requested_quantity=requested_quantity,
            approved_quantity=approved_quantity,
            reason=reason,
            checks=checks,
        )

    def _reject(
        self,
        run_id: str,
        signal_id: str,
        requested_quantity: int,
        reason: str,
        checks: dict[str, Any],
    ) -> RiskEvaluation:
        return self._store(
            run_id=run_id,
            signal_id=signal_id,
            outcome=RiskOutcome.REJECTED,
            requested_quantity=requested_quantity,
            approved_quantity=0,
            reason=reason,
            checks=checks,
        )

    def _store(
        self,
        *,
        run_id: str,
        signal_id: str,
        outcome: RiskOutcome,
        requested_quantity: int,
        approved_quantity: int,
        reason: str,
        checks: dict[str, Any],
    ) -> RiskEvaluation:
        decision_id = f"risk_{uuid.uuid4().hex[:12]}"
        evaluation = RiskEvaluation(
            decision_id=decision_id,
            outcome=outcome,
            approved=outcome != RiskOutcome.REJECTED,
            requested_quantity=requested_quantity,
            approved_quantity=approved_quantity,
            reason=reason,
            checks=checks,
            policy_version=self.policy.version,
        )
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO risk_decisions (
                    decision_id,
                    run_id,
                    signal_id,
                    approved,
                    requested_quantity,
                    approved_quantity,
                    reason,
                    checks_json,
                    created_at,
                    risk_policy_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    decision_id,
                    run_id,
                    signal_id,
                    evaluation.approved,
                    requested_quantity,
                    approved_quantity,
                    reason,
                    json.dumps(
                        {"outcome": outcome.value, **checks},
                        sort_keys=True,
                        default=str,
                    ),
                    utc_now(),
                    self.policy.version,
                ],
            )
        finally:
            con.close()
        return evaluation

    def _realized_loss(self, run_id: str) -> float:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT COALESCE(
                    SUM(CASE WHEN realized_pnl < 0 THEN -realized_pnl ELSE 0 END),
                    0
                )
                FROM trade_fills
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
        finally:
            con.close()
        return float(row[0]) if row else 0.0

    def _ensure_policy_stored(self) -> None:
        con = connect(self.db_path)
        try:
            for mode in self.policy.allowed_modes:
                con.execute(
                    """
                    INSERT OR IGNORE INTO risk_limits (
                        risk_limit_id,
                        scope,
                        scope_id,
                        execution_mode,
                        max_order_value,
                        max_position_value,
                        max_loss_per_trade,
                        max_daily_loss,
                        requires_approval,
                        enabled,
                        created_at,
                        updated_at,
                        policy_version,
                        max_quantity,
                        stop_loss_pct,
                        min_confidence,
                        allowed_symbols_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        f"{self.policy.policy_id}:{mode.value}",
                        "global",
                        "*",
                        mode.value,
                        self.policy.max_order_value,
                        self.policy.max_position_value,
                        self.policy.max_loss_per_trade,
                        self.policy.max_daily_loss,
                        mode == ExecutionMode.SEMI_AUTO,
                        True,
                        utc_now(),
                        utc_now(),
                        self.policy.version,
                        self.policy.max_quantity,
                        self.policy.stop_loss_pct,
                        self.policy.min_confidence,
                        json.dumps(self.policy.allowed_symbols),
                    ],
                )
        finally:
            con.close()


def get_risk_summary(db_path: Path, run_id: str) -> dict[str, Any]:
    con = connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT r.decision_id, r.signal_id, r.approved,
                   r.requested_quantity, r.approved_quantity,
                   r.reason, r.checks_json, r.risk_policy_version,
                   r.created_at, s.symbol, s.signal_type, s.direction,
                   s.reason, s.features_json, s.timestamp
            FROM risk_decisions AS r
            LEFT JOIN strategy_signals AS s ON s.signal_id = r.signal_id
            WHERE r.run_id = ?
            ORDER BY r.created_at
            """,
            [run_id],
        ).fetchall()
    finally:
        con.close()

    decisions = [
        {
            "decision_id": row[0],
            "signal_id": row[1],
            "approved": row[2],
            "requested_quantity": row[3],
            "approved_quantity": row[4],
            "reason": row[5],
            "checks": json.loads(row[6]),
            "policy_version": row[7],
            "created_at": row[8],
            "symbol": row[9],
            "signal_type": row[10],
            "direction": row[11],
            "signal_reason": row[12],
            "features": json.loads(row[13]) if row[13] else {},
            "timestamp": row[14],
        }
        for row in rows
    ]
    return {
        "run_id": run_id,
        "total": len(decisions),
        "approved": sum(1 for decision in decisions if decision["approved"]),
        "rejected": sum(1 for decision in decisions if not decision["approved"]),
        "decisions": decisions,
    }
