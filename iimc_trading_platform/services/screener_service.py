"""Watchlist management and live technical screening over OpenAlgo data.

The screener scans the user's watchlist with real broker candles and
deterministic indicator math (shared with the backtest runtime). Symbols
whose data cannot be fetched are reported as skipped, never guessed.
"""

from __future__ import annotations

from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..infrastructure.openalgo import OpenAlgoClient
from ..strategies.rule_spec import _ema, _rsi

_CONDITIONS = {
    "rsi_below",
    "rsi_above",
    "price_above_ema",
    "price_below_ema",
    "volume_spike",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ScreenerService:
    def __init__(
        self,
        db_path: Path,
        client: OpenAlgoClient | None = None,
    ) -> None:
        self.db_path = db_path
        self.client = client

    # ------------------------------------------------------------- watchlist
    def add_symbol(
        self,
        symbol: str,
        exchange: str = "NSE",
        *,
        added_by: str = "chat",
    ) -> dict[str, Any]:
        symbol = symbol.upper().strip()
        exchange = exchange.upper().strip()
        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM watchlist_symbols "
                "WHERE symbol = ? AND exchange = ?",
                [symbol, exchange],
            )
            con.execute(
                "INSERT INTO watchlist_symbols VALUES (?, ?, ?, ?)",
                [symbol, exchange, added_by, utc_now()],
            )
        finally:
            con.close()
        return {"symbol": symbol, "exchange": exchange, "status": "added"}

    def remove_symbol(self, symbol: str, exchange: str = "NSE") -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM watchlist_symbols "
                "WHERE symbol = ? AND exchange = ?",
                [symbol.upper().strip(), exchange.upper().strip()],
            )
        finally:
            con.close()
        return {"symbol": symbol.upper().strip(), "status": "removed"}

    def list_symbols(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT symbol, exchange, added_at FROM watchlist_symbols "
                "ORDER BY symbol"
            ).fetchall()
        finally:
            con.close()
        return {
            "symbols": [
                {"symbol": row[0], "exchange": row[1], "added_at": row[2]}
                for row in rows
            ]
        }

    # -------------------------------------------------------------- screening
    def scan(
        self,
        *,
        condition: str,
        threshold: float = 30.0,
        period: int = 14,
        interval: str = "D",
        lookback_days: int = 60,
    ) -> dict[str, Any]:
        if condition not in _CONDITIONS:
            raise ValueError(
                f"Unsupported condition {condition!r}; "
                f"use one of {sorted(_CONDITIONS)}"
            )
        if self.client is None:
            raise ValueError(
                "OpenAlgo credentials are required for live screening"
            )
        watchlist = self.list_symbols()["symbols"]
        if not watchlist:
            raise ValueError(
                "The watchlist is empty. Say 'add RELIANCE to watchlist' "
                "first."
            )
        end = date.today()
        start = end - timedelta(days=lookback_days)
        matches: list[dict[str, Any]] = []
        non_matches: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for item in watchlist:
            try:
                response = self.client.historical(
                    symbol=item["symbol"],
                    exchange=item["exchange"],
                    interval=interval,
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                )
                candles = response.get("data") or []
                closes = [float(row["close"]) for row in candles]
                volumes = [float(row.get("volume", 0)) for row in candles]
            except Exception as exc:
                skipped.append(
                    {"symbol": item["symbol"], "reason": str(exc)[:120]}
                )
                continue
            if len(closes) < max(period + 1, 21):
                skipped.append(
                    {
                        "symbol": item["symbol"],
                        "reason": f"only {len(closes)} candles returned",
                    }
                )
                continue
            evaluated = self._evaluate(
                condition, threshold, period, closes, volumes,
            )
            entry = {
                "symbol": item["symbol"],
                "exchange": item["exchange"],
                "last_close": closes[-1],
                **evaluated["values"],
            }
            (matches if evaluated["matched"] else non_matches).append(entry)
        return {
            "condition": condition,
            "threshold": threshold,
            "period": period,
            "interval": interval,
            "watchlist_size": len(watchlist),
            "matches": matches,
            "non_matches": non_matches,
            "skipped": skipped,
        }

    @staticmethod
    def _evaluate(
        condition: str,
        threshold: float,
        period: int,
        closes: list[float],
        volumes: list[float],
    ) -> dict[str, Any]:
        if condition in {"rsi_below", "rsi_above"}:
            rsi_value = _rsi(closes, period)[-1]
            matched = (
                rsi_value < threshold
                if condition == "rsi_below"
                else rsi_value > threshold
            )
            return {"matched": matched, "values": {"rsi": round(rsi_value, 2)}}
        if condition in {"price_above_ema", "price_below_ema"}:
            ema_value = _ema(closes, period)[-1]
            matched = (
                closes[-1] > ema_value
                if condition == "price_above_ema"
                else closes[-1] < ema_value
            )
            return {
                "matched": matched,
                "values": {"ema": round(ema_value, 2)},
            }
        average_volume = sum(volumes[-21:-1]) / 20
        matched = bool(average_volume) and (
            volumes[-1] >= threshold * average_volume
        )
        return {
            "matched": matched,
            "values": {
                "volume": volumes[-1],
                "avg_volume_20": round(average_volume, 2),
            },
        }
