"""The supervisor: the platform watching its own agents.

This is where autonomy actually lives. On a schedule, the supervisor:

1. **Re-runs registered agents** so the leaderboard reflects current data
   rather than whenever someone last clicked a button, and
2. **Compares each agent's new score against its own history**, raising a
   finding when something has materially changed.

What it deliberately does *not* do is act. It flags — retires nothing, trades
nothing, changes no configuration. An autonomous system that can also act on
its own conclusions needs a much stronger correctness guarantee than "the
metric moved"; a system that surfaces "this agent's out-of-sample edge has
halved since last week" is useful *and* safe, because a human still decides.

Findings are persisted so you can see what it noticed while you were away.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..db import connect

# A score has to move by more than this fraction before it's worth reporting;
# small wobbles are noise, and a supervisor that cries wolf gets ignored.
_MATERIAL_CHANGE = 0.25
_MIN_HISTORY = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SupervisorService:
    def __init__(
        self,
        db_path: Path,
        run_agent: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        """``run_agent(agent_name, symbol) -> run payload`` (optional: the
        supervisor can analyse existing history without running anything)."""
        self.db_path = db_path
        self.run_agent = run_agent

    # -- scheduled sweep ------------------------------------------------------

    def sweep(
        self, agents: list[str], symbol: str = "RELIANCE"
    ) -> dict[str, Any]:
        """Run the named agents, then look for drift. Safe to call on a timer."""

        ran: list[dict[str, Any]] = []
        errors: list[str] = []
        if self.run_agent is not None:
            for name in agents:
                try:
                    payload = self.run_agent(name, symbol)
                    ran.append(
                        {
                            "agent": name,
                            "status": payload.get("status"),
                            "run_id": payload.get("run_id"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - recorded, never fatal
                    errors.append(f"{name}: {str(exc)[:160]}")
        findings = self.detect_drift()
        return {
            "ran": ran,
            "errors": errors,
            "findings": findings,
            "swept_at": _utc_now().isoformat(),
        }

    # -- drift detection ------------------------------------------------------

    def detect_drift(self) -> list[dict[str, Any]]:
        """Compare each agent's latest score with the one before it."""

        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT s.agent_id, a.name, a.category, s.composite,
                       s.metrics_json, s.run_id, s.scored_at
                FROM agent_scores s
                JOIN agents a ON a.agent_id = s.agent_id
                ORDER BY s.agent_id, s.scored_at DESC
                """
            ).fetchall()
        finally:
            con.close()

        by_agent: dict[str, list[Any]] = {}
        for row in rows:
            by_agent.setdefault(row[0], []).append(row)

        findings: list[dict[str, Any]] = []
        for agent_id, history in by_agent.items():
            if len(history) < _MIN_HISTORY:
                continue
            latest, previous = history[0], history[1]
            new_score, old_score = latest[3], previous[3]
            name, category = latest[1], latest[2]

            # An agent that could be scored before but can't now is worth
            # knowing about — it usually means its data went away.
            if new_score is None and old_score is not None:
                findings.append(
                    self._record(
                        kind="became_inconclusive",
                        severity="warning",
                        agent_id=agent_id,
                        summary=(
                            f"{name} can no longer be scored — it was "
                            f"{round(old_score, 4)} before."
                        ),
                        detail={"previous": old_score, "run_id": latest[5]},
                    )
                )
                continue
            if new_score is None or old_score is None:
                continue

            delta = new_score - old_score
            scale = max(abs(old_score), 1e-9)
            if abs(delta) / scale < _MATERIAL_CHANGE:
                continue
            improved = delta > 0
            findings.append(
                self._record(
                    kind="score_improved" if improved else "score_degraded",
                    severity="info" if improved else "warning",
                    agent_id=agent_id,
                    summary=(
                        f"{name} ({category}) "
                        f"{'improved' if improved else 'degraded'}: "
                        f"{round(old_score, 4)} → {round(new_score, 4)}"
                    ),
                    detail={
                        "previous": old_score,
                        "latest": new_score,
                        "delta": round(delta, 6),
                        "run_id": latest[5],
                        "metrics": json.loads(latest[4]) if latest[4] else {},
                    },
                )
            )
        return findings

    def _record(
        self,
        *,
        kind: str,
        severity: str,
        agent_id: str,
        summary: str,
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        finding_id = f"find_{uuid.uuid4().hex[:10]}"
        detected_at = _utc_now()
        con = connect(self.db_path)
        try:
            # One open finding of a kind per agent — a supervisor that logs the
            # same drift every hour is noise, not signal.
            con.execute(
                "DELETE FROM supervisor_findings "
                "WHERE agent_id = ? AND kind = ? AND acknowledged = FALSE",
                [agent_id, kind],
            )
            con.execute(
                "INSERT INTO supervisor_findings VALUES "
                "(?, ?, ?, ?, ?, ?, FALSE, ?)",
                [
                    finding_id,
                    kind,
                    severity,
                    agent_id,
                    summary,
                    json.dumps(detail, default=str),
                    detected_at,
                ],
            )
        finally:
            con.close()
        return {
            "finding_id": finding_id,
            "kind": kind,
            "severity": severity,
            "agent_id": agent_id,
            "summary": summary,
            "detail": detail,
            "detected_at": detected_at.isoformat(),
        }

    # -- reading --------------------------------------------------------------

    def list_findings(
        self, *, include_acknowledged: bool = False, limit: int = 50
    ) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT finding_id, kind, severity, agent_id, summary, "
                "detail_json, acknowledged, detected_at "
                "FROM supervisor_findings ORDER BY detected_at DESC LIMIT ?",
                [limit],
            ).fetchall()
        finally:
            con.close()
        findings = [
            {
                "finding_id": r[0],
                "kind": r[1],
                "severity": r[2],
                "agent_id": r[3],
                "summary": r[4],
                "detail": json.loads(r[5]) if r[5] else {},
                "acknowledged": bool(r[6]),
                "detected_at": _iso(r[7]),
            }
            for r in rows
            if include_acknowledged or not r[6]
        ]
        return {"findings": findings}

    def acknowledge(self, finding_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            con.execute(
                "UPDATE supervisor_findings SET acknowledged = TRUE "
                "WHERE finding_id = ?",
                [finding_id],
            )
        finally:
            con.close()
        return {"finding_id": finding_id, "acknowledged": True}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)
