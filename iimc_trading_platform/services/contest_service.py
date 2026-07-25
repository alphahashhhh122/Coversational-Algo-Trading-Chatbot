"""Contests: a fair, reproducible competition with a frozen dataset.

A contest is three commitments made *before* anyone competes:

1. **A frozen dataset.** The candles are hashed at creation. Every entrant is
   evaluated on the same bars, and the hash is stored with the results so a
   sceptic can verify nothing shifted underneath the scores.
2. **A deadline.** Entries after ``closes_at`` are refused. Without this, a
   late entrant could tune against results already published.
3. **An immutable snapshot.** Closing writes the standings to
   ``contest_results``. Later runs, re-scorings, or scoring-code changes cannot
   rewrite a finished contest — the record says what was true when it closed.

Together these are what make a leaderboard a *result* rather than a leaderboard.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..db import connect


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dataset_fingerprint(candles: list[Any]) -> str:
    """A stable hash of the evaluation bars.

    Hashing the candle values (not the row ids) means the fingerprint changes
    if the data changes, even if the dataset keeps its name.
    """
    hasher = hashlib.sha256()
    for candle in candles:
        if isinstance(candle, dict):
            payload = [
                str(candle.get(key))
                for key in ("timestamp", "open", "high", "low", "close", "volume")
            ]
        else:
            payload = [str(candle)]
        hasher.update("|".join(payload).encode("utf-8"))
    return hasher.hexdigest()[:32]


class ContestService:
    def __init__(self, db_path: Path, backtest_service: Any = None) -> None:
        self.db_path = db_path
        self.backtest_service = backtest_service

    # -- lifecycle ------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        symbol: str,
        exchange: str = "NSE",
        dataset_id: str | None = None,
        open_for_days: int = 7,
    ) -> dict[str, Any]:
        contest_id = f"contest_{uuid.uuid4().hex[:10]}"
        dataset_hash = None
        row_count = 0
        if dataset_id and self.backtest_service is not None:
            try:
                _ds, candles = self.backtest_service.load_dataset_candles(dataset_id)
                dataset_hash = dataset_fingerprint(candles)
                row_count = len(candles)
                self._freeze_dataset(
                    dataset_id, symbol, exchange, dataset_hash, row_count
                )
            except Exception:  # noqa: BLE001 - a contest can open before data lands
                dataset_hash = None
        closes_at = _utc_now() + timedelta(days=open_for_days)
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO contests VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                [
                    contest_id,
                    name,
                    symbol.upper().strip(),
                    exchange.upper().strip(),
                    dataset_id,
                    dataset_hash,
                    closes_at,
                    _utc_now(),
                ],
            )
        finally:
            con.close()
        return {
            "contest_id": contest_id,
            "name": name,
            "symbol": symbol.upper().strip(),
            "eval_dataset_id": dataset_id,
            "dataset_hash": dataset_hash,
            "frozen_rows": row_count,
            "closes_at": closes_at.isoformat(),
            "status": "open",
        }

    def _freeze_dataset(
        self,
        dataset_id: str,
        symbol: str,
        exchange: str,
        content_hash: str,
        row_count: int,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM eval_datasets WHERE eval_dataset_id = ?", [dataset_id]
            )
            con.execute(
                "INSERT INTO eval_datasets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    dataset_id,
                    symbol.upper().strip(),
                    exchange.upper().strip(),
                    "unknown",
                    _utc_now().date(),
                    _utc_now().date(),
                    row_count,
                    content_hash,
                    _utc_now(),
                ],
            )
        finally:
            con.close()

    def get(self, contest_id: str) -> dict[str, Any] | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                "SELECT contest_id, name, symbol, exchange, eval_dataset_id, "
                "dataset_hash, closes_at, status FROM contests "
                "WHERE contest_id = ?",
                [contest_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {
            "contest_id": row[0],
            "name": row[1],
            "symbol": row[2],
            "exchange": row[3],
            "eval_dataset_id": row[4],
            "dataset_hash": row[5],
            "closes_at": _iso(row[6]),
            "status": row[7],
        }

    def list(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT contest_id, name, symbol, status, closes_at, dataset_hash "
                "FROM contests ORDER BY created_at DESC"
            ).fetchall()
        finally:
            con.close()
        return {
            "contests": [
                {
                    "contest_id": r[0],
                    "name": r[1],
                    "symbol": r[2],
                    "status": r[3],
                    "closes_at": _iso(r[4]),
                    "dataset_hash": r[5],
                }
                for r in rows
            ]
        }

    def is_open(self, contest_id: str) -> bool:
        contest = self.get(contest_id)
        if contest is None or contest["status"] != "open":
            return False
        closes_at = contest["closes_at"]
        return bool(closes_at) and _utc_now() < datetime.fromisoformat(closes_at)

    # -- closing --------------------------------------------------------------

    def close(
        self, contest_id: str, leaderboard: dict[str, Any]
    ) -> dict[str, Any]:
        """Freeze the standings. Idempotent: a closed contest is never rewritten."""

        contest = self.get(contest_id)
        if contest is None:
            raise ValueError(f"unknown contest {contest_id!r}")
        if contest["status"] == "closed":
            return {
                "contest_id": contest_id,
                "status": "already_closed",
                "results": self.results(contest_id)["results"],
            }
        snapshot_at = _utc_now()
        ranked = leaderboard.get("ranked", [])
        con = connect(self.db_path)
        try:
            for entry in ranked:
                con.execute(
                    "INSERT INTO contest_results VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        f"cres_{uuid.uuid4().hex[:10]}",
                        contest_id,
                        entry.get("agent_id"),
                        entry.get("version", ""),
                        entry.get("run_id"),
                        json.dumps(entry.get("metrics", {}), default=str),
                        entry.get("composite"),
                        entry.get("rank"),
                        snapshot_at,
                    ],
                )
            con.execute(
                "UPDATE contests SET status = 'closed' WHERE contest_id = ?",
                [contest_id],
            )
        finally:
            con.close()
        return {
            "contest_id": contest_id,
            "status": "closed",
            "entrants": len(ranked),
            "dataset_hash": contest["dataset_hash"],
            "snapshot_at": snapshot_at.isoformat(),
        }

    def results(self, contest_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT agent_id, version, run_id, metrics_json, composite, "
                "rank, snapshot_at FROM contest_results "
                "WHERE contest_id = ? ORDER BY rank",
                [contest_id],
            ).fetchall()
        finally:
            con.close()
        contest = self.get(contest_id)
        return {
            "contest_id": contest_id,
            "status": contest["status"] if contest else "unknown",
            "dataset_hash": contest["dataset_hash"] if contest else None,
            "results": [
                {
                    "agent_id": r[0],
                    "version": r[1],
                    "run_id": r[2],  # evidence link survives the snapshot
                    "metrics": json.loads(r[3]) if r[3] else {},
                    "composite": r[4],
                    "rank": r[5],
                    "snapshot_at": _iso(r[6]),
                }
                for r in rows
            ],
        }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)
