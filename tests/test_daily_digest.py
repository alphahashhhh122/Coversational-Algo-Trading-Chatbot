from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.daily_digest_service import DailyDigestService


class _Supervisor:
    def __init__(self, findings: list[dict] | None = None, boom: bool = False):
        self._findings = findings or []
        self._boom = boom

    def list_findings(self, *, limit: int = 50, include_acknowledged: bool = False):
        if self._boom:
            raise RuntimeError("findings table unreadable")
        return {"findings": self._findings}


class _Evaluation:
    def __init__(self, ranked: list[dict] | None = None, boom: bool = False):
        self._ranked = ranked or []
        self._boom = boom

    def leaderboard(self, **_):
        if self._boom:
            raise RuntimeError("leaderboard unavailable")
        return {"ranked": self._ranked, "unranked": []}


class _DataHealth:
    def __init__(self, report: dict | None = None):
        self._report = report or {
            "price_coverage_pct": 3.8,
            "fundamentals_coverage_pct": 0.0,
            "gaps": ["50 of 52 symbols have no price history."],
        }

    def coverage(self, *_args, **_kwargs):
        return self._report


def _finding(kind: str, summary: str, severity: str = "warning") -> dict:
    return {
        "finding_id": f"find_{kind}",
        "kind": kind,
        "severity": severity,
        "summary": summary,
        "detail": {},
    }


class DigestTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def _sections(self, digest: dict) -> dict[str, dict]:
        return {s["section"]: s for s in digest["sections"]}

    def test_findings_are_sorted_into_the_three_questions(self) -> None:
        svc = DailyDigestService(
            self.path,
            supervisor=_Supervisor(
                [
                    _finding("score_degraded", "alpha degraded: 10 → 4"),
                    _finding("score_improved", "beta improved", "info"),
                    _finding("data_stale", "ds_1 is stale"),
                ]
            ),
            evaluation=_Evaluation([]),
            data_health=_DataHealth(),
        )
        sections = self._sections(svc.generate())
        changed = [i["kind"] for i in sections["what_changed"]["items"]]
        self.assertCountEqual(changed, ["score_degraded", "score_improved"])
        self.assertEqual(
            [i["kind"] for i in sections["whats_stale"]["items"]], ["data_stale"]
        )
        # Bad news is pulled out separately so good news cannot bury it.
        self.assertEqual(
            [i["kind"] for i in sections["what_degraded"]["items"]], ["score_degraded"]
        )

    def test_every_item_carries_attribution(self) -> None:
        svc = DailyDigestService(
            self.path,
            supervisor=_Supervisor([_finding("score_degraded", "alpha degraded")]),
            evaluation=_Evaluation(
                [{"rank": 1, "name": "alpha", "composite": 4.0, "run_id": "arun_1"}]
            ),
            data_health=_DataHealth(),
        )
        sections = self._sections(svc.generate())
        for item in sections["what_changed"]["items"]:
            self.assertTrue(item["attribution"])
        leaders = sections["what_changed"]["leaderboard_top"]
        self.assertEqual(leaders[0]["attribution"], "run arun_1")

    def test_headline_leads_with_bad_news(self) -> None:
        svc = DailyDigestService(
            self.path,
            supervisor=_Supervisor(
                [
                    _finding("score_degraded", "alpha degraded"),
                    _finding("data_stale", "ds_1 is stale"),
                ]
            ),
        )
        self.assertIn("degraded", svc.generate()["headline"])

    def test_quiet_day_says_so_rather_than_inventing_news(self) -> None:
        svc = DailyDigestService(
            self.path, supervisor=_Supervisor([]), evaluation=_Evaluation([])
        )
        digest = svc.generate()
        self.assertEqual(
            digest["headline"],
            "Nothing to report: no material moves and no stale data.",
        )
        self.assertEqual(self._sections(digest)["what_degraded"]["items"], [])

    def test_missing_collaborators_are_reported_as_gaps(self) -> None:
        """Absence is a fact. A section we could not fill must say why."""
        sections = self._sections(DailyDigestService(self.path).generate())
        self.assertTrue(
            any("evaluation" in g for g in sections["what_changed"]["gaps"])
        )
        self.assertTrue(
            any("data-health" in g for g in sections["whats_stale"]["gaps"])
        )

    def test_a_failing_collaborator_does_not_fail_the_digest(self) -> None:
        svc = DailyDigestService(
            self.path,
            supervisor=_Supervisor(boom=True),
            evaluation=_Evaluation(boom=True),
        )
        digest = svc.generate()
        self.assertTrue(digest["digest_id"])
        self.assertTrue(
            any("unavailable" in g for g in self._sections(digest)["what_changed"]["gaps"])
        )

    def test_coverage_gaps_are_carried_into_the_brief(self) -> None:
        svc = DailyDigestService(
            self.path, supervisor=_Supervisor([]), data_health=_DataHealth()
        )
        stale = self._sections(svc.generate())["whats_stale"]
        self.assertEqual(stale["coverage"]["price_coverage_pct"], 3.8)
        self.assertTrue(any("no price history" in g for g in stale["gaps"]))

    def test_committee_disagreement_is_preserved_not_resolved(self) -> None:
        def committee(symbol: str) -> dict:
            return {
                "members": ["market_researcher", "strategy_validator"],
                "agreements": [],
                "disagreements": [
                    {
                        "topic": "direction",
                        "positions": [
                            {"member": "market_researcher", "stance": "constructive"},
                            {"member": "strategy_validator", "stance": "cautious"},
                        ],
                    }
                ],
                "gaps": [],
            }

        svc = DailyDigestService(
            self.path, supervisor=_Supervisor([]), committee=committee
        )
        section = self._sections(svc.generate(symbol="RELIANCE"))["committee"]
        text = section["items"][0]["text"]
        self.assertIn("constructive", text)
        self.assertIn("cautious", text)
        self.assertEqual(section["items"][0]["attribution"], "committee (unresolved)")

    def test_committee_is_skipped_without_a_symbol(self) -> None:
        svc = DailyDigestService(
            self.path, supervisor=_Supervisor([]), committee=lambda s: {}
        )
        self.assertNotIn("committee", self._sections(svc.generate()))

    def test_a_failing_committee_is_a_gap_not_a_crash(self) -> None:
        def boom(symbol: str) -> dict:
            raise RuntimeError("member unavailable")

        svc = DailyDigestService(
            self.path, supervisor=_Supervisor([]), committee=boom
        )
        section = self._sections(svc.generate(symbol="RELIANCE"))["committee"]
        self.assertTrue(any("member unavailable" in g for g in section["gaps"]))
        self.assertEqual(section["items"], [])

    def test_digests_persist_newest_first(self) -> None:
        svc = DailyDigestService(self.path, supervisor=_Supervisor([]))
        first = svc.generate()
        second = svc.generate(symbol="RELIANCE")
        listed = svc.list(limit=5)["digests"]
        self.assertEqual(len(listed), 2)
        self.assertEqual(svc.latest()["digest_id"], second["digest_id"])
        self.assertIn(first["digest_id"], {d["digest_id"] for d in listed})

    def test_latest_is_none_before_anything_is_generated(self) -> None:
        self.assertIsNone(DailyDigestService(self.path).latest())

    def test_digest_takes_no_action(self) -> None:
        """Safety: the digest is a view. It must expose no verbs."""
        public = [m for m in dir(DailyDigestService) if not m.startswith("_")]
        for forbidden in ("order", "trade", "submit", "approve", "retire", "refresh"):
            self.assertFalse(
                [m for m in public if forbidden in m.lower()],
                f"digest must not expose {forbidden!r}",
            )


if __name__ == "__main__":
    unittest.main()
