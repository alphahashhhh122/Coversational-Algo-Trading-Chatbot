"""A watch/monitor agent for technical conditions.

Complements price alerts (`PriceAlertService`, which watches raw price
thresholds) by watching *technical* conditions — RSI levels and price-vs-EMA —
evaluated against real broker candles via ``ScreenerService.technical_snapshot``.

A watch only ever **notifies**; it never trades and never prepares an order.
``evaluate`` is exposed so a scheduled job can run it proactively, and the chat
tool ``check_watches`` runs it on demand so the user can see it work now. Nothing
is fabricated — a watch with no data is reported as unchecked, not triggered.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .screener_service import ScreenerService

# threshold_required: RSI conditions need a level; price-vs-EMA conditions don't.
_CONDITIONS = {
    "rsi_below": True,
    "rsi_above": True,
    "price_above_ema20": False,
    "price_below_ema20": False,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WatchService:
    def __init__(self, db_path: Path, screener: ScreenerService) -> None:
        self.db_path = db_path
        self.screener = screener

    # -- writes ---------------------------------------------------------------

    def create(
        self,
        *,
        symbol: str,
        condition: str,
        threshold: float | None = None,
        exchange: str = "NSE",
        created_by: str = "chat",
    ) -> dict[str, Any]:
        condition = condition.lower().strip()
        if condition not in _CONDITIONS:
            raise ValueError(
                f"I can watch {sorted(_CONDITIONS)}, not {condition!r}."
            )
        if _CONDITIONS[condition]:
            if threshold is None:
                raise ValueError(f"{condition} needs a level, e.g. 'RSI below 30'.")
            if not 0 < float(threshold) < 100:
                raise ValueError("An RSI level must be between 0 and 100.")
        else:
            threshold = None
        watch_id = f"watch_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO technical_watches VALUES "
                "(?, ?, ?, ?, ?, 'active', ?, ?, NULL, NULL, NULL)",
                [
                    watch_id,
                    symbol.upper().strip(),
                    exchange.upper().strip(),
                    condition,
                    (float(threshold) if threshold is not None else None),
                    created_by,
                    _utc_now(),
                ],
            )
        finally:
            con.close()
        return {
            "watch_id": watch_id,
            "symbol": symbol.upper().strip(),
            "exchange": exchange.upper().strip(),
            "condition": condition,
            "threshold": threshold,
            "status": "active",
        }

    def remove(self, symbol: str, exchange: str = "NSE") -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM technical_watches WHERE symbol = ? AND exchange = ?",
                [symbol.upper().strip(), exchange.upper().strip()],
            )
        finally:
            con.close()
        return {"symbol": symbol.upper().strip(), "status": "removed"}

    # -- reads ----------------------------------------------------------------

    def list(self, *, include_triggered: bool = True) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT watch_id, symbol, exchange, condition, threshold, status,
                       last_value, last_checked_at, triggered_at
                FROM technical_watches
                ORDER BY created_at DESC LIMIT 200
                """
            ).fetchall()
        finally:
            con.close()
        watches = [
            {
                "watch_id": r[0],
                "symbol": r[1],
                "exchange": r[2],
                "condition": r[3],
                "threshold": r[4],
                "status": r[5],
                "last_value": r[6],
                "last_checked_at": _iso(r[7]),
                "triggered_at": _iso(r[8]),
            }
            for r in rows
            if include_triggered or r[5] == "active"
        ]
        return {"watches": watches}

    # -- evaluation -----------------------------------------------------------

    def evaluate(self) -> dict[str, Any]:
        """Check active watches against fresh technicals; flag any that fire."""

        active = [w for w in self.list()["watches"] if w["status"] == "active"]
        fired: list[dict[str, Any]] = []
        errors: list[str] = []
        now = _utc_now()
        con = connect(self.db_path)
        try:
            for watch in active:
                snapshot = self.screener.technical_snapshot(
                    watch["symbol"], watch["exchange"]
                )
                if snapshot.get("status") != "ok":
                    errors.append(f"{watch['symbol']}: {snapshot.get('reason')}")
                    continue
                value, met = _condition_met(watch, snapshot)
                if met:
                    con.execute(
                        "UPDATE technical_watches SET status='triggered', "
                        "last_value=?, last_checked_at=?, triggered_at=? "
                        "WHERE watch_id=?",
                        [value, now, now, watch["watch_id"]],
                    )
                    fired.append({**watch, "last_value": value})
                else:
                    con.execute(
                        "UPDATE technical_watches SET last_value=?, "
                        "last_checked_at=? WHERE watch_id=?",
                        [value, now, watch["watch_id"]],
                    )
        finally:
            con.close()
        return {"checked": len(active), "fired": fired, "errors": errors}


def _condition_met(
    watch: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[float, bool]:
    condition = watch["condition"]
    if condition == "rsi_below":
        rsi = snapshot["rsi"]
        return rsi, rsi < watch["threshold"]
    if condition == "rsi_above":
        rsi = snapshot["rsi"]
        return rsi, rsi > watch["threshold"]
    if condition == "price_above_ema20":
        last = snapshot["last_close"]
        return last, last > snapshot["ema20"]
    if condition == "price_below_ema20":
        last = snapshot["last_close"]
        return last, last < snapshot["ema20"]
    return 0.0, False


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


# -- natural-language helpers (used by the chat router) -----------------------

def parse_watch_request(message: str) -> dict[str, Any] | None:
    """Pull a symbol + technical condition out of a 'watch ...' request.

    Returns ``{symbol, condition, threshold}`` or ``None`` if it isn't a
    technical watch (e.g. a price alert, which the price-alert tool handles).
    """

    text = message.lower()
    rsi_below = re.search(r"rsi\s*(?:is\s*)?(?:below|under|<|drops?\s+below)\s*(\d{1,2})", text)
    rsi_above = re.search(r"rsi\s*(?:is\s*)?(?:above|over|>|goes?\s+above)\s*(\d{1,3})", text)
    condition: str | None = None
    threshold: float | None = None
    if rsi_below:
        condition, threshold = "rsi_below", float(rsi_below.group(1))
    elif rsi_above:
        condition, threshold = "rsi_above", float(rsi_above.group(1))
    elif re.search(r"(?:price\s+)?(?:above|crosses?\s+above|over)\s+(?:the\s+)?ema", text):
        condition = "price_above_ema20"
    elif re.search(r"(?:price\s+)?(?:below|crosses?\s+below|under)\s+(?:the\s+)?ema", text):
        condition = "price_below_ema20"
    if condition is None:
        return None
    return {"condition": condition, "threshold": threshold}
