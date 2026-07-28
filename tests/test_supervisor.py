from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iimc_trading_platform.agents.base import (
    AgentBudget,
    AgentTask,
    BudgetLedger,
    ServiceAgent,
)
from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.supervisor_service import SupervisorService


class BudgetLedgerTest(unittest.TestCase):
    def test_no_breach_within_caps(self) -> None:
        ledger = BudgetLedger(AgentBudget(max_seconds=10, max_steps=5, max_llm_calls=3))
        ledger.step(2)
        ledger.llm_call(1)
        self.assertEqual(ledger.exceeded(1.0), [])

    def test_each_cap_is_named_when_breached(self) -> None:
        ledger = BudgetLedger(AgentBudget(max_seconds=1, max_steps=1, max_llm_calls=1))
        ledger.step(5)
        ledger.llm_call(5)
        breaches = ledger.exceeded(99.0)
        self.assertEqual(len(breaches), 3)
        self.assertTrue(any("steps" in b for b in breaches))
        self.assertTrue(any("LLM calls" in b for b in breaches))

    def test_agent_reports_step_cost(self) -> None:
        agent = ServiceAgent(
            agent_id="s@1", name="s", version="1", category="research",
            description="d", capabilities=(),
            runner=lambda t: {}, interpret=lambda p: (p, [], []),
        )
        result = agent.run(AgentTask(task_type="t"))
        self.assertEqual(result.cost["steps"], 1)
        self.assertIn("llm_calls", result.cost)


class SupervisorTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)
        self._agent("alpha@1.0", "alpha", "strategy")

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def _agent(self, agent_id: str, name: str, category: str) -> None:
        con = connect(self.path)
        try:
            con.execute(
                "INSERT INTO agents VALUES (?, ?, '1.0', ?, '', '[]', '{}', "
                "'test', 'active', ?)",
                [agent_id, name, category, datetime.now(timezone.utc).replace(tzinfo=None)],
            )
        finally:
            con.close()

    def _score(self, agent_id: str, composite: float | None, minutes_ago: int) -> None:
        con = connect(self.path)
        try:
            con.execute(
                "INSERT INTO agent_scores VALUES (?, ?, '1.0', ?, NULL, '{}', ?, ?)",
                [
                    f"sc_{minutes_ago}_{agent_id}",
                    agent_id,
                    f"arun_{minutes_ago}",
                    composite,
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(minutes=minutes_ago),
                ],
            )
        finally:
            con.close()

    def test_material_degradation_is_flagged(self) -> None:
        self._score("alpha@1.0", 10.0, minutes_ago=60)
        self._score("alpha@1.0", 4.0, minutes_ago=1)  # -60%
        findings = SupervisorService(self.path).detect_drift()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "score_degraded")
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertIn("→", findings[0]["summary"])

    def test_improvement_is_informational_not_a_warning(self) -> None:
        self._score("alpha@1.0", 4.0, minutes_ago=60)
        self._score("alpha@1.0", 10.0, minutes_ago=1)
        findings = SupervisorService(self.path).detect_drift()
        self.assertEqual(findings[0]["kind"], "score_improved")
        self.assertEqual(findings[0]["severity"], "info")

    def test_small_wobble_is_not_reported(self) -> None:
        self._score("alpha@1.0", 10.0, minutes_ago=60)
        self._score("alpha@1.0", 9.5, minutes_ago=1)  # -5%, under threshold
        self.assertEqual(SupervisorService(self.path).detect_drift(), [])

    def test_becoming_unscorable_is_flagged(self) -> None:
        self._score("alpha@1.0", 8.0, minutes_ago=60)
        self._score("alpha@1.0", None, minutes_ago=1)
        findings = SupervisorService(self.path).detect_drift()
        self.assertEqual(findings[0]["kind"], "became_inconclusive")

    def test_single_score_has_nothing_to_compare(self) -> None:
        self._score("alpha@1.0", 8.0, minutes_ago=1)
        self.assertEqual(SupervisorService(self.path).detect_drift(), [])

    def test_repeated_detection_does_not_pile_up_duplicates(self) -> None:
        self._score("alpha@1.0", 10.0, minutes_ago=60)
        self._score("alpha@1.0", 4.0, minutes_ago=1)
        svc = SupervisorService(self.path)
        svc.detect_drift()
        svc.detect_drift()
        svc.detect_drift()
        self.assertEqual(len(svc.list_findings()["findings"]), 1)

    def test_acknowledged_findings_are_hidden_by_default(self) -> None:
        self._score("alpha@1.0", 10.0, minutes_ago=60)
        self._score("alpha@1.0", 4.0, minutes_ago=1)
        svc = SupervisorService(self.path)
        finding = svc.detect_drift()[0]
        svc.acknowledge(finding["finding_id"])
        self.assertEqual(svc.list_findings()["findings"], [])
        self.assertEqual(
            len(svc.list_findings(include_acknowledged=True)["findings"]), 1
        )

    def test_sweep_runs_agents_and_survives_failures(self) -> None:
        def runner(name, symbol):
            if name == "broken":
                raise ValueError("provider down")
            return {"status": "ok", "run_id": f"arun_{name}"}

        svc = SupervisorService(self.path, run_agent=runner)
        result = svc.sweep(["alpha", "broken"])
        self.assertEqual(len(result["ran"]), 1)
        self.assertTrue(any("broken" in e for e in result["errors"]))

    def test_supervisor_only_flags_never_acts(self) -> None:
        """Safety: the supervisor must have no action surface at all."""
        public = [m for m in dir(SupervisorService) if not m.startswith("_")]
        for forbidden in ("retire", "disable", "trade", "order", "submit", "approve"):
            self.assertFalse(
                [m for m in public if forbidden in m.lower()],
                f"supervisor must not expose {forbidden!r}",
            )



