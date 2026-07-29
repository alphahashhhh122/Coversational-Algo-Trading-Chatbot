"""Scoring for ATL agents (docs/ATL_TRANSITION.md §4.3).

Scores are computed *from recorded runs*, never from a fresh unverifiable
claim, so every leaderboard cell traces back to an ``agent_runs`` row and the
evidence stored with it.

Ranking rules, by category:

- **strategy** — out-of-sample only. In-sample returns are deliberately
  ignored: a config that won on train and lost on test is *penalised*, not
  celebrated. Too few OOS trades is ``inconclusive`` (unranked), never a
  flattering zero.
- **research** — coverage of the expected sections plus resolvable citations
  and honest gap reporting. Eloquence is not measured.
- **monitor** — precision: conditions that fired and re-verify against the
  stored snapshot, minus false fires.

A composite is only produced when the evidence supports one; otherwise the
scorecard says ``inconclusive`` and explains why.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect

# Research agents are expected to cover these four questions.
_EXPECTED_SECTIONS = ("valuation", "fundamentals", "technicals", "news")

# A walk-forward verdict maps to a multiplier on the OOS return: overfit
# configurations are actively penalised.
_VERDICT_WEIGHT = {
    "holds_up": 1.0,
    "weaker_but_positive": 0.8,
    "poor": 0.5,
    "overfit": 0.25,
    "inconclusive": 0.0,
}

_MIN_OOS_TRADES = 3

# Bumped whenever the scoring rule changes, and stored with every score, so a
# leaderboard never silently mixes numbers produced by different rules.
#
#   1 — raw out-of-sample return, weighted by verdict.
#   2 — excess over buy-and-hold, with additive drawdown/Sharpe penalties.
#   3 — Sharpe measured over every day in the window rather than only the days
#       a strategy traded. The old basis overstated infrequent traders by an
#       order of magnitude, so a v2 composite is not comparable to a v3 one.
SCORING_VERSION = 3

# Risk penalties, applied additively (see _score_strategy).
_DRAWDOWN_WEIGHT = 0.5
_NEGATIVE_SHARPE_WEIGHT = 0.1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentEvaluationService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # -- scoring --------------------------------------------------------------

    def score_run(self, run: dict[str, Any], category: str) -> dict[str, Any]:
        """Turn one recorded run into a scorecard. Pure function of the run."""

        if run.get("status") == "failed":
            return {
                "status": "inconclusive",
                "reason": "the run failed",
                "metrics": {},
                "composite": None,
            }
        findings = run.get("findings") or {}
        if category == "strategy":
            return _score_strategy(findings)
        if category == "research":
            return _score_research(findings, run.get("evidence") or [])
        if category == "monitor":
            return _score_monitor(findings)
        return {
            "status": "unscored",
            "reason": f"no scorecard for category {category!r}",
            "metrics": {},
            "composite": None,
        }

    def record_score(
        self,
        *,
        agent_id: str,
        version: str,
        run_id: str,
        scorecard: dict[str, Any],
        eval_dataset_id: str | None = None,
    ) -> str:
        score_id = f"ascore_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO agent_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    score_id,
                    agent_id,
                    version,
                    run_id,
                    eval_dataset_id,
                    json.dumps(scorecard, default=str),
                    scorecard.get("composite"),
                    _utc_now(),
                ],
            )
        finally:
            con.close()
        return score_id

    def rescore_history(self) -> dict[str, Any]:
        """Re-score every recorded run under the current rule.

        Scores accumulate over months while the scoring rule changes underneath
        them, and a leaderboard that mixes rules is quietly meaningless — a
        composite from one rule ranked directly against a composite from
        another. This recomputes every stored score from the evidence already
        in ``agent_runs`` (``findings_json``, ``evidence_json``, ``status``),
        which is exactly what :meth:`score_run` consumes, so nothing is
        invented and the original runs are untouched.

        What it deliberately cannot do is make an old run comparable. A run
        that never captured a benchmark or a Sharpe has no such number to
        recover; re-scoring returns it as ``inconclusive`` naming what is
        missing. Only re-running the agent produces a rankable score, and
        saying so is more useful than a confident figure derived from half the
        evidence.
        """

        con = connect(self.db_path)
        try:
            runs = con.execute(
                """
                SELECT r.run_id, r.agent_id, r.version, r.status,
                       r.findings_json, r.evidence_json, a.category
                FROM agent_runs r
                JOIN agents a ON a.agent_id = r.agent_id
                ORDER BY r.started_at
                """
            ).fetchall()
        finally:
            con.close()

        rescored: list[dict[str, Any]] = []
        for run_id, agent_id, version, status, findings, evidence, category in runs:
            card = self.score_run(
                {
                    "status": status,
                    "findings": json.loads(findings) if findings else {},
                    "evidence": json.loads(evidence) if evidence else [],
                },
                category,
            )
            con = connect(self.db_path)
            try:
                # One score per run under the current rule: replace rather than
                # append, so the leaderboard's "latest score" cannot pick up a
                # stale rule's row.
                con.execute(
                    "DELETE FROM agent_scores WHERE run_id = ?", [run_id]
                )
            finally:
                con.close()
            self.record_score(
                agent_id=agent_id,
                version=version,
                run_id=run_id,
                scorecard=card,
            )
            rescored.append(
                {
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "status": card["status"],
                    "composite": card.get("composite"),
                    "reason": card.get("reason"),
                }
            )
        ranked = [r for r in rescored if r["composite"] is not None]
        return {
            "scoring_version": SCORING_VERSION,
            "runs_rescored": len(rescored),
            "now_ranked": len(ranked),
            "now_inconclusive": len(rescored) - len(ranked),
            "results": rescored,
        }

    # -- leaderboard ----------------------------------------------------------

    def leaderboard(self, *, category: str | None = None) -> dict[str, Any]:
        """Latest score per agent, ranked. Unscorable agents are listed
        separately as ``inconclusive`` rather than ranked at zero."""

        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT s.agent_id, a.name, a.category, s.version, s.run_id,
                       s.metrics_json, s.composite, s.scored_at,
                       s.eval_dataset_id
                FROM agent_scores s
                JOIN agents a ON a.agent_id = s.agent_id
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY s.agent_id ORDER BY s.scored_at DESC
                ) = 1
                """
            ).fetchall()
        finally:
            con.close()
        entries = []
        for r in rows:
            if category and r[2] != category:
                continue
            card = json.loads(r[5])
            entries.append(
                {
                    "agent_id": r[0],
                    "name": r[1],
                    "category": r[2],
                    "version": r[3],
                    "run_id": r[4],  # evidence link
                    "eval_dataset_id": r[8],
                    "status": card.get("status"),
                    "reason": card.get("reason"),
                    "metrics": card.get("metrics", {}),
                    "composite": r[6],
                    # Which rule produced this number. A row scored under an
                    # older rule is not comparable with the rest, and the
                    # reader has no way to know that unless we say it.
                    "scoring_version": card.get("metrics", {}).get(
                        "scoring_version"
                    ),
                    "current_scoring_version": SCORING_VERSION,
                    "scored_at": r[7].isoformat() if isinstance(r[7], datetime) else str(r[7]),
                }
            )
        ranked = sorted(
            [e for e in entries if e["composite"] is not None],
            key=lambda e: e["composite"],
            reverse=True,
        )
        for position, entry in enumerate(ranked, start=1):
            entry["rank"] = position
        unranked = [e for e in entries if e["composite"] is None]
        return {"ranked": ranked, "unranked": unranked}


