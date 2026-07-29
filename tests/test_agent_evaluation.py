from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.agent_evaluation_service import (
    SCORING_VERSION,
    AgentEvaluationService,
)


class StrategyScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = AgentEvaluationService(Path("unused.duckdb"))

    def _score(self, findings: dict) -> dict:
        return self.svc.score_run({"status": "ok", "findings": findings}, "strategy")

    def test_in_sample_alone_is_never_scored(self) -> None:
        # A run with only in-sample numbers must not produce a ranking.
        card = self._score({"in_sample_return_pct": 42.0})
        self.assertEqual(card["status"], "inconclusive")
        self.assertIsNone(card["composite"])
        self.assertIn("walk-forward", card["reason"])

    def test_overfit_scores_below_holds_up_at_equal_return(self) -> None:
        holds = self._score({
            "out_of_sample_return_pct": 10.0, "out_of_sample_trades": 12,
            "out_of_sample_excess_return_pct": 10.0,
            "verdict": "holds_up",
        })
        overfit = self._score({
            "out_of_sample_return_pct": 10.0, "out_of_sample_trades": 12,
            "out_of_sample_excess_return_pct": 10.0,
            "verdict": "overfit",
        })
        self.assertGreater(holds["composite"], overfit["composite"])

    def test_too_few_trades_is_inconclusive_not_zero(self) -> None:
        card = self._score({
            "out_of_sample_return_pct": 8.0, "out_of_sample_trades": 1,
            "verdict": "holds_up",
        })
        self.assertEqual(card["status"], "inconclusive")
        self.assertIsNone(card["composite"])
        self.assertIn("out-of-sample trade", card["reason"])

    def test_losing_strategy_scores_negative(self) -> None:
        card = self._score({
            "out_of_sample_return_pct": -5.0, "out_of_sample_trades": 20,
            "out_of_sample_excess_return_pct": -5.0,
            "verdict": "poor",
        })
        self.assertEqual(card["status"], "scored")
        self.assertLess(card["composite"], 0)

    def test_failed_run_is_inconclusive(self) -> None:
        card = self.svc.score_run({"status": "failed", "findings": {}}, "strategy")
        self.assertEqual(card["status"], "inconclusive")


class ResearchScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = AgentEvaluationService(Path("unused.duckdb"))

    def test_full_coverage_beats_partial(self) -> None:
        full = self.svc.score_run(
            {"status": "ok", "findings": {
                "sections_available": ["valuation", "fundamentals", "technicals", "news"]
            }, "evidence": []}, "research",
        )
        partial = self.svc.score_run(
            {"status": "ok", "findings": {"sections_available": ["valuation"]},
             "evidence": []}, "research",
        )
        self.assertGreater(full["composite"], partial["composite"])
        self.assertEqual(full["metrics"]["coverage"], 1.0)

    def test_citations_add_bounded_bonus(self) -> None:
        base = {"sections_available": ["valuation", "news"]}
        without = self.svc.score_run(
            {"status": "ok", "findings": base, "evidence": []}, "research"
        )
        with_cites = self.svc.score_run(
            {"status": "ok",
             "findings": {**base, "citations": [{"url": "https://example.com/a"}]},
             "evidence": []}, "research",
        )
        self.assertGreater(with_cites["composite"], without["composite"])

    def test_empty_research_is_inconclusive(self) -> None:
        card = self.svc.score_run(
            {"status": "ok", "findings": {"sections_available": []}, "evidence": []},
            "research",
        )
        self.assertEqual(card["status"], "inconclusive")


class MonitorScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = AgentEvaluationService(Path("unused.duckdb"))

    def test_verified_fires_score_full_precision(self) -> None:
        card = self.svc.score_run(
            {"status": "ok", "findings": {
                "checked": 2, "fired": [{"symbol": "X", "last_value": 25.0}],
                "errors": [],
            }}, "monitor",
        )
        self.assertEqual(card["metrics"]["precision"], 1.0)
        self.assertEqual(card["composite"], 100.0)

    def test_unavailable_data_lowers_coverage_not_precision(self) -> None:
        card = self.svc.score_run(
            {"status": "ok", "findings": {
                "checked": 2, "fired": [], "errors": ["X: no candles"],
            }}, "monitor",
        )
        self.assertEqual(card["metrics"]["data_coverage"], 0.5)
        self.assertEqual(card["metrics"]["precision"], 1.0)

    def test_no_watches_is_inconclusive(self) -> None:
        card = self.svc.score_run(
            {"status": "ok", "findings": {"checked": 0, "fired": []}}, "monitor"
        )
        self.assertEqual(card["status"], "inconclusive")


class LeaderboardTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)
        self.svc = AgentEvaluationService(self.path)
        con_agents = [
            ("alpha@1.0", "alpha", "strategy"),
            ("beta@1.0", "beta", "strategy"),
            ("gamma@1.0", "gamma", "research"),
        ]
        from iimc_trading_platform.db import connect
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        con = connect(self.path)
        try:
            for agent_id, name, category in con_agents:
                con.execute(
                    "INSERT INTO agents VALUES (?, ?, '1.0', ?, '', '[]', '{}',"
                    " 'test', 'active', ?)",
                    [agent_id, name, category, now],
                )
        finally:
            con.close()

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_ranking_orders_by_composite_and_links_evidence(self) -> None:
        self.svc.record_score(
            agent_id="alpha@1.0", version="1.0", run_id="arun_a",
            scorecard={"status": "scored", "composite": 5.0, "metrics": {}},
            eval_dataset_id="ds1",
        )
        self.svc.record_score(
            agent_id="beta@1.0", version="1.0", run_id="arun_b",
            scorecard={"status": "scored", "composite": 9.0, "metrics": {}},
        )
        board = self.svc.leaderboard()
        self.assertEqual([e["name"] for e in board["ranked"]], ["beta", "alpha"])
        self.assertEqual(board["ranked"][0]["rank"], 1)
        # Every ranked row links back to its run (evidence).
        self.assertTrue(all(e["run_id"] for e in board["ranked"]))
        self.assertEqual(board["ranked"][1]["eval_dataset_id"], "ds1")

    def test_inconclusive_is_unranked_not_zero(self) -> None:
        self.svc.record_score(
            agent_id="alpha@1.0", version="1.0", run_id="arun_a",
            scorecard={"status": "inconclusive", "composite": None,
                       "reason": "too few trades", "metrics": {}},
        )
        board = self.svc.leaderboard()
        self.assertEqual(board["ranked"], [])
        self.assertEqual(len(board["unranked"]), 1)
        self.assertEqual(board["unranked"][0]["reason"], "too few trades")

    def test_only_latest_score_per_agent_counts(self) -> None:
        self.svc.record_score(
            agent_id="alpha@1.0", version="1.0", run_id="arun_old",
            scorecard={"status": "scored", "composite": 1.0, "metrics": {}},
        )
        self.svc.record_score(
            agent_id="alpha@1.0", version="1.0", run_id="arun_new",
            scorecard={"status": "scored", "composite": 7.0, "metrics": {}},
        )
        board = self.svc.leaderboard()
        self.assertEqual(len(board["ranked"]), 1)
        self.assertEqual(board["ranked"][0]["composite"], 7.0)
        self.assertEqual(board["ranked"][0]["run_id"], "arun_new")

    def test_category_filter(self) -> None:
        self.svc.record_score(
            agent_id="alpha@1.0", version="1.0", run_id="arun_a",
            scorecard={"status": "scored", "composite": 5.0, "metrics": {}},
        )
        self.svc.record_score(
            agent_id="gamma@1.0", version="1.0", run_id="arun_g",
            scorecard={"status": "scored", "composite": 80.0, "metrics": {}},
        )
        board = self.svc.leaderboard(category="strategy")
        self.assertEqual([e["name"] for e in board["ranked"]], ["alpha"])


