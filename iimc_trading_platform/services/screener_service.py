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
from .instrument_names import company_name

_CONDITIONS = {
    "rsi_below",
    "rsi_above",
    "price_above_ema",
    "price_below_ema",
    "volume_spike",
}

# Curated NIFTY 50 constituents (NSE symbols). Index membership is revised
# periodically by NSE; this is a representative snapshot used as a ready-made
# scanning universe so the client never has to build a watchlist by hand.
NIFTY_50 = (
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB",
    "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK",
    "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
    "INDUSINDBK", "INFY", "ITC", "JSWSTEEL", "KOTAKBANK",
    "LT", "LTIM", "M&M", "MARUTI", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATAMOTORS",
    "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO",
)

_UNIVERSES: dict[str, tuple[str, ...]] = {
    "nifty50": NIFTY_50,
    "nifty": NIFTY_50,
    "nifty_50": NIFTY_50,
}


def resolve_universe(name: str) -> list[dict[str, str]] | None:
    """Return [{symbol, exchange}] for a known index name, else None."""
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    symbols = _UNIVERSES.get(key) or _UNIVERSES.get(name.lower().replace(" ", ""))
    if not symbols:
        return None
    return [{"symbol": symbol, "exchange": "NSE"} for symbol in symbols]


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

    def technical_snapshot(
        self,
        symbol: str,
        exchange: str = "NSE",
        *,
        interval: str = "D",
        period: int = 14,
        lookback_days: int = 200,
    ) -> dict[str, Any]:
        """RSI/EMA/trend read for a single symbol from live broker candles.

        Returns a ``status`` of ``ok`` or ``unavailable`` (with a reason) and
        never fabricates values when data cannot be fetched.
        """
        symbol = symbol.upper().strip()
        exchange = exchange.upper().strip()
        if self.client is None:
            return {"status": "unavailable", "reason": "broker not configured"}
        end = date.today()
        start = end - timedelta(days=lookback_days)
        try:
            response = self.client.historical(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
            candles = response.get("data") or []
            closes = [float(row["close"]) for row in candles]
        except Exception as exc:  # noqa: BLE001 - reported, not fabricated
            return {"status": "unavailable", "reason": str(exc)[:160]}
        if len(closes) < max(period + 1, 55):
            return {
                "status": "unavailable",
                "reason": f"only {len(closes)} candles returned",
            }
        last = closes[-1]
        rsi_value = round(_rsi(closes, period)[-1], 2)
        ema20 = round(_ema(closes, 20)[-1], 2)
        ema50 = round(_ema(closes, 50)[-1], 2)
        if ema20 > ema50 and last > ema50:
            trend = "uptrend"
        elif ema20 < ema50 and last < ema50:
            trend = "downtrend"
        else:
            trend = "sideways"
        if rsi_value >= 70:
            momentum = "overbought"
        elif rsi_value <= 30:
            momentum = "oversold"
        else:
            momentum = "neutral"
        return {
            "status": "ok",
            "symbol": symbol,
            "exchange": exchange,
            "last_close": round(last, 2),
            "rsi": rsi_value,
            "ema20": ema20,
            "ema50": ema50,
            "trend": trend,
            "momentum": momentum,
            "candles_used": len(closes),
        }

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
        universe: str | None = None,
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
        # A named index (e.g. NIFTY 50) is scanned directly; otherwise fall
        # back to the user's saved watchlist.
        if universe:
            symbols = resolve_universe(universe)
            if symbols is None:
                raise ValueError(
                    f"I don't have a built-in list for {universe!r}. "
                    "Try 'NIFTY 50', or add symbols to your watchlist and "
                    "screen that."
                )
            universe_label = "nifty50"
        else:
            symbols = self.list_symbols()["symbols"]
            universe_label = "watchlist"
            if not symbols:
                raise ValueError(
                    "Tell me which stocks to scan — e.g. 'screen NIFTY 50 "
                    "for RSI below 30' — or add symbols to your watchlist "
                    "first."
                )
        end = date.today()
        start = end - timedelta(days=lookback_days)
        matches: list[dict[str, Any]] = []
        non_matches: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for item in symbols:
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
                "company_name": company_name(item["symbol"], item["exchange"]),
                "last_close": closes[-1],
                **evaluated["values"],
            }
            (matches if evaluated["matched"] else non_matches).append(entry)
        return {
            "condition": condition,
            "threshold": threshold,
            "period": period,
            "interval": interval,
            "universe": universe_label,
            "universe_size": len(symbols),
            "watchlist_size": len(symbols),
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
