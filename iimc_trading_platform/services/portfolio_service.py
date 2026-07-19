from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..infrastructure import initialize_database


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class PortfolioRiskPolicy:
    version: str = "1.0.0"
    max_order_value: float = 2_000_000.0
    max_gross_exposure: float = 5_000_000.0
    max_symbol_exposure: float = 2_000_000.0
    max_daily_loss: float = 50_000.0
    max_concentration_pct: float = 0.60
    concentration_activation_value: float = 100_000.0
    reservation_ttl_seconds: int = 120


class PortfolioService:
    def __init__(
        self,
        db_path: Path,
        policy: PortfolioRiskPolicy | None = None,
    ) -> None:
        self.db_path = db_path
        self.policy = policy or PortfolioRiskPolicy()
        initialize_database(db_path)

    def create(
        self,
        *,
        name: str,
        starting_cash: float,
        created_by: str,
        base_currency: str = "INR",
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Portfolio name is required")
        if starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        portfolio_id = f"portfolio_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                INSERT INTO portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    portfolio_id,
                    name.strip(),
                    base_currency.upper(),
                    starting_cash,
                    starting_cash,
                    "active",
                    created_by,
                    now,
                    now,
                ],
            )
            con.execute(
                """
                INSERT INTO portfolio_ledger VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    f"ledger_{uuid.uuid4().hex[:12]}",
                    portfolio_id,
                    "opening_balance",
                    portfolio_id,
                    None,
                    None,
                    None,
                    None,
                    0.0,
                    starting_cash,
                    0.0,
                    json.dumps(
                        {"base_currency": base_currency.upper()},
                        sort_keys=True,
                    ),
                    now,
                ],
            )
            con.execute(
                """
                INSERT INTO risk_control_state VALUES (
                    'portfolio', ?, TRUE, NULL, ?, ?
                )
                """,
                [portfolio_id, created_by, now],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return self.get(portfolio_id)

    def list(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT portfolio_id, name, base_currency, starting_cash,
                       cash_balance, status, created_by, created_at, updated_at
                FROM portfolios
                ORDER BY created_at DESC
                """
            ).fetchall()
        finally:
            con.close()
        return {
            "portfolios": [
                {
                    "portfolio_id": row[0],
                    "name": row[1],
                    "base_currency": row[2],
                    "starting_cash": row[3],
                    "cash_balance": row[4],
                    "status": row[5],
                    "created_by": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                }
                for row in rows
            ]
        }

    def mark_to_market(
        self,
        portfolio_id: str,
        quote_fetcher,
    ) -> dict[str, Any]:
        """Mark open virtual positions against live quotes.

        quote_fetcher(symbol) must return a live price or raise; failed
        quotes are reported per position, never estimated.
        """
        snapshot = self.get(portfolio_id)
        marked: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        total_unrealized = 0.0
        market_value = 0.0
        for position in snapshot.get("positions", []):
            symbol = position["symbol"]
            try:
                live_price = float(quote_fetcher(symbol))
            except Exception as exc:
                errors.append({"symbol": symbol, "reason": str(exc)[:120]})
                continue
            quantity = float(position["quantity"])
            average_price = float(position["average_price"])
            unrealized = (live_price - average_price) * quantity
            total_unrealized += unrealized
            market_value += live_price * quantity
            marked.append(
                {
                    "symbol": symbol,
                    "quantity": quantity,
                    "average_price": average_price,
                    "live_price": live_price,
                    "unrealized_pnl": round(unrealized, 2),
                }
            )
        cash_balance = float(snapshot.get("cash_balance", 0))
        return {
            "portfolio_id": portfolio_id,
            "name": snapshot.get("name"),
            "cash_balance": cash_balance,
            "positions_marked": marked,
            "quote_errors": errors,
            "total_unrealized_pnl": round(total_unrealized, 2),
            "market_value": round(market_value, 2),
            "total_equity": round(cash_balance + market_value, 2),
        }

    def get(self, portfolio_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            portfolio = con.execute(
                """
                SELECT portfolio_id, name, base_currency, starting_cash,
                       cash_balance, status, created_by, created_at, updated_at
                FROM portfolios
                WHERE portfolio_id = ?
                """,
                [portfolio_id],
            ).fetchone()
            if portfolio is None:
                raise ValueError(f"Portfolio not found: {portfolio_id}")
            positions = con.execute(
                """
                SELECT symbol, quantity, average_price, last_price,
                       realized_pnl, updated_at
                FROM portfolio_positions
                WHERE portfolio_id = ? AND quantity != 0
                ORDER BY symbol
                """,
                [portfolio_id],
            ).fetchall()
            control = con.execute(
                """
                SELECT trading_enabled, reason, changed_by, updated_at
                FROM risk_control_state
                WHERE scope = 'portfolio' AND scope_id = ?
                """,
                [portfolio_id],
            ).fetchone()
            active_reservations = con.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(notional), 0)
                FROM risk_reservations
                WHERE portfolio_id = ? AND status = 'active'
                  AND expires_at > ?
                """,
                [portfolio_id, utc_now()],
            ).fetchone()
            daily_loss = con.execute(
                """
                SELECT COALESCE(
                    SUM(CASE WHEN realized_pnl < 0 THEN -realized_pnl ELSE 0 END),
                    0
                )
                FROM portfolio_ledger
                WHERE portfolio_id = ?
                  AND CAST(created_at AS DATE) = CURRENT_DATE
                """,
                [portfolio_id],
            ).fetchone()[0]
        finally:
            con.close()
        position_payload = [
            {
                "symbol": row[0],
                "quantity": row[1],
                "average_price": row[2],
                "last_price": row[3],
                "market_value": row[1] * row[3],
                "unrealized_pnl": row[1] * (row[3] - row[2]),
                "realized_pnl": row[4],
                "updated_at": row[5],
            }
            for row in positions
        ]
        gross_exposure = sum(
            abs(position["market_value"])
            for position in position_payload
        )
        net_exposure = sum(
            position["market_value"]
            for position in position_payload
        )
        return {
            "portfolio_id": portfolio[0],
            "name": portfolio[1],
            "base_currency": portfolio[2],
            "starting_cash": portfolio[3],
            "cash_balance": portfolio[4],
            "status": portfolio[5],
            "created_by": portfolio[6],
            "created_at": portfolio[7],
            "updated_at": portfolio[8],
            "positions": position_payload,
            "gross_exposure": gross_exposure,
            "net_exposure": net_exposure,
            "equity": portfolio[4] + net_exposure,
            "daily_realized_loss": float(daily_loss),
            "active_reservations": active_reservations[0],
            "reserved_notional": active_reservations[1],
            "risk_control": {
                "trading_enabled": control[0] if control else True,
                "reason": control[1] if control else None,
                "changed_by": control[2] if control else None,
                "updated_at": control[3] if control else None,
            },
        }

    def evaluate_and_reserve(
        self,
        *,
        portfolio_id: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
    ) -> dict[str, Any]:
        side = side.upper()
        symbol = symbol.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                UPDATE risk_reservations
                SET status = 'expired', updated_at = ?
                WHERE status = 'active' AND expires_at <= ?
                """,
                [now, now],
            )
            portfolio = con.execute(
                """
                SELECT cash_balance, status
                FROM portfolios
                WHERE portfolio_id = ?
                """,
                [portfolio_id],
            ).fetchone()
            if portfolio is None:
                raise ValueError(f"Portfolio not found: {portfolio_id}")
            control = con.execute(
                """
                SELECT trading_enabled, reason
                FROM risk_control_state
                WHERE scope = 'portfolio' AND scope_id = ?
                """,
                [portfolio_id],
            ).fetchone()
            positions = con.execute(
                """
                SELECT symbol, quantity, last_price
                FROM portfolio_positions
                WHERE portfolio_id = ?
                """,
                [portfolio_id],
            ).fetchall()
            reservations = con.execute(
                """
                SELECT symbol, side, quantity, notional
                FROM risk_reservations
                WHERE portfolio_id = ? AND status = 'active'
                """,
                [portfolio_id],
            ).fetchall()
            daily_loss = float(
                con.execute(
                    """
                    SELECT COALESCE(
                        SUM(CASE WHEN realized_pnl < 0 THEN -realized_pnl ELSE 0 END),
                        0
                    )
                    FROM portfolio_ledger
                    WHERE portfolio_id = ?
                      AND CAST(created_at AS DATE) = CURRENT_DATE
                    """,
                    [portfolio_id],
                ).fetchone()[0]
            )

            position_map = {
                row[0]: {"quantity": int(row[1]), "last_price": float(row[2])}
                for row in positions
            }
            gross_exposure = sum(
                abs(item["quantity"] * item["last_price"])
                for item in position_map.values()
            )
            current_symbol_value = (
                position_map.get(symbol, {}).get("quantity", 0)
                * position_map.get(symbol, {}).get("last_price", price)
            )
            reserved_buy_notional = sum(
                float(row[3]) for row in reservations if row[1] == "BUY"
            )
            reserved_symbol_buy_notional = sum(
                float(row[3])
                for row in reservations
                if row[0] == symbol and row[1] == "BUY"
            )
            reserved_sell_quantity = sum(
                int(row[2])
                for row in reservations
                if row[0] == symbol and row[1] == "SELL"
            )
            trading_enabled = control[0] if control else True
            checks: dict[str, Any] = {
                "portfolio_active": portfolio[1] == "active",
                "kill_switch_clear": bool(trading_enabled),
                "daily_loss": {
                    "value": daily_loss,
                    "limit": self.policy.max_daily_loss,
                    "passed": daily_loss < self.policy.max_daily_loss,
                },
            }
            approved_quantity = quantity
            if side == "BUY":
                approved_quantity = min(
                    approved_quantity,
                    math.floor(self.policy.max_order_value / price),
                    math.floor(
                        max(
                            float(portfolio[0]) - reserved_buy_notional,
                            0.0,
                        )
                        / price
                    ),
                    math.floor(
                        max(
                            self.policy.max_gross_exposure
                            - gross_exposure
                            - reserved_buy_notional,
                            0.0,
                        )
                        / price
                    ),
                    math.floor(
                        max(
                            self.policy.max_symbol_exposure
                            - current_symbol_value,
                            0.0,
                        )
                        / price
                    ),
                )
            else:
                available = (
                    position_map.get(symbol, {}).get("quantity", 0)
                    - reserved_sell_quantity
                )
                approved_quantity = min(approved_quantity, max(available, 0))

            proposed_symbol_value = (
                current_symbol_value
                + reserved_symbol_buy_notional
                + approved_quantity * price
                if side == "BUY"
                else max(
                    current_symbol_value - approved_quantity * price,
                    0.0,
                )
            )
            proposed_gross = (
                gross_exposure
                + reserved_buy_notional
                + approved_quantity * price
                if side == "BUY"
                else max(gross_exposure - approved_quantity * price, 0.0)
            )
            concentration = (
                proposed_symbol_value / proposed_gross
                if proposed_gross
                else 0.0
            )
            concentration_enforced = (
                proposed_gross >= self.policy.concentration_activation_value
            )
            checks.update(
                {
                    "requested_notional": quantity * price,
                    "approved_notional": approved_quantity * price,
                    "cash_available": float(portfolio[0]) - reserved_buy_notional,
                    "gross_exposure_before": gross_exposure,
                    "gross_exposure_after": proposed_gross,
                    "symbol_exposure_after": proposed_symbol_value,
                    "concentration_pct": concentration,
                    "concentration_enforced": concentration_enforced,
                    "concentration_passed": (
                        not concentration_enforced
                        or concentration <= self.policy.max_concentration_pct
                    ),
                }
            )
            hard_checks_pass = (
                checks["portfolio_active"]
                and checks["kill_switch_clear"]
                and checks["daily_loss"]["passed"]
                and approved_quantity > 0
                and checks["concentration_passed"]
            )
            if not hard_checks_pass:
                approved_quantity = 0
                checks["approved_notional"] = 0.0
                checks["gross_exposure_after"] = (
                    gross_exposure + reserved_buy_notional
                )
                checks["symbol_exposure_after"] = (
                    current_symbol_value + reserved_symbol_buy_notional
                )
            approved = approved_quantity > 0
            reason = _portfolio_risk_reason(
                requested=quantity,
                approved=approved_quantity,
                checks=checks,
                control_reason=control[1] if control else None,
            )
            decision_id = f"prisk_{uuid.uuid4().hex[:12]}"
            con.execute(
                """
                INSERT INTO portfolio_risk_decisions VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    decision_id,
                    portfolio_id,
                    symbol,
                    side,
                    quantity,
                    approved_quantity,
                    price,
                    approved,
                    reason,
                    json.dumps(checks, sort_keys=True, default=str),
                    self.policy.version,
                    now,
                ],
            )
            reservation_id = None
            expires_at = None
            if approved:
                reservation_id = f"reserve_{uuid.uuid4().hex[:12]}"
                expires_at = now + timedelta(
                    seconds=self.policy.reservation_ttl_seconds
                )
                con.execute(
                    """
                    INSERT INTO risk_reservations VALUES (
                        ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?
                    )
                    """,
                    [
                        reservation_id,
                        decision_id,
                        portfolio_id,
                        symbol,
                        side,
                        approved_quantity,
                        approved_quantity * price,
                        expires_at,
                        now,
                        now,
                    ],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return {
            "decision_id": decision_id,
            "portfolio_id": portfolio_id,
            "symbol": symbol,
            "side": side,
            "requested_quantity": quantity,
            "approved_quantity": approved_quantity,
            "approved": approved,
            "reason": reason,
            "checks": checks,
            "policy_version": self.policy.version,
            "reservation_id": reservation_id,
            "reservation_expires_at": expires_at,
        }

    def apply_fill(
        self,
        *,
        portfolio_id: str,
        reservation_id: str,
        reference_id: str,
        price: float,
        fees: float = 0.0,
    ) -> dict[str, Any]:
        if price <= 0 or fees < 0:
            raise ValueError("price must be positive and fees non-negative")
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            existing = con.execute(
                """
                SELECT ledger_entry_id
                FROM portfolio_ledger
                WHERE portfolio_id = ? AND event_type = 'fill'
                  AND reference_id = ?
                """,
                [portfolio_id, reference_id],
            ).fetchone()
            if existing:
                con.execute("ROLLBACK")
                return self.get(portfolio_id)
            reservation = con.execute(
                """
                SELECT symbol, side, quantity, status, expires_at
                FROM risk_reservations
                WHERE reservation_id = ? AND portfolio_id = ?
                """,
                [reservation_id, portfolio_id],
            ).fetchone()
            if reservation is None:
                raise ValueError(f"Reservation not found: {reservation_id}")
            if reservation[3] != "active" or reservation[4] <= now:
                raise ValueError("Risk reservation is not active")
            portfolio = con.execute(
                "SELECT cash_balance FROM portfolios WHERE portfolio_id = ?",
                [portfolio_id],
            ).fetchone()
            position = con.execute(
                """
                SELECT quantity, average_price, realized_pnl
                FROM portfolio_positions
                WHERE portfolio_id = ? AND symbol = ?
                """,
                [portfolio_id, reservation[0]],
            ).fetchone()
            symbol, side, quantity = (
                reservation[0],
                reservation[1],
                int(reservation[2]),
            )
            current_quantity = int(position[0]) if position else 0
            current_average = float(position[1]) if position else 0.0
            current_realized = float(position[2]) if position else 0.0
            if side == "BUY":
                cash_delta = -(price * quantity + fees)
                if float(portfolio[0]) + cash_delta < -1e-9:
                    raise ValueError("Insufficient cash at fill price")
                new_quantity = current_quantity + quantity
                new_average = (
                    (
                        current_quantity * current_average
                        + quantity * price
                    )
                    / new_quantity
                )
                realized_pnl = -fees
            else:
                if quantity > current_quantity:
                    raise ValueError("Sell fill exceeds current long position")
                cash_delta = price * quantity - fees
                realized_pnl = (
                    price - current_average
                ) * quantity - fees
                new_quantity = current_quantity - quantity
                new_average = current_average if new_quantity else 0.0

            con.execute(
                """
                INSERT INTO portfolio_positions VALUES (
                    ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (portfolio_id, symbol) DO UPDATE SET
                    quantity = EXCLUDED.quantity,
                    average_price = EXCLUDED.average_price,
                    last_price = EXCLUDED.last_price,
                    realized_pnl = EXCLUDED.realized_pnl,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    portfolio_id,
                    symbol,
                    new_quantity,
                    new_average,
                    price,
                    current_realized + realized_pnl,
                    now,
                ],
            )
            con.execute(
                """
                UPDATE portfolios
                SET cash_balance = cash_balance + ?, updated_at = ?
                WHERE portfolio_id = ?
                """,
                [cash_delta, now, portfolio_id],
            )
            con.execute(
                """
                INSERT INTO portfolio_ledger VALUES (
                    ?, ?, 'fill', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    f"ledger_{uuid.uuid4().hex[:12]}",
                    portfolio_id,
                    reference_id,
                    symbol,
                    side,
                    quantity,
                    price,
                    fees,
                    cash_delta,
                    realized_pnl,
                    json.dumps(
                        {"reservation_id": reservation_id},
                        sort_keys=True,
                    ),
                    now,
                ],
            )
            con.execute(
                """
                UPDATE risk_reservations
                SET status = 'consumed', updated_at = ?
                WHERE reservation_id = ?
                """,
                [now, reservation_id],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return self.get(portfolio_id)

    def release_reservation(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                UPDATE risk_reservations
                SET status = 'released', updated_at = ?
                WHERE reservation_id = ? AND status = 'active'
                RETURNING portfolio_id
                """,
                [utc_now(), reservation_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError("Active reservation not found")
        return self.get(row[0])

    def set_trading_enabled(
        self,
        *,
        portfolio_id: str,
        enabled: bool,
        reason: str,
        changed_by: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("A reason is required")
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            exists = con.execute(
                "SELECT COUNT(*) FROM portfolios WHERE portfolio_id = ?",
                [portfolio_id],
            ).fetchone()[0]
            if not exists:
                raise ValueError(f"Portfolio not found: {portfolio_id}")
            now = utc_now()
            con.execute(
                """
                INSERT INTO risk_control_state VALUES (
                    'portfolio', ?, ?, ?, ?, ?
                )
                ON CONFLICT (scope, scope_id) DO UPDATE SET
                    trading_enabled = EXCLUDED.trading_enabled,
                    reason = EXCLUDED.reason,
                    changed_by = EXCLUDED.changed_by,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    portfolio_id,
                    enabled,
                    reason.strip(),
                    changed_by,
                    now,
                ],
            )
            con.execute(
                """
                INSERT INTO portfolio_ledger VALUES (
                    ?, ?, 'trading_control', ?, NULL, NULL, NULL, NULL,
                    0, 0, 0, ?, ?
                )
                """,
                [
                    f"ledger_{uuid.uuid4().hex[:12]}",
                    portfolio_id,
                    f"control_{uuid.uuid4().hex[:12]}",
                    json.dumps(
                        {
                            "enabled": enabled,
                            "reason": reason.strip(),
                            "changed_by": changed_by,
                        },
                        sort_keys=True,
                    ),
                    now,
                ],
            )
            if not enabled:
                con.execute(
                    """
                    UPDATE risk_reservations
                    SET status = 'released', updated_at = ?
                    WHERE portfolio_id = ? AND status = 'active'
                    """,
                    [now, portfolio_id],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return self.get(portfolio_id)


def _portfolio_risk_reason(
    *,
    requested: int,
    approved: int,
    checks: dict[str, Any],
    control_reason: str | None,
) -> str:
    if not checks["portfolio_active"]:
        return "Portfolio is not active"
    if not checks["kill_switch_clear"]:
        return f"Portfolio kill switch is active: {control_reason or 'no reason'}"
    if not checks["daily_loss"]["passed"]:
        return "Portfolio daily loss limit has been reached"
    if not checks["concentration_passed"]:
        return "Proposed order exceeds the concentration limit"
    if approved <= 0:
        return "No quantity remains after cash, position, or exposure limits"
    if approved < requested:
        return f"Quantity resized from {requested} to {approved}"
    return "Portfolio risk checks passed and exposure was reserved"