class PreBenchmarkRunTest(unittest.TestCase):
    """Runs recorded before benchmark-relative scoring cannot be ranked.

    This is the exact findings shape found in the live database: no
    ``out_of_sample_benchmark_pct`` key at all, as opposed to a current run
    that carries the key with a None value because the benchmark could not be
    computed. Falling back to raw return would let a pre-benchmark number
    compete on the same leaderboard as benchmark-relative ones.
    """

    def setUp(self) -> None:
        self.svc = AgentEvaluationService(Path("unused.duckdb"))

    def _score(self, findings: dict) -> dict:
        return self.svc.score_run({"status": "ok", "findings": findings}, "strategy")

    def test_a_run_with_no_benchmark_key_is_inconclusive(self) -> None:
        card = self._score({
            "out_of_sample_return_pct": 5.0,
            "out_of_sample_trades": 20,
            "verdict": "holds_up",
        })
        self.assertEqual(card["status"], "inconclusive")
        self.assertIsNone(card["composite"])
        self.assertIn("re-run", card["reason"])

    def test_an_uncomputable_benchmark_is_also_unrankable(self) -> None:
        """Same answer whether the benchmark is missing or uncomputable.

        Either way there is no excess return, and a raw return cannot be sorted
        against excess returns without changing what the column means.
        """
        card = self._score({
            "out_of_sample_return_pct": 5.0,
            "out_of_sample_trades": 20,
            "verdict": "holds_up",
            "out_of_sample_benchmark_pct": None,
            "out_of_sample_excess_return_pct": None,
        })
        self.assertEqual(card["status"], "inconclusive")
        self.assertIsNone(card["composite"])


class RescoreHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)
        self.svc = AgentEvaluationService(self.path)
        from datetime import datetime, timezone
        import json as _json

        from iimc_trading_platform.db import connect

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        con = connect(self.path)
        try:
            for agent_id, name in (("alpha@1.0", "alpha"), ("beta@1.0", "beta")):
                con.execute(
                    "INSERT INTO agents VALUES (?, ?, '1.0', 'strategy', '', "
                    "'[]', '{}', 'test', 'active', ?)", [agent_id, name, now])
            # An old run, on its own agent: no benchmark recorded.
            con.execute(
                "INSERT INTO agent_runs VALUES ('arun_old', 'beta@1.0', '1.0', "
                "'{}', 'ok', ?, '[]', '[]', '{}', ?, ?)",
                [_json.dumps({
                    "out_of_sample_return_pct": 5.0,
                    "out_of_sample_trades": 20,
                    "verdict": "holds_up",
                }), now, now])
            # A current run, fully instrumented.
            con.execute(
                "INSERT INTO agent_runs VALUES ('arun_new', 'alpha@1.0', '1.0', "
                "'{}', 'ok', ?, '[]', '[]', '{}', ?, ?)",
                [_json.dumps({
                    "out_of_sample_return_pct": 5.0,
                    "out_of_sample_trades": 20,
                    "verdict": "holds_up",
                    "out_of_sample_benchmark_pct": 2.0,
                    "out_of_sample_excess_return_pct": 3.0,
                    "out_of_sample_sharpe": 1.2,
                }), now, now])
        finally:
            con.close()

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_every_run_is_rescored_under_the_current_rule(self) -> None:
        out = self.svc.rescore_history()
        self.assertEqual(out["scoring_version"], SCORING_VERSION)
        self.assertEqual(out["runs_rescored"], 2)
        self.assertEqual(out["now_ranked"], 1)
        self.assertEqual(out["now_inconclusive"], 1)

    def test_the_old_run_is_named_not_quietly_ranked(self) -> None:
        self.svc.rescore_history()
        board = self.svc.leaderboard()
        self.assertEqual([e["run_id"] for e in board["ranked"]], ["arun_new"])
        self.assertTrue(
            any("re-run" in (e["reason"] or "") for e in board["unranked"])
        )

    def test_rescoring_twice_does_not_duplicate_scores(self) -> None:
        self.svc.rescore_history()
        self.svc.rescore_history()
        from iimc_trading_platform.db import connect

        con = connect(self.path)
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM agent_scores").fetchone()[0], 2
            )
        finally:
            con.close()

    def test_ranked_rows_carry_the_version_that_produced_them(self) -> None:
        self.svc.rescore_history()
        entry = self.svc.leaderboard()["ranked"][0]
        self.assertEqual(entry["scoring_version"], SCORING_VERSION)
        self.assertEqual(entry["current_scoring_version"], SCORING_VERSION)

    def test_an_empty_database_rescores_nothing_without_complaint(self) -> None:
        from iimc_trading_platform.db import connect

        con = connect(self.path)
        try:
            con.execute("DELETE FROM agent_runs")
        finally:
            con.close()
        self.assertEqual(self.svc.rescore_history()["runs_rescored"], 0)