def _score_strategy(findings: dict[str, Any]) -> dict[str, Any]:
    """Out-of-sample, benchmark-relative, and risk-adjusted.

    Three deliberate choices:

    1. **In-sample numbers never reach a ranking.** Only the untouched test
       window counts.
    2. **The benchmark is the bar.** A strategy is scored on *excess* return
       over simply holding the instrument, because returning +2% while the
       instrument returned +10% destroyed value. When no benchmark is
       available we fall back to raw return and say so in ``reason``.
    3. **Risk is subtracted, not ignored.** Drawdown and negative Sharpe are
       applied as additive penalties so the ranking stays monotonic — more
       drawdown always ranks lower, and a penalty can never flip a loss into
       a better score the way a multiplier would.
    """

    oos = findings.get("out_of_sample_return_pct")
    trades = int(findings.get("out_of_sample_trades") or 0)
    verdict = findings.get("verdict", "inconclusive")
    if oos is None:
        return {
            "status": "inconclusive",
            "reason": "no out-of-sample result (walk-forward required)",
            "metrics": {"walk_forward": False},
            "composite": None,
        }
    excess = findings.get("out_of_sample_excess_return_pct")
    sharpe = findings.get("out_of_sample_sharpe")
    drawdown_pct = findings.get("out_of_sample_drawdown_pct")
    metrics = {
        "out_of_sample_return_pct": oos,
        "out_of_sample_trades": trades,
        "out_of_sample_drawdown": findings.get("out_of_sample_drawdown"),
        "out_of_sample_drawdown_pct": drawdown_pct,
        "out_of_sample_excess_return_pct": excess,
        "out_of_sample_benchmark_pct": findings.get("out_of_sample_benchmark_pct"),
        "out_of_sample_sharpe": sharpe,
        "out_of_sample_win_rate_pct": findings.get("out_of_sample_win_rate_pct"),
        "verdict": verdict,
        "walk_forward": True,
        "windows": findings.get("windows"),
        "windows_held_up": findings.get("windows_held_up"),
        "scoring_version": SCORING_VERSION,
    }
    if trades < _MIN_OOS_TRADES:
        return {
            "status": "inconclusive",
            "reason": (
                f"only {trades} out-of-sample trade(s); "
                f"{_MIN_OOS_TRADES} needed to judge"
            ),
            "metrics": metrics,
            "composite": None,
        }

    # Without a benchmark there is no rankable score. Falling back to raw
    # return used to look like graceful degradation, but it puts two different
    # quantities in the same column: +5% raw against +5% *excess* over holding
    # are not the same claim, and the leaderboard sorts them as though they
    # were. Runs recorded before benchmark-relative scoring existed land here
    # too, which is the honest answer for them — re-running is what makes them
    # comparable, and saying so beats a confident number built on half the
    # evidence.
    if excess is None:
        return {
            "status": "inconclusive",
            "reason": (
                "no benchmark to measure against, so this cannot be ranked "
                "beside benchmark-relative scores — re-run this agent to rank it"
            ),
            "metrics": metrics,
            "composite": None,
        }

    composite = float(excess) * _VERDICT_WEIGHT.get(verdict, 0.5)
    base_source = "excess return over buy-and-hold"

    # Additive penalties keep the ordering monotonic.
    penalty = 0.0
    if drawdown_pct:
        penalty += abs(float(drawdown_pct)) * _DRAWDOWN_WEIGHT
    if sharpe is not None and float(sharpe) < 0:
        penalty += abs(float(sharpe)) * _NEGATIVE_SHARPE_WEIGHT

    # Consistency across walk-forward windows, when measured.
    windows = findings.get("windows")
    held = findings.get("windows_held_up")
    consistency_note = ""
    if windows and held is not None and windows > 1:
        composite *= held / windows
        consistency_note = f", scaled by {held}/{windows} windows holding up"

    return {
        "status": "scored",
        "reason": (
            f"{base_source}, weighted by verdict '{verdict}', "
            f"less risk penalties{consistency_note}"
        ),
        "metrics": metrics,
        "composite": round(composite - penalty, 6),
    }


