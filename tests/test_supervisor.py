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


if __name__ == "__main__":
    unittest.main()