if __name__ == "__main__":
    unittest.main()


class RiskAdjustedScoringTest(unittest.TestCase):
    """Phase A proofs: the ranking must respect benchmark and risk."""

    def setUp(self) -> None:
        self.svc = AgentEvaluationService(Path("unused.duckdb"))

    def _score(self, **findings) -> dict:
        base = {
            "out_of_sample_return_pct": 5.0,
            "out_of_sample_trades": 20,
            "verdict": "holds_up",
            # Ranking requires a benchmark; these cases isolate the risk
            # penalties, so the excess matches the raw return.
            "out_of_sample_excess_return_pct": 5.0,
        }
        base.update(findings)
        return self.svc.score_run({"status": "ok", "findings": base}, "strategy")

    def test_beating_the_benchmark_ranks_above_trailing_it(self) -> None:
        # Same raw return; one beat buy-and-hold, the other trailed it badly.
        beat = self._score(
            out_of_sample_return_pct=5.0,
            out_of_sample_excess_return_pct=3.0,
            out_of_sample_benchmark_pct=2.0,
        )
        trailed = self._score(
            out_of_sample_return_pct=5.0,
            out_of_sample_excess_return_pct=-5.0,
            out_of_sample_benchmark_pct=10.0,
        )
        self.assertGreater(beat["composite"], trailed["composite"])
        # Trailing the benchmark by more than you returned is a negative score.
        self.assertLess(trailed["composite"], 0)

    def test_higher_drawdown_ranks_below_equal_return(self) -> None:
        calm = self._score(
            out_of_sample_excess_return_pct=5.0, out_of_sample_drawdown_pct=5.0
        )
        wild = self._score(
            out_of_sample_excess_return_pct=5.0, out_of_sample_drawdown_pct=40.0
        )
        self.assertGreater(calm["composite"], wild["composite"])

    def test_negative_sharpe_is_penalised(self) -> None:
        good = self._score(
            out_of_sample_excess_return_pct=5.0, out_of_sample_sharpe=1.5
        )
        bad = self._score(
            out_of_sample_excess_return_pct=5.0, out_of_sample_sharpe=-3.0
        )
        self.assertGreater(good["composite"], bad["composite"])

    def test_no_benchmark_means_no_rank(self) -> None:
        """Raw return and excess return are different quantities.

        Ranking one beside the other sorts a +5% raw result against a +5%
        *excess over holding* result as if they were the same claim.
        """
        card = self._score(out_of_sample_excess_return_pct=None)
        self.assertEqual(card["status"], "inconclusive")
        self.assertIsNone(card["composite"])
        self.assertIn("no benchmark", card["reason"])

    def test_windows_consistency_scales_the_score(self) -> None:
        lucky = self._score(
            out_of_sample_excess_return_pct=8.0, windows=4, windows_held_up=1
        )
        consistent = self._score(
            out_of_sample_excess_return_pct=8.0, windows=4, windows_held_up=4
        )
        self.assertGreater(consistent["composite"], lucky["composite"])

    def test_scoring_version_is_recorded(self) -> None:
        card = self._score(out_of_sample_excess_return_pct=1.0)
        self.assertEqual(card["metrics"]["scoring_version"], SCORING_VERSION)
