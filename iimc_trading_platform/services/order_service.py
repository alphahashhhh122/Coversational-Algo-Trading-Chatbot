from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..domain import ExecutionMode, OrderStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {
        OrderStatus.PENDING_APPROVAL,
        OrderStatus.SUBMITTED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.PENDING_APPROVAL: {
        OrderStatus.SUBMITTED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.SUBMITTED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.FAILED: set(),
    OrderStatus.RISK_APPROVED: set(),
    OrderStatus.RISK_REJECTED: set(),
}


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    run_id: str
    decision_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    status: OrderStatus
    execution_mode: ExecutionMode
    price: float
    idempotency_key: str
    broker_order_id: str | None
    filled_quantity: int
    average_fill_price: float | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


class OrderService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def create_order(
        self,
        *,
        run_id: str,
        decision_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: int,
        execution_mode: ExecutionMode,
        price: float,
    ) -> OrderRecord:
        idempotency_key = f"{run_id}:{decision_id}"
        existing = self.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        now = utc_now()
        order = OrderRecord(
            order_id=f"ord_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            decision_id=decision_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            status=(
                OrderStatus.PENDING_APPROVAL
                if execution_mode == ExecutionMode.SEMI_AUTO
                else OrderStatus.CREATED
            ),
            execution_mode=execution_mode,
            price=price,
            idempotency_key=idempotency_key,
            broker_order_id=None,
            filled_quantity=0,
            average_fill_price=None,
            rejection_reason=None,
            created_at=now,
            updated_at=now,
        )
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO order_events (
                    order_id,
                    run_id,
                    decision_id,
                    symbol,
                    side,
                    order_type,
                    quantity,
                    status,
                    payload_json,
                    broker_order_id,
                    created_at,
                    updated_at,
                    execution_mode,
                    price,
                    idempotency_key,
                    filled_quantity,
                    average_fill_price,
                    rejection_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    order.order_id,
                    run_id,
                    decision_id,
                    symbol,
                    side,
                    order_type,
                    quantity,
                    order.status.value,
                    json.dumps(
                        {
                            "execution_mode": execution_mode.value,
                            "price": price,
                        },
                        sort_keys=True,
                    ),
                    None,
                    now,
                    now,
                    execution_mode.value,
                    price,
                    idempotency_key,
                    0,
                    None,
                    None,
                ],
            )
        finally:
            con.close()
        self._record_transition(
            order.order_id,
            None,
            order.status,
            "order created",
            {},
        )
        return order

    def transition(
        self,
        order_id: str,
        new_status: OrderStatus,
        *,
        reason: str | None = None,
        broker_order_id: str | None = None,
        filled_quantity: int | None = None,
        average_fill_price: float | None = None,
    ) -> OrderRecord:
        order = self.get(order_id)
        if order is None:
            raise ValueError(f"Order not found: {order_id}")
        if new_status not in VALID_TRANSITIONS.get(order.status, set()):
            raise ValueError(
                f"Invalid order transition {order.status.value} "
                f"-> {new_status.value}"
            )

        updated = replace(
            order,
            status=new_status,
            broker_order_id=broker_order_id or order.broker_order_id,
            filled_quantity=(
                filled_quantity
                if filled_quantity is not None
                else order.filled_quantity
            ),
            average_fill_price=(
                average_fill_price
                if average_fill_price is not None
                else order.average_fill_price
            ),
            rejection_reason=(
                reason
                if new_status in {OrderStatus.REJECTED, OrderStatus.FAILED}
                else order.rejection_reason
            ),
            updated_at=utc_now(),
        )
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE order_events
                SET status = ?,
                    broker_order_id = ?,
                    filled_quantity = ?,
                    average_fill_price = ?,
                    rejection_reason = ?,
                    updated_at = ?
                WHERE order_id = ?
                """,
                [
                    updated.status.value,
                    updated.broker_order_id,
                    updated.filled_quantity,
                    updated.average_fill_price,
                    updated.rejection_reason,
                    updated.updated_at,
                    order_id,
                ],
            )
        finally:
            con.close()
        self._record_transition(
            order_id,
            order.status,
            new_status,
            reason,
            {
                "broker_order_id": updated.broker_order_id,
                "filled_quantity": updated.filled_quantity,
                "average_fill_price": updated.average_fill_price,
            },
        )
        return updated

    def record_simulated_fill(
        self,
        order: OrderRecord,
        *,
        fill_price: float,
        fees: float,
        realized_pnl: float,
        filled_at: datetime,
    ) -> OrderRecord:
        current = order
        if current.status == OrderStatus.CREATED:
            current = self.transition(
                current.order_id,
                OrderStatus.SUBMITTED,
                reason="research/sandbox order submitted",
            )
        if current.status == OrderStatus.PENDING_APPROVAL:
            raise ValueError("Semi-auto order requires approval before fill")

        trade_id = f"trd_{current.order_id.removeprefix('ord_')}"
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO trade_fills (
                    trade_id,
                    order_id,
                    run_id,
                    symbol,
                    side,
                    quantity,
                    price,
                    fees,
                    realized_pnl,
                    filled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    trade_id,
                    current.order_id,
                    current.run_id,
                    current.symbol,
                    current.side,
                    current.quantity,
                    fill_price,
                    fees,
                    realized_pnl,
                    filled_at,
                ],
            )
        finally:
            con.close()
        return self.transition(
            current.order_id,
            OrderStatus.FILLED,
            reason="simulated fill recorded",
            filled_quantity=current.quantity,
            average_fill_price=fill_price,
        )

    def get(self, order_id: str) -> OrderRecord | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT order_id, run_id, decision_id, symbol, side,
                       order_type, quantity, status, execution_mode, price,
                       idempotency_key, broker_order_id, filled_quantity,
                       average_fill_price, rejection_reason, created_at,
                       COALESCE(updated_at, created_at)
                FROM order_events
                WHERE order_id = ?
                """,
                [order_id],
            ).fetchone()
        finally:
            con.close()
        return self._row_to_order(row) if row else None

    def find_by_idempotency_key(self, key: str) -> OrderRecord | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT order_id
                FROM order_events
                WHERE idempotency_key = ?
                """,
                [key],
            ).fetchone()
        finally:
            con.close()
        return self.get(row[0]) if row else None

    def _record_transition(
        self,
        order_id: str,
        from_status: OrderStatus | None,
        to_status: OrderStatus,
        reason: str | None,
        payload: dict[str, Any],
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO order_state_events VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"ordevt_{uuid.uuid4().hex[:12]}",
                    order_id,
                    from_status.value if from_status else None,
                    to_status.value,
                    reason,
                    json.dumps(payload, sort_keys=True, default=str),
                    utc_now(),
                ],
            )
        finally:
            con.close()

    @staticmethod
    def _row_to_order(row: tuple) -> OrderRecord:
        return OrderRecord(
            order_id=row[0],
            run_id=row[1],
            decision_id=row[2],
            symbol=row[3],
            side=row[4],
            order_type=row[5],
            quantity=int(row[6]),
            status=OrderStatus(row[7]),
            execution_mode=ExecutionMode(row[8]),
            price=float(row[9]),
            idempotency_key=row[10],
            broker_order_id=row[11],
            filled_quantity=int(row[12] or 0),
            average_fill_price=row[13],
            rejection_reason=row[14],
            created_at=row[15],
            updated_at=row[16],
        )


def get_order_timeline(db_path: Path, run_id: str) -> dict[str, Any]:
    con = connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT o.order_id, o.symbol, o.side, o.quantity, o.price,
                   o.status, o.execution_mode, o.created_at,
                   s.from_status, s.to_status, s.reason, s.created_at
            FROM order_events AS o
            LEFT JOIN order_state_events AS s ON s.order_id = o.order_id
            WHERE o.run_id = ?
            ORDER BY o.created_at, s.created_at
            """,
            [run_id],
        ).fetchall()
    finally:
        con.close()

    orders: dict[str, dict[str, Any]] = {}
    for row in rows:
        order = orders.setdefault(
            row[0],
            {
                "order_id": row[0],
                "symbol": row[1],
                "side": row[2],
                "quantity": row[3],
                "price": row[4],
                "status": row[5],
                "execution_mode": row[6],
                "created_at": row[7],
                "transitions": [],
            },
        )
        if row[9]:
            order["transitions"].append(
                {
                    "from": row[8],
                    "to": row[9],
                    "reason": row[10],
                    "created_at": row[11],
                }
            )
    return {"run_id": run_id, "orders": list(orders.values())}