class _Freshness:
    """Reports the datasets named in ``stale`` as stale, others as fresh."""

    def __init__(self, stale: set[str] | None = None, boom: set[str] | None = None):
        self.stale = stale or set()
        self.boom = boom or set()

    def assess(self, dataset_id, purpose, *, reference_time=None):
        if dataset_id in self.boom:
            raise ValueError("no policy for this dataset")
        return {
            "status": "stale" if dataset_id in self.stale else "fresh",
            "age_minutes": 999 if dataset_id in self.stale else 1,
            "threshold_minutes": 60,
        }


class DataStalenessTest(SupervisorTest):
    """Phase D: the supervisor watches data, and may refresh it - only that."""

    def _dataset(self, dataset_id: str) -> None:
        from datetime import datetime as _dt

        con = connect(self.path)
        try:
            con.execute(
                "INSERT INTO data_catalog VALUES (?, 'market_data', 'ohlcv', "
                "'RELIANCE', 'NSE', 'D', ?, ?, 100, 'ohlcv', 'src', "
                "'validated', NULL, ?)",
                [dataset_id, _dt(2026, 1, 1), _dt(2026, 6, 1),
                 datetime.now(timezone.utc).replace(tzinfo=None)],
            )
        finally:
            con.close()

    def test_stale_dataset_is_flagged_and_refresh_enqueued(self) -> None:
        self._dataset("ds_stale")
        queued: list[str] = []
        svc = SupervisorService(
            self.path,
            freshness=_Freshness(stale={"ds_stale"}),
            enqueue_refresh=queued.append,
        )
        findings, refreshed = svc.check_data_health()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "data_stale")
        self.assertEqual(queued, ["ds_stale"])
        self.assertEqual(refreshed, ["ds_stale"])
        self.assertIn("refresh has been queued", findings[0]["summary"])

    def test_fresh_dataset_produces_no_noise(self) -> None:
        self._dataset("ds_fresh")
        svc = SupervisorService(self.path, freshness=_Freshness())
        findings, refreshed = svc.check_data_health()
        self.assertEqual(findings, [])
        self.assertEqual(refreshed, [])

    def test_without_a_refresh_hook_it_only_flags(self) -> None:
        self._dataset("ds_stale")
        svc = SupervisorService(self.path, freshness=_Freshness(stale={"ds_stale"}))
        findings, refreshed = svc.check_data_health()
        self.assertEqual(len(findings), 1)
        self.assertEqual(refreshed, [])
        self.assertIn("no refresh path", findings[0]["summary"])

    def test_a_failing_refresh_still_reports_the_staleness(self) -> None:
        self._dataset("ds_stale")

        def boom(dataset_id):
            raise RuntimeError("queue unavailable")

        svc = SupervisorService(
            self.path,
            freshness=_Freshness(stale={"ds_stale"}),
            enqueue_refresh=boom,
        )
        findings, refreshed = svc.check_data_health()
        self.assertEqual(len(findings), 1)
        self.assertEqual(refreshed, [])

    def test_unassessable_dataset_is_reported_not_swallowed(self) -> None:
        self._dataset("ds_odd")
        svc = SupervisorService(self.path, freshness=_Freshness(boom={"ds_odd"}))
        findings, _ = svc.check_data_health()
        self.assertEqual(findings[0]["kind"], "data_unassessable")

    def test_no_freshness_service_means_no_data_findings(self) -> None:
        self._dataset("ds_any")
        findings, refreshed = SupervisorService(self.path).check_data_health()
        self.assertEqual((findings, refreshed), ([], []))


