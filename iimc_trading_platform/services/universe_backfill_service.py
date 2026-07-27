"""Backfill price history for a whole universe, resumably.

The platform's agents can only answer questions about symbols it holds data
for. This walks a universe (the NIFTY 50 by default), importing history one
symbol at a time through the existing
:class:`OpenAlgoHistoryImportService`, and records the outcome per symbol so a
run that is interrupted — or that hits an expired broker token halfway —
resumes where it stopped instead of starting over.

Design notes:

- **Resumable by construction.** Progress lives in ``universe_backfill_status``
  keyed by (symbol, exchange, interval), so "already imported" is a fact in the
  database rather than something inferred from a log.
- **Bounded per invocation.** ``max_symbols`` limits how much one call does,
  so a scheduled job takes small bites instead of one long blocking sweep.
- **Honest failure.** A symbol whose import fails is recorded with its reason
  and retried next time; it never silently disappears, and a failure for one
  symbol never aborts the rest.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .screener_service import NIFTY_50

_UNIVERSES: dict[str, tuple[str, ...]] = {"nifty50": NIFTY_50}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UniverseBackfillService:
    def __init__(self, db_path: Path, history_import: Any) -> None:
        self.db_path = db_path
        self.history_import = history_import

    # -- status ---------------------------------------------------------------

    def status(self, universe: str = "nifty50") -> dict[str, Any]:
        symbols = _UNIVERSES.get(universe, NIFTY_50)
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT symbol, interval, status, rows_imported, reason, "
                "updated_at FROM universe_backfill_status"
            ).fetchall()
        finally:
            con.close()
        by_symbol = {
            r[0]: {
                "symbol": r[0],
                "interval": r[1],
                "status": r[2],
                "rows_imported": r[3],
                "reason": r[4],
                "updated_at": _iso(r[5]),
            }
            for r in rows
        }
        done = [s for s in symbols if by_symbol.get(s, {}).get("status") == "ok"]
        failed = [
            s for s in symbols if by_symbol.get(s, {}).get("status") == "failed"
        ]
        return {
            "universe": universe,
            "total": len(symbols),
            "imported": len(done),
            "failed": len(failed),
            "pending": len(symbols) - len(done) - len(failed),
            "coverage_pct": round(len(done) / len(symbols) * 100, 1) if symbols else 0.0,
            "symbols": [by_symbol.get(s, {"symbol": s, "status": "pending"}) for s in symbols],
        }

    # -- backfill -------------------------------------------------------------

    def run(
        self,
        *,
        universe: str = "nifty50",
        interval: str = "D",
        exchange: str = "NSE",
        lookback_days: int = 365,
        max_symbols: int = 5,
        retry_failed: bool = True,
    ) -> dict[str, Any]:
        """Import the next ``max_symbols`` symbols that still need data."""

        symbols = _UNIVERSES.get(universe)
        if symbols is None:
            raise ValueError(
                f"Unknown universe {universe!r}; known: {sorted(_UNIVERSES)}"
            )
        pending = self._pending(symbols, interval, retry_failed=retry_failed)
        end = date.today()
        start = end - timedelta(days=lookback_days)
        results: list[dict[str, Any]] = []
        for symbol in pending[:max_symbols]:
            try:
                imported = self.history_import.import_history(
                    symbol=symbol,
                    exchange=exchange,
                    asset_class="equity",
                    interval=interval,
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                )
                rows = int(
                    imported.get("row_count")
                    or imported.get("rows_imported")
                    or 0
                )
                self._record(symbol, interval, "ok", rows, None)
                results.append(
                    {"symbol": symbol, "status": "ok", "rows": rows}
                )
            except Exception as exc:  # noqa: BLE001 - recorded, never fatal
                reason = str(exc)[:200]
                self._record(symbol, interval, "failed", 0, reason)
                results.append(
                    {"symbol": symbol, "status": "failed", "reason": reason}
                )
        return {
            "universe": universe,
            "interval": interval,
            "attempted": len(results),
            "remaining": max(len(pending) - len(results), 0),
            "results": results,
        }

    def _pending(
        self, symbols: tuple[str, ...], interval: str, *, retry_failed: bool
    ) -> list[str]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT symbol, status FROM universe_backfill_status "
                "WHERE interval = ?",
                [interval],
            ).fetchall()
        finally:
            con.close()
        state = {r[0]: r[1] for r in rows}
        return [
            symbol
            for symbol in symbols
            if state.get(symbol) != "ok"
            and (retry_failed or state.get(symbol) != "failed")
        ]

    def _record(
        self,
        symbol: str,
        interval: str,
        status: str,
        rows: int,
        reason: str | None,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM universe_backfill_status "
                "WHERE symbol = ? AND interval = ?",
                [symbol, interval],
            )
            con.execute(
                "INSERT INTO universe_backfill_status VALUES (?, ?, ?, ?, ?, ?)",
                [symbol, interval, status, rows, reason, _utc_now()],
            )
        finally:
            con.close()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (datetime, date)) else str(value)