def _score_research(
    findings: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    """Coverage + real citations. Missing sections cost score, not honesty."""

    # The loop agent nests its sections under "findings".
    sections = findings.get("sections_available")
    if sections is None:
        sections = (findings.get("findings") or {}).get("sections_available", [])
    covered = [s for s in _EXPECTED_SECTIONS if s in (sections or [])]
    citations = findings.get("citations") or [
        e for e in evidence if e.get("kind") in {"citation", "metric"} or e.get("url")
    ]
    resolvable = [c for c in citations if c.get("url") or c.get("ref") or c.get("source")]
    coverage = len(covered) / len(_EXPECTED_SECTIONS)
    metrics = {
        "coverage": round(coverage, 3),
        "sections_covered": covered,
        "citations": len(resolvable),
        "gaps_reported": len(findings.get("gaps") or []),
        "scoring_version": SCORING_VERSION,
    }
    if not covered and not resolvable:
        return {
            "status": "inconclusive",
            "reason": "no sections covered and no citations gathered",
            "metrics": metrics,
            "composite": None,
        }
    # Coverage dominates; citations add a bounded bonus so a well-sourced
    # briefing edges out a bare one without letting link-spam win.
    composite = round(coverage * 100 + min(len(resolvable), 10) * 2, 3)
    return {
        "status": "scored",
        "reason": "coverage of the four core questions, plus real citations",
        "metrics": metrics,
        "composite": composite,
    }


def _score_monitor(findings: dict[str, Any]) -> dict[str, Any]:
    """Precision of fired conditions; unavailable data is not a false fire."""

    checked = int(findings.get("checked") or 0)
    fired = findings.get("fired") or []
    errors = findings.get("errors") or []
    metrics = {
        "checked": checked,
        "fired": len(fired),
        "unavailable": len(errors),
        "scoring_version": SCORING_VERSION,
    }
    if checked == 0:
        return {
            "status": "inconclusive",
            "reason": "no active watches to evaluate",
            "metrics": metrics,
            "composite": None,
        }
    verified = [f for f in fired if f.get("last_value") is not None]
    precision = (len(verified) / len(fired)) if fired else 1.0
    coverage = (checked - len(errors)) / checked
    metrics["precision"] = round(precision, 3)
    metrics["data_coverage"] = round(coverage, 3)
    return {
        "status": "scored",
        "reason": "verified fires over total fires, weighted by data coverage",
        "metrics": metrics,
        "composite": round(precision * coverage * 100, 3),
    }
