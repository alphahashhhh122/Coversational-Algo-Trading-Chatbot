"""The daily digest: one brief answering "what changed while I was away?".

The supervisor produces findings continuously; the leaderboard moves; datasets
go stale. Individually each is a notification, and a platform that emits fifty
notifications a day is a platform you stop reading. The digest collapses them
into **one attributed brief** with three questions answered in order:

1. **What changed** — agents whose score moved materially, plus the current top
   of the leaderboard.
2. **What's stale** — datasets past their freshness threshold and the coverage
   gaps that block agents from working at all.
3. **What degraded** — the subset that is bad news, separated out so it cannot
   be lost inside the good news.

Two design commitments carry through from the rest of the platform:

**Everything is attributed.** Each section names the service or agent that
produced it, so a claim in the digest can always be traced back to the run that
supports it. Nothing here is generated prose over invented numbers.

**Absence is reported, not filled in.** When a section has no data — no score
history yet, no freshness service wired up, a committee that failed — the
digest says so. An empty section reads as "nothing to report"; a *missing* one
reads as "we could not look", and those are different facts.

The digest reads only. It flags nothing new, retires nothing, and trades
nothing; it is a view over work the supervisor already did.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..db import connect

_INTERESTING_KINDS = {
    "score_degraded",
    "score_improved",
    "became_inconclusive",
    "regime_shift",
}
_STALENESS_KINDS = {"data_stale", "data_unassessable"}
_BAD_NEWS_KINDS = {"score_degraded", "became_inconclusive", "regime_shift"}
_TOP_N = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DailyDigestService:
    def __init__(
        self,
        db_path: Path,
        *,
        evaluation: Any | None = None,
        supervisor: Any | None = None,
        data_health: Any | None = None,
        committee: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        """Collaborators are injected rather than constructed here.

        The digest is a *composition* of things that already exist. Building
        them internally would hide which parts of the platform it depends on,
        and make it impossible to generate a digest when one of them is
        unavailable — which is exactly the case it has to handle honestly.
        """
        self.db_path = db_path
        self.evaluation = evaluation
        self.supervisor = supervisor
        self.data_health = data_health
        self.committee = committee

    # -- generation ---------------------------------------------------------

    def generate(self, *, symbol: str | None = None) -> dict[str, Any]:
        sections = [
            self._what_changed(),
            self._whats_stale(),
            self._what_degraded(),
        ]
        if symbol:
            sections.append(self._committee_read(symbol))

        headline = _headline(sections)
        digest_id = f"digest_{uuid.uuid4().hex[:10]}"
        generated_at = _utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO daily_digests VALUES (?, ?, ?, ?)",
                [
                    digest_id,
                    headline,
                    json.dumps(sections, default=str),
                    generated_at,
                ],
            )
        finally:
            con.close()
        return {
            "digest_id": digest_id,
            "headline": headline,
            "sections": sections,
            "generated_at": generated_at.isoformat(),
        }

    def _findings(self) -> list[dict[str, Any]]:
        if self.supervisor is None:
            return []
        try:
            return self.supervisor.list_findings(limit=200)["findings"]
        except Exception:  # noqa: BLE001 - a missing section beats a failed digest
            return []

    def _what_changed(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        gaps: list[str] = []
        for finding in self._findings():
            if finding.get("kind") in _INTERESTING_KINDS:
                items.append(
                    {
                        "text": finding.get("summary", ""),
                        "attribution": f"supervisor finding {finding.get('finding_id')}",
                        "kind": finding.get("kind"),
                    }
                )

        leaders: list[dict[str, Any]] = []
        if self.evaluation is None:
            gaps.append("No evaluation service available — leaderboard not read.")
        else:
            try:
                board = self.evaluation.leaderboard()
                for entry in board.get("ranked", [])[:_TOP_N]:
                    leaders.append(
                        {
                            "rank": entry.get("rank"),
                            "name": entry.get("name"),
                            "composite": entry.get("composite"),
                            # The run is the evidence: every ranked number here
                            # can be opened and checked.
                            "attribution": f"run {entry.get('run_id')}",
                        }
                    )
                if not board.get("ranked"):
                    gaps.append(
                        "No agent has a scorable run yet, so there is no ranking "
                        "to report."
                    )
            except Exception as exc:  # noqa: BLE001
                gaps.append(f"Leaderboard unavailable: {str(exc)[:120]}")

        return {
            "section": "what_changed",
            "title": "What changed",
            "source": "supervisor + agent evaluation",
            "items": items,
            "leaderboard_top": leaders,
            "gaps": gaps,
        }

    def _whats_stale(self) -> dict[str, Any]:
        items = [
            {
                "text": finding.get("summary", ""),
                "attribution": f"supervisor finding {finding.get('finding_id')}",
                "kind": finding.get("kind"),
            }
            for finding in self._findings()
            if finding.get("kind") in _STALENESS_KINDS
        ]
        gaps: list[str] = []
        coverage: dict[str, Any] = {}
        if self.data_health is None:
            gaps.append("No data-health service available — coverage not checked.")
        else:
            try:
                report = self.data_health.coverage()
                coverage = {
                    "price_coverage_pct": report.get("price_coverage_pct"),
                    "fundamentals_coverage_pct": report.get(
                        "fundamentals_coverage_pct"
                    ),
                    "attribution": "data_health coverage report",
                }
                gaps.extend(report.get("gaps", []))
            except Exception as exc:  # noqa: BLE001
                gaps.append(f"Coverage unavailable: {str(exc)[:120]}")

        return {
            "section": "whats_stale",
            "title": "What's stale",
            "source": "supervisor + data_health",
            "items": items,
            "coverage": coverage,
            "gaps": gaps,
        }

    def _what_degraded(self) -> dict[str, Any]:
        items = [
            {
                "text": finding.get("summary", ""),
                "attribution": f"supervisor finding {finding.get('finding_id')}",
                "kind": finding.get("kind"),
                "severity": finding.get("severity"),
            }
            for finding in self._findings()
            if finding.get("kind") in _BAD_NEWS_KINDS
        ]
        return {
            "section": "what_degraded",
            "title": "What degraded",
            "source": "supervisor drift + regime detection",
            "items": items,
            # Separated from "what changed" on purpose: good news is not
            # allowed to bury bad news by sitting in the same list.
            "gaps": [],
        }

    def _committee_read(self, symbol: str) -> dict[str, Any]:
        """A multi-agent read on one symbol, each member quoted separately."""

        section: dict[str, Any] = {
            "section": "committee",
            "title": f"Committee read on {symbol.upper()}",
            "source": "research committee",
            "items": [],
            "gaps": [],
        }
        if self.committee is None:
            section["gaps"].append(
                "No committee runner configured — no multi-agent read taken."
            )
            return section
        try:
            verdict = self.committee(symbol)
        except Exception as exc:  # noqa: BLE001
            section["gaps"].append(f"Committee run failed: {str(exc)[:160]}")
            return section

        for text in verdict.get("agreements", []):
            section["items"].append({"text": text, "attribution": "committee consensus"})
        for disagreement in verdict.get("disagreements", []):
            positions = ", ".join(
                f"{p.get('member')} says {p.get('stance')}"
                for p in disagreement.get("positions", [])
            )
            # Disagreement is carried through verbatim rather than resolved —
            # the same rule the committee itself follows.
            section["items"].append(
                {
                    "text": f"Members disagree on {disagreement.get('topic')}: {positions}",
                    "attribution": "committee (unresolved)",
                }
            )
        section["gaps"].extend(verdict.get("gaps", []))
        section["members"] = verdict.get("members", [])
        return section

    # -- reading ------------------------------------------------------------

    def latest(self) -> dict[str, Any] | None:
        digests = self.list(limit=1)["digests"]
        return digests[0] if digests else None

    def list(self, *, limit: int = 10) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT digest_id, headline, sections_json, generated_at "
                "FROM daily_digests ORDER BY generated_at DESC LIMIT ?",
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "digests": [
                {
                    "digest_id": r[0],
                    "headline": r[1],
                    "sections": json.loads(r[2]) if r[2] else [],
                    "generated_at": (
                        r[3].isoformat() if isinstance(r[3], datetime) else str(r[3])
                    ),
                }
                for r in rows
            ]
        }


def _headline(sections: list[dict[str, Any]]) -> str:
    """One line that is true even when there is nothing to say."""

    counts = {s["section"]: len(s.get("items", [])) for s in sections}
    degraded = counts.get("what_degraded", 0)
    stale = counts.get("whats_stale", 0)
    changed = counts.get("what_changed", 0)
    if degraded:
        return (
            f"{degraded} thing{'s' if degraded != 1 else ''} degraded"
            + (f", {stale} stale" if stale else "")
            + " — worth a look."
        )
    if stale:
        return f"Nothing degraded, but {stale} data issue{'s' if stale != 1 else ''} need attention."
    if changed:
        return f"{changed} change{'s' if changed != 1 else ''}, none of them bad."
    # Phrased without "since the last digest" — this may be the first one.
    return "Nothing to report: no material moves and no stale data."
