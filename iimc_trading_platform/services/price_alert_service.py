"""Price alerts evaluated against real OpenAlgo quotes.

Alerts trigger only on broker-provided prices; evaluation records the
observed price and never infers a trigger from missing data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..infrastructure.openalgo import OpenAlgoClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PriceAlertService:
    def __init__(
        self,
        db_path: Path,
        client: OpenAlgoClient | None = None,
    ) -> None:
        self.db_path = db_path
        self.client = client

    def create(
        self,
        *,
        symbol: str,
        direction: str,
        threshold: float,
        exchange: str = "NSE",
        created_by: str = "chat",
    ) -> dict[str, Any]:
        direction = direction.lower().strip()
        if direction not in {"above", "below"}:
            raise ValueError("direction must be 'above' or 'below'")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        alert_id = f"palert_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO price_alerts VALUES "
                "(?, ?, ?, ?, ?, 'active', ?, ?, NULL, NULL, NULL)",
                [
                    alert_id,
                    symbol.upper().strip(),
                    exchange.upper().strip(),
                    direction,
                    float(threshold),
                    created_by,
                    utc_now(),
                ],
            )
        finally:
            con.close()
        return {
            "alert_id": alert_id,
            "symbol": symbol.upper().strip(),
            "exchange": exchange.upper().strip(),
            "direction": direction,
            "threshold": float(threshold),
            "status": "active",
        }

    def list(self, *, include_triggered: bool = True) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT alert_id, symbol, exchange, direction, threshold,
                       status, last_price, last_checked_at, triggered_at
                FROM price_alerts
                ORDER BY created_at DESC
                LIMIT 200
                """
            ).fetchall()
        finally:
            con.close()
        alerts = [
            {
                "alert_id": row[0],
                "symbol": row[1],
                "exchange": row[2],
                "direction": row[3],
                "threshold": row[4],
                "status": row[5],
                "last_price": row[6],
                "last_checked_at": row[7],
                "triggered_at": row[8],
            }
            for row in rows
            if include_triggered or row[5] == "active"
        ]
        return {"alerts": alerts}

    def evaluate(self) -> dict[str, Any]:
        """Check active alerts against live quotes; mark crossed ones."""
        if self.client is None:
            raise ValueError(
                "OpenAlgo credentials are required to evaluate price alerts"
            )
        active = [
            item
            for item in self.list()["alerts"]
            if item["status"] == "active"
        ]
        triggered: list[dict[str, Any]] = []
        errors: list[str] = []
        now = utc_now()
        con = connect(self.db_path)
        try:
            for alert in active:
                try:
                    quote = self.client.quote(
                        symbol=alert["symbol"],
                        exchange=alert["exchange"],
                    )
                    data = quote.get("data") or {}
                    price = float(data.get("ltp"))
                except Exception as exc:
                    errors.append(f"{alert['symbol']}: {exc}")
                    continue
                crossed = (
                    price >= alert["threshold"]
                    if alert["direction"] == "above"
                    else price <= alert["threshold"]
                )
                if crossed:
                    con.execute(
                        "UPDATE price_alerts SET status='triggered', "
                        "last_price=?, last_checked_at=?, triggered_at=? "
                        "WHERE alert_id=?",
                        [price, now, now, alert["alert_id"]],
                    )
                    triggered.append({**alert, "last_price": price})
                else:
                    con.execute(
                        "UPDATE price_alerts SET last_price=?, "
                        "last_checked_at=? WHERE alert_id=?",
                        [price, now, alert["alert_id"]],
                    )
        finally:
            con.close()
        return {
            "checked": len(active),
            "triggered": triggered,
            "errors": errors,
        }
