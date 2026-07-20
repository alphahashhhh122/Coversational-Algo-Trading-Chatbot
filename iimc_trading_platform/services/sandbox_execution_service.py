from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from ..db import connect
from ..domain import ExecutionMode, OrderStatus
from ..infrastructure.openalgo import OpenAlgoClient, OpenAlgoResponseError
from .audit_service import AuditService
from .order_service import OrderService


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SandboxBroker(Protocol):
    def analyzer_status(self) -> dict[str, Any]: ...

    def quote(self, *, symbol: str, exchange: str) -> dict[str, Any]: ...

    def place_sandbox_order(self, **kwargs) -> dict[str, Any]: ...

    def place_live_order(self, **kwargs) -> dict[str, Any]: ...

    def order_status(
        self,
        *,
        order_id: str,
        strategy: str,
    ) -> dict[str, Any]: ...


class SandboxExecutionService:
    def __init__(
        self,
        db_path: Path,
        audit_service: AuditService,
        broker: SandboxBroker | None,
        *,
        require_approval: bool = False,
        allow_live_trading: bool = False,
        provider_readiness: Callable[[], dict[str, Any]] | None = None,
        max_signal_age_minutes: int | None = None,
    ) -> None:
        self.db_path = db_path
        self.audit = audit_service
        self.broker = broker
        self.require_approval = require_approval
        self.allow_live_trading = allow_live_trading
        self.provider_readiness = provider_readiness
        self.max_signal_age_minutes = max_signal_age_minutes
        self.orders = OrderService(db_path)

    def prepare_direct_intent(
        self,
        *,
        risk_service,
        symbol: str,
        side: str,
        quantity: int,
        exchange: str = "NSE",
        product: str = "MIS",
        order_type: str = "MARKET",
        limit_price: float | None = None,
        strategy_name: str = "manual_order",
        requested_by: str = "chat",
    ) -> dict[str, Any]:
        """Quote-anchored discretionary paper order.

        Fetches a fresh broker quote, records a manual signal at the live
        price, runs the deterministic risk policy, and then flows through
        the exact same intent/approval/submission machine as strategy
        orders. Nothing is executed here — the intent still requires
        human approval before submission.
        """
        if self.broker is None:
            raise ValueError(
                "OpenAlgo credentials are required for direct orders"
            )
        side = side.upper().strip()
        if side != "BUY":
            raise ValueError(
                "Direct orders currently support BUY entries. To exit or "
                "reduce a position, use Square off in the Monitor tab or a "
                "strategy exit."
            )
        symbol = symbol.upper().strip()
        quote = self.broker.quote(symbol=symbol, exchange=exchange)
        live_price = float((quote.get("data") or {}).get("ltp") or 0)
        if live_price <= 0:
            raise ValueError(
                f"No fresh quote is available for {symbol}; the order was "
                "not prepared"
            )
        now = utc_now()
        run_id = f"manual_{uuid.uuid4().hex[:10]}"
        signal_id = f"sig_manual_{uuid.uuid4().hex[:12]}"
        signal_type = "entry" if side == "BUY" else "exit"
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO strategy_signals VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    signal_id,
                    run_id,
                    now,
                    symbol,
                    signal_type,
                    "long" if side == "BUY" else "flat",
                    1.0,
                    f"Manual {side} request at live price {live_price}",
                    json.dumps({"source": "direct_order", "ltp": live_price}),
                    now,
                ],
            )
        finally:
            con.close()
        evaluation = risk_service.evaluate(
            run_id=run_id,
            signal_id=signal_id,
            signal_type=signal_type,
            symbol=symbol,
            price=live_price,
            requested_quantity=quantity,
            confidence=1.0,
            execution_mode=ExecutionMode.SEMI_AUTO,
        )
        if not evaluation.approved:
            raise ValueError(
                f"Risk policy rejected the order: {evaluation.reason}"
            )
        return self.prepare_intent(
            decision_id=evaluation.decision_id,
            symbol=symbol,
            exchange=exchange,
            side=side,
            product=product,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            strategy_name=strategy_name,
            requested_by=requested_by,
        )

    def prepare_intent(
        self,
        *,
        decision_id: str,
        symbol: str,
        exchange: str,
        side: str,
        product: str,
        order_type: str,
        quantity: int,
        strategy_name: str,
        limit_price: float | None = None,
        trigger_price: float | None = None,
        requested_by: str = "user",
        execution_mode: ExecutionMode = ExecutionMode.SEMI_AUTO,
    ) -> dict[str, Any]:
        side = side.upper()
        order_type = order_type.upper()
        product = product.upper()
        exchange = exchange.upper()
        symbol = symbol.strip().upper()
        if execution_mode not in {ExecutionMode.SEMI_AUTO, ExecutionMode.LIVE}:
            raise ValueError("Order intents support semi_auto or live mode")
        is_live = execution_mode == ExecutionMode.LIVE
        if is_live and not self.allow_live_trading:
            raise PermissionError("Live trading is disabled by configuration")
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if order_type not in {"MARKET", "LIMIT", "SL", "SL-M"}:
            raise ValueError("Unsupported order type")
        if product not in {"MIS", "CNC", "NRML"}:
            raise ValueError("Unsupported product")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if order_type in {"LIMIT", "SL"} and not _valid_positive_price(limit_price):
            raise ValueError(f"{order_type} requires limit_price")
        if order_type in {"SL", "SL-M"} and not _valid_positive_price(trigger_price):
            raise ValueError(f"{order_type} requires trigger_price")

        risk = self._approved_risk_decision(decision_id, execution_mode)
        approved_symbol = risk["symbol"]
        if symbol != approved_symbol:
            raise ValueError(
                "Intent symbol does not match the risk-approved symbol "
                f"({approved_symbol})"
            )
        expected_exchange = self._run_exchange(risk["run_id"])
        if expected_exchange is not None and exchange != expected_exchange:
            raise ValueError(
                "Intent exchange does not match the backtest dataset exchange "
                f"({expected_exchange})"
            )
        expected_side = risk.get("expected_side")
        if expected_side is not None and side != expected_side:
            raise ValueError(
                "Intent side does not match the risk-approved signal side "
                f"({expected_side})"
            )
        self._assert_signal_is_current(risk)
        if quantity > int(risk["approved_quantity"]):
            raise ValueError(
                "Intent quantity exceeds the risk-approved quantity"
            )
        idempotency_key = (
            f"{decision_id}:{symbol}:{exchange}:{side}:{product}:"
            f"{order_type}:{quantity}:{limit_price}:{trigger_price}"
        )
        existing = self._find_intent_by_key(idempotency_key)
        if existing is not None:
            return existing

        intent_id = f"intent_{uuid.uuid4().hex[:12]}"
        approval_id = (
            f"approval_{uuid.uuid4().hex[:12]}"
            if self.require_approval or is_live
            else None
        )
        now = utc_now()
        payload = {
            "risk_policy_version": risk["risk_policy_version"],
            "risk_checks": risk["checks"],
        }
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                INSERT INTO order_intents (
                    intent_id, run_id, signal_id, symbol, exchange, side,
                    product, order_type, quantity, limit_price,
                    execution_mode, status, payload_json, created_at,
                    updated_at, decision_id, approval_id, order_id,
                    broker_order_id, idempotency_key, rejection_reason,
                    strategy_name, trigger_price
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    intent_id,
                    risk["run_id"],
                    risk["signal_id"],
                    symbol,
                    exchange,
                    side,
                    product,
                    order_type,
                    quantity,
                    limit_price,
                    execution_mode.value,
                    "pending_approval" if approval_id else "approved",
                    json.dumps(payload, sort_keys=True, default=str),
                    now,
                    now,
                    decision_id,
                    approval_id,
                    None,
                    None,
                    idempotency_key,
                    None,
                    strategy_name,
                    trigger_price,
                ],
            )
            if approval_id:
                con.execute(
                    """
                    INSERT INTO approval_requests (
                        approval_id, entity_type, entity_id, requested_action,
                        status, requested_by, decided_by, reason, created_at,
                        decided_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        approval_id,
                        "order_intent",
                        intent_id,
                        (
                            "submit_openalgo_live_order"
                            if is_live
                            else "submit_openalgo_sandbox_order"
                        ),
                        "pending",
                        requested_by,
                        None,
                        None,
                        now,
                        None,
                        json.dumps(
                            {
                                "decision_id": decision_id,
                                "quantity": quantity,
                                "symbol": symbol,
                            },
                            sort_keys=True,
                        ),
                    ],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        self.audit.record(
            entity_type="order_intent",
            entity_id=intent_id,
            action=(
                "approval_requested"
                if approval_id
                else "paper_intent_preapproved"
            ),
            actor=requested_by,
            payload={"approval_id": approval_id},
        )
        return self.get_intent(intent_id)

    def prepare_live_intent(self, **kwargs: Any) -> dict[str, Any]:
        return self.prepare_intent(
            **kwargs,
            execution_mode=ExecutionMode.LIVE,
        )

    def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any]:
        if not decided_by.strip():
            raise ValueError("decided_by is required")
        if not reason.strip():
            raise ValueError("A decision reason is required")
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT entity_id, status
                FROM approval_requests
                WHERE approval_id = ?
                """,
                [approval_id],
            ).fetchone()
            if row is None:
                raise ValueError(f"Approval not found: {approval_id}")
            if row[1] != "pending":
                raise ValueError(f"Approval is already {row[1]}")
            intent_id = row[0]
            decision_status = "approved" if approved else "rejected"
            intent_status = "approved" if approved else "rejected"
            now = utc_now()
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_by = ?, reason = ?, decided_at = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                [
                    decision_status,
                    decided_by,
                    reason,
                    now,
                    approval_id,
                ],
            )
            con.execute(
                """
                UPDATE order_intents
                SET status = ?, rejection_reason = ?, updated_at = ?
                WHERE intent_id = ? AND status = 'pending_approval'
                """,
                [
                    intent_status,
                    None if approved else reason,
                    now,
                    intent_id,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            con.close()
        self.audit.record(
            entity_type="approval_request",
            entity_id=approval_id,
            action=decision_status,
            actor=decided_by,
            payload={"reason": reason, "intent_id": intent_id},
        )
        return self.get_intent(intent_id)

    def submit(self, intent_id: str, *, actor: str) -> dict[str, Any]:
        if self.broker is None:
            raise ValueError(
                "OpenAlgo credentials are required for sandbox submission"
            )
        intent = self.get_intent(intent_id)
        if intent["status"] != "approved":
            if intent["status"] == "submitting":
                raise ValueError(
                    "Intent submission is already in progress; duplicate "
                    "broker submission refused"
                )
            raise ValueError(
                f"Intent must be approved before submission; "
                f"current status is {intent['status']}"
            )

        execution_mode = ExecutionMode(intent["execution_mode"])
        is_live = execution_mode == ExecutionMode.LIVE
        if is_live and not self.allow_live_trading:
            raise PermissionError("Live trading is disabled by configuration")
        if is_live:
            self._assert_live_provider_ready()
        if not is_live:
            analyzer = self.broker.analyzer_status()
            if (
                analyzer.get("analyze_mode") is not True
                or analyzer.get("mode") != "analyze"
            ):
                raise ValueError(
                    "OpenAlgo analyzer mode is not active; submission refused"
                )

        order = self.orders.create_order(
            run_id=intent["run_id"],
            decision_id=intent["decision_id"],
            symbol=intent["symbol"],
            side=intent["side"],
            order_type=intent["order_type"],
            quantity=intent["quantity"],
            execution_mode=(
                ExecutionMode.LIVE if is_live else ExecutionMode.SANDBOX
            ),
            price=float(intent["limit_price"] or 0.0),
        )
        if not self._claim_submission(intent_id, order.order_id):
            current = self.get_intent(intent_id)
            raise ValueError(
                "Intent submission is already in progress or complete; "
                f"current status is {current['status']}"
            )
        self.audit.record(
            entity_type="order_intent",
            entity_id=intent_id,
            action="submission_started",
            actor=actor,
            payload={"order_id": order.order_id},
        )

        try:
            broker_method = (
                self.broker.place_live_order
                if is_live
                else self.broker.place_sandbox_order
            )
            response = broker_method(
                strategy=intent["strategy_name"],
                symbol=intent["symbol"],
                action=intent["side"],
                exchange=intent["exchange"],
                price_type=intent["order_type"],
                product=intent["product"],
                quantity=intent["quantity"],
                price=float(intent["limit_price"] or 0.0),
                trigger_price=float(intent["trigger_price"] or 0.0),
            )
        except OpenAlgoResponseError as exc:
            self._set_intent_status(
                intent_id,
                "failed",
                rejection_reason=str(exc),
            )
            self.orders.transition(
                order.order_id,
                OrderStatus.FAILED,
                reason="OpenAlgo rejected the paper-order request",
            )
            self.audit.record(
                entity_type="order_intent",
                entity_id=intent_id,
                action="submission_rejected",
                actor=actor,
                payload={"error_type": type(exc).__name__},
            )
            raise ValueError(
                "OpenAlgo rejected this paper order before accepting it: "
                f"{exc}. No analyzer order was created."
            ) from exc
        except Exception as exc:
            self._set_intent_status(
                intent_id,
                "submission_uncertain",
                rejection_reason=str(exc),
            )
            self.orders.transition(
                order.order_id,
                OrderStatus.FAILED,
                reason=(
                    "OpenAlgo submission outcome is uncertain; "
                    "automatic retry is disabled"
                ),
            )
            self.audit.record(
                entity_type="order_intent",
                entity_id=intent_id,
                action="submission_uncertain",
                actor=actor,
                payload={"error_type": type(exc).__name__},
            )
            raise

        broker_order_id = str(response["orderid"])
        self.orders.transition(
            order.order_id,
            OrderStatus.SUBMITTED,
            reason=(
                "OpenAlgo live order accepted"
                if is_live
                else "OpenAlgo analyzer order accepted"
            ),
            broker_order_id=broker_order_id,
        )
        self._set_intent_status(
            intent_id,
            "submitted",
            order_id=order.order_id,
            broker_order_id=broker_order_id,
        )
        self.audit.record(
            entity_type="order_intent",
            entity_id=intent_id,
            action="submitted",
            actor=actor,
            payload={
                "order_id": order.order_id,
                "broker_order_id": broker_order_id,
                "mode": response.get("mode"),
                "execution_mode": execution_mode.value,
            },
        )
        return self.get_intent(intent_id)

    def _assert_live_provider_ready(self) -> None:
        if self.provider_readiness is not None:
            readiness = self.provider_readiness()
            if not readiness.get("ok"):
                raise ValueError(
                    "OpenAlgo live execution readiness failed: "
                    f"{readiness.get('message', 'provider verification failed')}"
                )
            if readiness.get("analyzer_mode") is True:
                raise ValueError(
                    "OpenAlgo analyzer mode is active; live submission refused"
                )
            return

        analyzer = self.broker.analyzer_status() if self.broker else {}
        if (
            analyzer.get("analyze_mode") is True
            or analyzer.get("mode") == "analyze"
        ):
            raise ValueError(
                "OpenAlgo analyzer mode is active; live submission refused"
            )

    def _claim_submission(self, intent_id: str, order_id: str) -> bool:
        """Atomically reserve an approved intent before its broker call."""
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                UPDATE order_intents
                SET status = 'submitting', order_id = ?, updated_at = ?
                WHERE intent_id = ? AND status = 'approved'
                RETURNING intent_id
                """,
                [order_id, utc_now(), intent_id],
            ).fetchone()
            return row is not None
        finally:
            con.close()

    def reconcile(self, intent_id: str, *, actor: str = "system") -> dict[str, Any]:
        if self.broker is None:
            raise ValueError(
                "OpenAlgo credentials are required for reconciliation"
            )
        intent = self.get_intent(intent_id)
        if intent["status"] not in {"submitted", "open", "pending"}:
            raise ValueError(
                f"Intent cannot be reconciled from status {intent['status']}"
            )
        if not intent["broker_order_id"] or not intent["order_id"]:
            raise ValueError("Intent has no submitted OpenAlgo order")

        broker = self.broker.order_status(
            order_id=intent["broker_order_id"],
            strategy=intent["strategy_name"],
        )
        broker_status = str(broker.get("order_status", "")).lower()
        mapping = {
            "complete": ("filled", OrderStatus.FILLED),
            "open": ("open", None),
            "pending": ("pending", None),
            "rejected": ("rejected", OrderStatus.REJECTED),
            "cancelled": ("cancelled", OrderStatus.CANCELLED),
        }
        if broker_status not in mapping:
            raise ValueError(
                f"Unsupported OpenAlgo order status: {broker_status!r}"
            )
        intent_status, order_status = mapping[broker_status]
        if order_status is not None:
            current = self.orders.get(intent["order_id"])
            if current and current.status != order_status:
                if order_status == OrderStatus.FILLED:
                    self.orders.record_simulated_fill(
                        current,
                        fill_price=_broker_float(
                            broker,
                            "average_price",
                            "avg_price",
                            "price",
                            "fill_price",
                        ),
                        fees=_broker_float(
                            broker,
                            "fees",
                            "charges",
                            "brokerage",
                            default=0.0,
                        ),
                        realized_pnl=_broker_float(
                            broker,
                            "realized_pnl",
                            "pnl",
                            default=0.0,
                        ),
                        filled_at=utc_now(),
                    )
                else:
                    self.orders.transition(
                        current.order_id,
                        order_status,
                        reason=f"Reconciled from OpenAlgo: {broker_status}",
                    )
        self._set_intent_status(intent_id, intent_status)
        snapshot_id = self._store_reconciliation_snapshot(
            intent_id,
            broker,
        )
        self.audit.record(
            entity_type="order_intent",
            entity_id=intent_id,
            action="reconciled",
            actor=actor,
            payload={
                "broker_status": broker_status,
                "snapshot_id": snapshot_id,
            },
        )
        result = self.get_intent(intent_id)
        result["reconciliation_snapshot_id"] = snapshot_id
        result["broker_state"] = broker
        return result

    def cancel(self, intent_id: str, *, actor: str) -> dict[str, Any]:
        if self.broker is None:
            raise ValueError("OpenAlgo credentials are required for cancellation")
        intent = self.get_intent(intent_id)
        if intent["status"] not in {"submitted", "open", "pending"}:
            raise ValueError(
                f"Intent cannot be cancelled from status {intent['status']}"
            )
        if not intent["broker_order_id"] or not intent["order_id"]:
            raise ValueError("Intent has no submitted OpenAlgo order")

        response = self.broker.cancel_order(
            order_id=intent["broker_order_id"],
            strategy=intent["strategy_name"],
        )
        self.orders.transition(
            intent["order_id"],
            OrderStatus.CANCELLED,
            reason="OpenAlgo analyzer order cancelled",
            broker_order_id=intent["broker_order_id"],
        )
        self._set_intent_status(intent_id, "cancelled")
        self.audit.record(
            entity_type="order_intent",
            entity_id=intent_id,
            action="cancelled",
            actor=actor,
            payload={
                "broker_order_id": intent["broker_order_id"],
                "mode": response.get("mode"),
                "message": response.get("message"),
            },
        )
        result = self.get_intent(intent_id)
        result["broker_cancel_state"] = response
        return result

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT intent_id, run_id, signal_id, decision_id, approval_id,
                       order_id, broker_order_id, symbol, exchange, side,
                       product, order_type, quantity, limit_price,
                       trigger_price, execution_mode, status, strategy_name,
                       rejection_reason, payload_json, created_at, updated_at
                FROM order_intents
                WHERE intent_id = ?
                """,
                [intent_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(f"Order intent not found: {intent_id}")
        return {
            "intent_id": row[0],
            "run_id": row[1],
            "signal_id": row[2],
            "decision_id": row[3],
            "approval_id": row[4],
            "order_id": row[5],
            "broker_order_id": row[6],
            "symbol": row[7],
            "exchange": row[8],
            "side": row[9],
            "product": row[10],
            "order_type": row[11],
            "quantity": row[12],
            "limit_price": row[13],
            "trigger_price": row[14],
            "execution_mode": row[15],
            "status": row[16],
            "strategy_name": row[17],
            "rejection_reason": row[18],
            "payload": json.loads(row[19]),
            "created_at": row[20],
            "updated_at": row[21],
        }

    def list_intents(self, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT intent_id, run_id, signal_id, decision_id, approval_id,
                       order_id, broker_order_id, symbol, exchange, side,
                       product, order_type, quantity, limit_price,
                       trigger_price, execution_mode, status, strategy_name,
                       rejection_reason, created_at, updated_at
                FROM order_intents
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "intents": [
                {
                    "intent_id": row[0],
                    "run_id": row[1],
                    "signal_id": row[2],
                    "decision_id": row[3],
                    "approval_id": row[4],
                    "order_id": row[5],
                    "broker_order_id": row[6],
                    "symbol": row[7],
                    "exchange": row[8],
                    "side": row[9],
                    "product": row[10],
                    "order_type": row[11],
                    "quantity": row[12],
                    "limit_price": row[13],
                    "trigger_price": row[14],
                    "execution_mode": row[15],
                    "status": row[16],
                    "strategy_name": row[17],
                    "rejection_reason": row[18],
                    "created_at": row[19],
                    "updated_at": row[20],
                }
                for row in rows
            ]
        }

    def list_pending_approvals(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT approval_id, entity_id, requested_action, requested_by,
                       created_at
                FROM approval_requests
                WHERE status = 'pending'
                ORDER BY created_at
                """
            ).fetchall()
        finally:
            con.close()
        return {
            "approvals": [
                {
                    "approval_id": row[0],
                    "intent_id": row[1],
                    "requested_action": row[2],
                    "requested_by": row[3],
                    "created_at": row[4],
                }
                for row in rows
            ]
        }

    def _approved_risk_decision(
        self,
        decision_id: str,
        execution_mode: ExecutionMode,
    ) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT run_id, signal_id, approved, approved_quantity,
                       checks_json, risk_policy_version
                FROM risk_decisions
                WHERE decision_id = ?
                """,
                [decision_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(f"Risk decision not found: {decision_id}")
        if row[2] is not True:
            raise ValueError("Risk decision was rejected")
        checks = json.loads(row[4])
        approved_symbol = str(
            checks.get("symbol_check", {}).get("symbol") or ""
        ).upper()
        if not approved_symbol:
            raise ValueError(
                "Risk decision has no approved symbol scope and cannot be "
                "used for an order intent"
            )
        evaluated_mode = (
            checks.get("execution_mode_check", {}).get("mode")
        )
        if evaluated_mode != execution_mode.value:
            raise ValueError(
                f"{execution_mode.value} order intents require a risk "
                f"decision evaluated in {execution_mode.value} mode"
            )
        signal_scope = self._signal_scope(row[1])
        signal_symbol = signal_scope.get("signal_symbol")
        if signal_symbol is not None and signal_symbol != approved_symbol:
            raise ValueError(
                "Risk decision symbol scope does not match its persisted "
                "strategy signal"
            )
        return {
            "run_id": row[0],
            "signal_id": row[1],
            "symbol": approved_symbol,
            "approved_quantity": row[3],
            "checks": checks,
            "risk_policy_version": row[5],
            **signal_scope,
        }

    def _signal_scope(self, signal_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT timestamp, symbol, signal_type, direction
                FROM strategy_signals
                WHERE signal_id = ?
                """,
                [signal_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return {}
        signal_type = str(row[2]).lower()
        return {
            "signal_timestamp": row[0],
            "signal_symbol": str(row[1]).upper(),
            "expected_side": "BUY" if signal_type == "entry" else "SELL",
        }

    def _assert_signal_is_current(self, risk: dict[str, Any]) -> None:
        if self.max_signal_age_minutes is None or self.max_signal_age_minutes == 0:
            return
        signal_timestamp = risk.get("signal_timestamp")
        if not isinstance(signal_timestamp, datetime):
            raise ValueError(
                "Paper intent requires a persisted current strategy signal; "
                "run the strategy on freshly imported market data first"
            )
        age_seconds = (utc_now() - signal_timestamp).total_seconds()
        if age_seconds < -60 or age_seconds > self.max_signal_age_minutes * 60:
            raise ValueError(
                "Risk-approved signal is outside the paper-trading freshness "
                f"window ({self.max_signal_age_minutes} minutes); refresh "
                "OpenAlgo history and run the strategy again"
            )

    def _run_exchange(self, run_id: str) -> str | None:
        """Return the governed dataset exchange when this is a stored backtest run."""
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT c.exchange
                FROM strategy_runs AS r
                JOIN data_catalog AS c ON c.dataset_id = r.dataset_id
                WHERE r.run_id = ?
                """,
                [run_id],
            ).fetchone()
        finally:
            con.close()
        return str(row[0]).upper() if row and row[0] else None

    def _find_intent_by_key(self, key: str) -> dict[str, Any] | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT intent_id
                FROM order_intents
                WHERE idempotency_key = ?
                """,
                [key],
            ).fetchone()
        finally:
            con.close()
        return self.get_intent(row[0]) if row else None

    def _set_intent_status(
        self,
        intent_id: str,
        status: str,
        *,
        order_id: str | None = None,
        broker_order_id: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE order_intents
                SET status = ?,
                    order_id = COALESCE(?, order_id),
                    broker_order_id = COALESCE(?, broker_order_id),
                    rejection_reason = ?,
                    updated_at = ?
                WHERE intent_id = ?
                """,
                [
                    status,
                    order_id,
                    broker_order_id,
                    rejection_reason,
                    utc_now(),
                    intent_id,
                ],
            )
        finally:
            con.close()

    def _store_reconciliation_snapshot(
        self,
        intent_id: str,
        broker_state: dict[str, Any],
    ) -> str:
        snapshot_id = f"oas_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO openalgo_snapshots VALUES (?, ?, ?, ?, ?)
                """,
                [
                    snapshot_id,
                    "order_status",
                    f"openalgo_api:orderstatus:{intent_id}",
                    json.dumps(broker_state, sort_keys=True, default=str),
                    utc_now(),
                ],
            )
        finally:
            con.close()
        return snapshot_id


def _broker_float(
    payload: dict[str, Any],
    *names: str,
    default: float | None = None,
) -> float:
    for name in names:
        value = payload.get(name)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    if default is not None:
        return default
    raise ValueError(
        f"OpenAlgo fill response omitted numeric field: {'/'.join(names)}"
    )


def _valid_positive_price(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0
