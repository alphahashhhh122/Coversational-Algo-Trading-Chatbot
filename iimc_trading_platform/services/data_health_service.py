"""What data the platform actually holds, per symbol.

Agents fail in confusing ways when the data they need simply isn't there — the
fundamental analyst returns "no statements stored", the comparator can only
compare what it has, and a backtest quietly has nothing to run on. This turns
that into a visible, up-front fact: for every symbol in a universe, does the
platform hold price history, fundamentals, or neither?

Read-only. It reports coverage; it does not fetch anything (that is the
backfill job's role).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..db import connect
from .screener_service import NIFTY_50


class DataHealthService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def coverage(self, universe: str = "nifty50") -> dict[str, Any]:
        symbols = NIFTY_50 if universe == "nifty50" else NIFTY_50
        con = connect(self.db_path)
        try:
            price_rows = con.execute(
                "SELECT symbol, interval, row_count, end_ts "
                "FROM data_catalog WHERE quality_status NOT IN "
                "('rejected', 'empty')"
            ).fetchall()
            fundamental_rows = con.execute(
                "SELECT symbol, COUNT(*) FROM financial_statements GROUP BY symbol"
            ).fetchall()
        finally:
            con.close()

        price: dict[str, dict[str, Any]] = {}
        for symbol, interval, rows, end_date in price_rows:
            entry = price.setdefault(
                symbol, {"intervals": [], "rows": 0, "latest": None}
            )
            entry["intervals"].append(interval)
            entry["rows"] += int(rows or 0)
            latest = _as_date(end_date)
            if latest and (entry["latest"] is None or latest > entry["latest"]):
                entry["latest"] = latest
        fundamentals = {row[0]: int(row[1]) for row in fundamental_rows}

        per_symbol = []
        for symbol in symbols:
            has_price = symbol in price
            has_fundamentals = fundamentals.get(symbol, 0) > 0
            per_symbol.append(
                {
                    "symbol": symbol,
                    "has_price_history": has_price,
                    "price_rows": price.get(symbol, {}).get("rows", 0),
                    "intervals": sorted(
                        set(price.get(symbol, {}).get("intervals", []))
                    ),
                    "latest_bar": _iso(price.get(symbol, {}).get("latest")),
                    "has_fundamentals": has_fundamentals,
                    "statement_count": fundamentals.get(symbol, 0),
                    "ready_for": _ready_for(has_price, has_fundamentals),
                }
            )
        with_price = sum(1 for s in per_symbol if s["has_price_history"])
        with_fundamentals = sum(1 for s in per_symbol if s["has_fundamentals"])
        return {
            "universe": universe,
            "symbol_count": len(symbols),
            "with_price_history": with_price,
            "with_fundamentals": with_fundamentals,
            "price_coverage_pct": _pct(with_price, len(symbols)),
            "fundamentals_coverage_pct": _pct(with_fundamentals, len(symbols)),
            "symbols": per_symbol,
            # Named plainly so the gap is actionable rather than mysterious.
            "gaps": _gap_summary(with_price, with_fundamentals, len(symbols)),
        }


def _ready_for(has_price: bool, has_fundamentals: bool) -> list[str]:
    """Which agents can actually work on this symbol today."""
    ready = []
    if has_price:
        ready += ["backtest", "walk_forward", "technicals", "arena"]
    if has_fundamentals:
        ready += ["fundamental_analysis", "comparison"]
    return ready


def _gap_summary(with_price: int, with_fundamentals: int, total: int) -> list[str]:
    gaps = []
    if with_price < total:
        gaps.append(
            f"{total - with_price} of {total} symbols have no price history — "
            "run the universe backfill to make them researchable."
        )
    if with_fundamentals < total:
        gaps.append(
            f"{total - with_fundamentals} of {total} symbols have no financial "
            "statements — the fundamental analyst and comparator cannot cover them."
        )
    return gaps


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (date, datetime)) else None
