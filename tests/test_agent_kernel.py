from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from iimc_trading_platform.agents.base import (
    AgentBudget,
    AgentResult,
    AgentTask,
    ServiceAgent,
)
from iimc_trading_platform.agents.roster import build_founding_roster
from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.agent_registry_service import (
    AgentRegistryService,
)
from iimc_trading_platform.tools.registry import build_default_tool_registry


def _stub_agent(runner, interpret=None) -> ServiceAgent:
    return ServiceAgent(
        agent_id="stub@1.0",
        name="stub",
        version="1.0",
        category="research",
        description="stub agent",
        capabilities=("research",),
        runner=runner,
        interpret=interpret or (lambda payload: (payload, [], [])),
    )


class ServiceAgentContractTest(unittest.TestCase):
    def test_ok_run_carries_findings_and_cost(self) -> None:
        agent = _stub_agent(lambda task: {"value": 42})
        result = agent.run(AgentTask(task_type="research"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.findings, {"value": 42})
        self.assertIn("seconds", result.cost)

    def test_gaps_downgrade_to_partial(self) -> None:
        agent = _stub_agent(
            lambda task: {},
            interpret=lambda payload: ({}, [], ["fundamentals missing"]),
        )
        result = agent.run(AgentTask(task_type="research"))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.gaps, ["fundamentals missing"])

    def test_exception_becomes_failed_not_raised(self) -> None:
        def boom(task):
            raise ValueError("provider down")

        result = _stub_agent(boom).run(AgentTask(task_type="research"))
        self.assertEqual(result.status, "failed")
        self.assertIn("provider down", result.gaps[0])

    def test_budget_overrun_is_partial_and_honest(self) -> None:
        def slow(task):
            time.sleep(0.05)
            return {}

        result = _stub_agent(slow).run(
            AgentTask(task_type="research", budget=AgentBudget(max_seconds=0.01))
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("budget" in g for g in result.gaps))

    def test_invalid_category_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ServiceAgent(
                agent_id="x@1", name="x", version="1", category="rogue",
                description="", capabilities=(),
                runner=lambda t: {}, interpret=lambda p: (p, [], []),
            )


class FoundingRosterTest(unittest.TestCase):
    def test_roster_builds_against_real_tool_registry(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        roster = build_founding_roster(registry)
        names = {a.name for a in roster}
        self.assertEqual(
            names,
            {
                "market_researcher", "deep_researcher", "strategy_discoverer",
                "strategy_validator", "comparator", "sentinel",
                "fundamental_analyst", "news_analyst", "document_analyst",
            },
        )
        # Every agent declares a valid category and non-empty description.
        for agent in roster:
            self.assertIn(
                agent.category, {"research", "strategy", "monitor", "assistant"}
            )
            self.assertTrue(agent.description)

    def test_injected_runners_add_their_agents(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        full = build_founding_roster(
            registry,
            chat_runner=lambda m: {"answer": "hi"},
            committee_runner=lambda s, e: {"opinions": {}},
        )
        names = {a.name for a in full}
        self.assertIn("conversational_assistant", names)
        self.assertIn("research_committee", names)
        # The plan's bar: at least ten registered agents.
        self.assertGreaterEqual(len(full), 10)

    def test_symbol_requiring_agent_fails_honestly_without_symbol(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        roster = {a.name: a for a in build_founding_roster(registry)}
        result = roster["market_researcher"].run(AgentTask(task_type="research"))
        self.assertEqual(result.status, "failed")
        self.assertTrue(any("symbol" in g for g in result.gaps))


class AgentRegistryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)
        self.registry = AgentRegistryService(self.path)

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_sync_roster_is_idempotent(self) -> None:
        agent = _stub_agent(lambda t: {})
        self.registry.sync_roster([agent])
        self.registry.sync_roster([agent])
        listed = self.registry.list()["agents"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "stub")

    def test_record_and_list_runs_by_id_or_name(self) -> None:
        agent = _stub_agent(lambda t: {})
        self.registry.sync_roster([agent])
        task = AgentTask(task_type="research", symbol="RELIANCE")
        run_id = self.registry.record_run(
            agent, task, AgentResult(status="ok", findings={"x": 1})
        )
        self.assertTrue(run_id.startswith("arun_"))
        by_id = self.registry.list_runs("stub@1.0")["runs"]
        by_name = self.registry.list_runs("stub")["runs"]
        self.assertEqual(len(by_id), 1)
        self.assertEqual(len(by_name), 1)
        self.assertEqual(by_id[0]["task"]["symbol"], "RELIANCE")
        # run_count surfaces in the listing.
        self.assertEqual(self.registry.list()["agents"][0]["run_count"], 1)

    def test_get_unknown_agent_returns_none(self) -> None:
        self.assertIsNone(self.registry.get("nope"))


if __name__ == "__main__":
    unittest.main()