class RegimeDetectionTest(SupervisorTest):
    def _candles(self, moves: list[float]) -> list[dict]:
        price = 100.0
        out = [{"close": price}]
        for m in moves:
            price *= 1 + m
            out.append({"close": price})
        return out

    def test_volatility_spike_is_flagged(self) -> None:
        calm = [0.001, -0.001] * 25
        wild = [0.05, -0.05] * 25
        svc = SupervisorService(self.path)
        finding = svc.detect_regime_shift(
            self._candles(calm + wild), dataset_id="ds_x"
        )
        self.assertIsNotNone(finding)
        self.assertEqual(finding["kind"], "regime_shift")
        self.assertIn("more volatile", finding["summary"])
        self.assertGreater(finding["detail"]["ratio"], 1.5)

    def test_calming_market_is_also_flagged(self) -> None:
        wild = [0.05, -0.05] * 25
        calm = [0.001, -0.001] * 25
        finding = SupervisorService(self.path).detect_regime_shift(
            self._candles(wild + calm), dataset_id="ds_x"
        )
        self.assertIsNotNone(finding)
        self.assertIn("calmer", finding["summary"])

    def test_steady_regime_is_not_flagged(self) -> None:
        steady = [0.01, -0.01] * 50
        self.assertIsNone(
            SupervisorService(self.path).detect_regime_shift(
                self._candles(steady), dataset_id="ds_x"
            )
        )

    def test_too_little_history_returns_nothing(self) -> None:
        self.assertIsNone(
            SupervisorService(self.path).detect_regime_shift(
                self._candles([0.01] * 5), dataset_id="ds_x"
            )
        )


class SelfHealingBoundaryTest(unittest.TestCase):
    def test_the_only_action_is_a_data_refresh(self) -> None:
        """Safety: self-healing must not extend beyond fetching data.

        The supervisor may enqueue a refresh because that cannot lose money.
        It must still expose no way to retire, reconfigure, or trade.
        """
        public = [m for m in dir(SupervisorService) if not m.startswith("_")]
        for forbidden in (
            "retire", "disable", "delete", "trade", "order", "submit", "approve",
        ):
            self.assertFalse(
                [m for m in public if forbidden in m.lower()],
                f"supervisor must not expose {forbidden!r}",
            )
        # And the one action it does take is explicitly injected, not internal.
        import inspect

        params = inspect.signature(SupervisorService.__init__).parameters
        self.assertIn("enqueue_refresh", params)


class RegimeInSweepTest(SupervisorTest):
    """D3: detection has to actually run, and re-validation has to follow it."""

    def _shifting_candles(self) -> list[dict]:
        price, out = 100.0, [{"close": 100.0}]
        for move in [0.001, -0.001] * 25 + [0.05, -0.05] * 25:
            price *= 1 + move
            out.append({"close": price})
        return out

    def test_sweep_detects_the_shift_and_reports_revalidation(self) -> None:
        candles = self._shifting_candles()
        svc = SupervisorService(
            self.path,
            run_agent=lambda name, symbol: {"status": "ok", "run_id": "arun_1"},
            load_candles=lambda symbol: candles,
        )
        result = svc.sweep(["alpha"], "RELIANCE")
        self.assertEqual(result["regime_shifts"], 1)
        regime = [f for f in result["findings"] if f["kind"] == "regime_shift"][0]
        # The agents were re-run in this same sweep, so their next scores are
        # earned under the new regime - the finding says so plainly.
        self.assertTrue(regime["detail"]["revalidated_in_this_sweep"])

    def test_without_a_runner_it_does_not_claim_revalidation(self) -> None:
        candles = self._shifting_candles()
        svc = SupervisorService(self.path, load_candles=lambda s: candles)
        result = svc.sweep(["alpha"], "RELIANCE")
        regime = [f for f in result["findings"] if f["kind"] == "regime_shift"][0]
        self.assertFalse(regime["detail"]["revalidated_in_this_sweep"])

    def test_missing_history_leaves_the_regime_check_silent(self) -> None:
        def no_data(symbol):
            raise ValueError("no stored history")

        svc = SupervisorService(self.path, load_candles=no_data)
        self.assertEqual(svc.sweep(["alpha"], "RELIANCE")["regime_shifts"], 0)

    def test_no_candle_loader_means_no_regime_findings(self) -> None:
        self.assertEqual(SupervisorService(self.path).check_regime("RELIANCE"), [])


if __name__ == "__main__":
    unittest.main()
