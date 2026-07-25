"""The Arena: season-based agent competition (docs/ATL_TRANSITION.md §4.4).

Enrolled strategy agents compete on **real market data** through an internal
simulated ledger — the same fill/fee/slippage machinery the research backtests
use (``BacktestService.simulate_only``). There is deliberately **no broker code
path here, not even a sandbox one**: agents can compete autonomously precisely
because nothing they do can reach a real account.

A daily tick recomputes each entry's equity over the season window and records
a snapshot. When market data is unavailable the day is recorded as
``data_missing`` for everyone — never interpolated, never silently skipped.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect

_STARTING_EQUITY = 1_000_000.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ArenaService:
    def __init__(self, db_path: Path, backtest_service: Any) -> None:
        self.db_path = db_path
        self.backtest_service = backtest_service

    # -- seasons --------------------------------------------------------------

    def create_season(
        self,
        *,
        name: str,
        symbol: str,
        exchange: str = "NSE",
        starting_equity: float = _STARTING_EQUITY,
    ) -> dict[str, Any]:
        season_id = f"season_{uuid.uuid4().hex[:10]}"
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO arena_seasons VALUES (?, ?, ?, ?, ?, 'open', ?, NULL)",
                [
                    season_id,
                    name,
                    symbol.upper().strip(),
                    exchange.upper().strip(),
                    float(starting_equity),
                    _utc_now(),
                ],
            )
        finally:
            con.close()
        return {
            "season_id": season_id,
            "name": name,
            "symbol": symbol.upper().strip(),
            "status": "open",
            "starting_equity": float(starting_equity),
        }

    def list_seasons(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT s.season_id, s.name, s.symbol, s.exchange,
                       s.starting_equity, s.status, s.started_at,
                       (SELECT COUNT(*) FROM arena_entries e
                         WHERE e.season_id = s.season_id)
                FROM arena_seasons s ORDER BY s.started_at DESC
                """
            ).fetchall()
        finally:
            con.close()
        return {
            "seasons": [
                {
                    "season_id": r[0],
                    "name": r[1],
                    "symbol": r[2],
                    "exchange": r[3],
                    "starting_equity": r[4],
                    "status": r[5],
                    "started_at": _iso(r[6]),
                    "entries": int(r[7] or 0),
                }
                for r in rows
            ]
        }

    # -- entries --------------------------------------------------------------

    def enroll(
        self,
        *,
        season_id: str,
        agent_id: str,
        strategy_name: str = "ema_crossover",
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import json

        entry_id = f"entry_{uuid.uuid4().hex[:10]}"
        con = connect(self.db_path)
        try:
            existing = con.execute(
                "SELECT entry_id FROM arena_entries "
                "WHERE season_id = ? AND agent_id = ?",
                [season_id, agent_id],
            ).fetchone()
            if existing:
                return {"entry_id": existing[0], "status": "already_enrolled"}
            con.execute(
                "INSERT INTO arena_entries VALUES (?, ?, ?, ?, ?, ?)",
                [
                    entry_id,
                    season_id,
                    agent_id,
                    json.dumps(parameters or {}),
                    strategy_name,
                    _utc_now(),
                ],
            )
        finally:
            con.close()
        return {"entry_id": entry_id, "status": "enrolled", "agent_id": agent_id}

    # -- daily tick -----------------------------------------------------------

    def tick(self, season_id: str, *, dataset_id: str | None = None) -> dict[str, Any]:
        """Recompute each entry's standing on real data and snapshot it.

        Returns per-entry results; entries whose data is unavailable are
        recorded as ``data_missing`` rather than being given a fabricated
        equity.
        """

        import json

        con = connect(self.db_path)
        try:
            season = con.execute(
                "SELECT symbol, exchange, starting_equity FROM arena_seasons "
                "WHERE season_id = ?",
                [season_id],
            ).fetchone()
            if season is None:
                raise ValueError(f"unknown season {season_id!r}")
            entries = con.execute(
                "SELECT entry_id, agent_id, strategy_name, parameters_json "
                "FROM arena_entries WHERE season_id = ?",
                [season_id],
            ).fetchall()
        finally:
            con.close()

        starting_equity = float(season[2])
        results: list[dict[str, Any]] = []
        as_of = date.today()
        for entry_id, agent_id, strategy_name, parameters_json in entries:
            parameters = json.loads(parameters_json) if parameters_json else {}
            record: dict[str, Any] = {
                "entry_id": entry_id,
                "agent_id": agent_id,
                "strategy_name": strategy_name,
            }
            try:
                if dataset_id is None:
                    raise ValueError("no dataset available for this season yet")
                _ds, candles = self.backtest_service.load_dataset_candles(dataset_id)
                run = self.backtest_service.simulate_only(
                    strategy_name=strategy_name,
                    candles=candles,
                    parameters=parameters,
                    starting_equity=starting_equity,
                )
            except Exception as exc:  # noqa: BLE001 - recorded honestly
                record.update(
                    {
                        "data_status": "data_missing",
                        "reason": str(exc)[:200],
                        "equity": None,
                        "return_pct": None,
                    }
                )
                self._snapshot(entry_id, season_id, as_of, record)
                results.append(record)
                continue
            return_pct = run.get("return_pct")
            equity = (
                starting_equity * (1 + float(return_pct) / 100)
                if return_pct is not None
                else None
            )
            record.update(
                {
                    "data_status": "ok",
                    "equity": round(equity, 2) if equity is not None else None,
                    "return_pct": return_pct,
                    "trades": run.get("total_trades"),
                    "max_drawdown": run.get("max_drawdown"),
                }
            )
            self._snapshot(entry_id, season_id, as_of, record)
            results.append(record)
        return {"season_id": season_id, "as_of": as_of.isoformat(), "entries": results}

    def _snapshot(
        self, entry_id: str, season_id: str, as_of: date, record: dict[str, Any]
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM arena_equity_daily WHERE entry_id = ? AND as_of = ?",
                [entry_id, as_of],
            )
            con.execute(
                "INSERT INTO arena_equity_daily VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    f"snap_{uuid.uuid4().hex[:10]}",
                    season_id,
                    entry_id,
                    as_of,
                    record.get("equity"),
                    record.get("return_pct"),
                    record.get("trades"),
                    record.get("max_drawdown"),
                    record.get("data_status", "ok"),
                    _utc_now(),
                ],
            )
        finally:
            con.close()

    # -- standings ------------------------------------------------------------

    def standings(self, season_id: str) -> dict[str, Any]:
        """Latest snapshot per entry, ranked by return. Missing data shows as
        missing — it never becomes a zero that looks like a real result."""

        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT d.entry_id, e.agent_id, e.strategy_name, d.equity,
                       d.return_pct, d.trades, d.max_drawdown, d.data_status,
                       d.as_of
                FROM arena_equity_daily d
                JOIN arena_entries e ON e.entry_id = d.entry_id
                WHERE d.season_id = ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY d.entry_id ORDER BY d.as_of DESC
                ) = 1
                """,
                [season_id],
            ).fetchall()
        finally:
            con.close()
        entries = [
            {
                "entry_id": r[0],
                "agent_id": r[1],
                "strategy_name": r[2],
                "equity": r[3],
                "return_pct": r[4],
                "trades": r[5],
                "max_drawdown": r[6],
                "data_status": r[7],
                "as_of": _iso(r[8]),
            }
            for r in rows
        ]
        ranked = sorted(
            [e for e in entries if e["return_pct"] is not None],
            key=lambda e: e["return_pct"],
            reverse=True,
        )
        for position, entry in enumerate(ranked, start=1):
            entry["rank"] = position
        return {
            "season_id": season_id,
            "standings": ranked,
            "unavailable": [e for e in entries if e["return_pct"] is None],
        }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
