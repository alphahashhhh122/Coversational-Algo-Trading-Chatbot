"""The supervisor: the platform watching itself.

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

It also watches the *data* the agents depend on: a dataset going stale is
reported, and — the single exception to "only flag" — it may enqueue a
refresh job, because fetching data is the one corrective action that cannot
lose money. It still cannot retire an agent, change a configuration, or
trade.

Findings are persisted so you can see what it noticed while you were away.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..db import connect

# A score has to move by more than this fraction before it's worth reporting;
# small wobbles are noise, and a supervisor that cries wolf gets ignored.
_MATERIAL_CHANGE = 0.25
_MIN_HISTORY = 2

# Regime detection: how far recent volatility must diverge from the prior
# stretch before it counts as a different market rather than noise.
_REGIME_VOLATILE_RATIO = 1.5
_REGIME_CALM_RATIO = 0.67
_MIN_REGIME_OBSERVATIONS = 40


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SupervisorService:
    def __init__(
        self,
        db_path: Path,
        run_agent: Callable[[str, str], dict[str, Any]] | None = None,
        freshness: Any | None = None,
        enqueue_refresh: Callable[[str], Any] | None = None,
        load_candles: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        """Hooks are injected so the supervisor stays testable and so its
        one permitted action (``enqueue_refresh``) is explicit at the call
        site rather than hidden inside the service.
        """
        self.db_path = db_path
        self.run_agent = run_agent
        self.freshness = freshness
        self.enqueue_refresh = enqueue_refresh
        self.load_candles = load_candles

    # -- scheduled sweep ------------------------------------------------------

    def sweep(
        self, agents: list[str], symbol: str = "RELIANCE"
    ) -> dict[str, Any]:
        """Check the regime, re-run the agents, then look for drift.

        The order is the point. Regime is checked *first* so the agent runs
        that follow are the re-validation — their scores are produced under
        the new regime, and the drift check that follows compares them against
        scores earned under the old one. An edge that only worked in the
        previous regime therefore shows up as a degradation in the same sweep,
        with the regime finding sitting next to it as the explanation.

        Safe to call on a timer.
        """

        regime_findings = self.check_regime(symbol)
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
        data_findings, refreshed = self.check_data_health()
        return {
            "ran": ran,
            "errors": errors,
            "findings": regime_findings + findings + data_findings,
            "regime_shifts": len(regime_findings),
            "refresh_enqueued": refreshed,
            "swept_at": _utc_now().isoformat(),
        }

    def check_regime(self, symbol: str) -> list[dict[str, Any]]:
        """Load the symbol's history and test it for a regime change."""

        if self.load_candles is None:
            return []
        try:
            candles = self.load_candles(symbol)
        except Exception:  # noqa: BLE001 - no history is not a supervisor failure
            return []
        finding = self.detect_regime_shift(candles or [], dataset_id=symbol)
        if finding is None:
            return []
        # Say plainly whether a re-validation actually followed, so the finding
        # never implies work that did not happen.
        finding["detail"]["revalidated_in_this_sweep"] = self.run_agent is not None
        return [finding]

    # -- data staleness (and the one permitted action) ----------------------

    def check_data_health(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Flag stale datasets, and enqueue a refresh where that is safe.

        Enqueuing a data refresh is the *only* action the supervisor may
        take: fetching market data cannot lose money, so it needs no human
        judgement. Anything with consequences stays a flag.
        """

        if self.freshness is None:
            return [], []
        con = connect(self.db_path)
        try:
            dataset_ids = [
                row[0]
                for row in con.execute(
                    "SELECT dataset_id FROM data_catalog ORDER BY dataset_id"
                ).fetchall()
            ]
        finally:
            con.close()

        findings: list[dict[str, Any]] = []
        refreshed: list[str] = []
        for dataset_id in dataset_ids:
            try:
                assessment = self.freshness.assess(dataset_id, "current_market")
            except Exception as exc:  # noqa: BLE001 - never fatal
                findings.append(
                    self._record(
                        kind="data_unassessable",
                        severity="warning",
                        agent_id=dataset_id,
                        summary=f"Could not assess {dataset_id}: {str(exc)[:80]}",
                        detail={"dataset_id": dataset_id},
                    )
                )
                continue
            if assessment.get("status") != "stale":
                continue
            enqueued = False
            if self.enqueue_refresh is not None:
                try:
                    self.enqueue_refresh(dataset_id)
                    enqueued = True
                    refreshed.append(dataset_id)
                except Exception:  # noqa: BLE001 - flag it anyway
                    enqueued = False
            findings.append(
                self._record(
                    kind="data_stale",
                    severity="warning",
                    agent_id=dataset_id,
                    summary=(
                        f"{dataset_id} is stale"
                        + (
                            " - a refresh has been queued."
                            if enqueued
                            else " - no refresh path is configured."
                        )
                    ),
                    detail={
                        "dataset_id": dataset_id,
                        "refresh_enqueued": enqueued,
                        "assessment": {
                            k: v
                            for k, v in assessment.items()
                            if k in {"status", "age_minutes", "threshold_minutes"}
                        },
                    },
                )
            )
        return findings, refreshed

    # -- regime awareness ---------------------------------------------------

    def detect_regime_shift(
        self, candles: list[dict[str, Any]], *, dataset_id: str = "dataset"
    ) -> dict[str, Any] | None:
        """Compare recent volatility against the preceding stretch.

        An edge found in a calm market often evaporates in a volatile one. When
        the regime changes materially, the strategies ranked under the old
        regime deserve re-validation — so this raises a finding rather than
        quietly leaving a stale leaderboard looking authoritative.
        """

        closes = []
        for candle in candles:
            try:
                closes.append(float(candle["close"]))
            except (KeyError, TypeError, ValueError):
                continue
        returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
            if closes[i - 1]
        ]
        if len(returns) < _MIN_REGIME_OBSERVATIONS:
            return None
        split = len(returns) // 2
        earlier, recent = returns[:split], returns[split:]
        old_vol, new_vol = _stdev(earlier), _stdev(recent)
        if not old_vol:
            return None
        ratio = new_vol / old_vol
        if _REGIME_CALM_RATIO <= ratio <= _REGIME_VOLATILE_RATIO:
            return None
        direction = "more volatile" if ratio > 1 else "calmer"
        return self._record(
            kind="regime_shift",
            severity="warning",
            agent_id=dataset_id,
            summary=(
                f"{dataset_id} has turned {direction} "
                f"({ratio:.2f}x prior volatility) - strategies ranked under "
                "the previous regime are worth re-validating."
            ),
            detail={
                "dataset_id": dataset_id,
                "previous_volatility": round(old_vol, 8),
                "recent_volatility": round(new_vol, 8),
                "ratio": round(ratio, 4),
                "observations": len(returns),
            },
        )

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


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)
