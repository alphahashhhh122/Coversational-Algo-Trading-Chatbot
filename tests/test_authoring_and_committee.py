from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.agents.base import AgentTask
from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.authored_agent_service import (
    AuthoredAgentService,
)
from iimc_trading_platform.services.committee_service import CommitteeService


class _CustomStrategies:
    def __init__(self, missing: list[str] | None = None) -> None:
        self.missing = missing or []

    def get_spec(self, spec_id: str) -> dict:
        return {
            "spec": {
                "name": "My Momentum Idea",
                "symbol": "RELIANCE",
                "description": "Buys strength.",
            },
            "validation": {"missing_capabilities": self.missing},
        }


class _Optimizer:
    def __init__(self, out_ret: float = 4.0, trades: int = 9) -> None:
        self.out_ret = out_ret
        self.trades = trades
        self.calls = 0

    def walk_forward_spec(self, *, dataset_id, spec, split_ratio=0.7, min_trades=3):
        self.calls += 1
        return {
            "strategy": "rule_spec",
            "dataset_id": dataset_id,
            "status": "ok",
            "train_bars": 700,
            "test_bars": 300,
            "in_sample_return_pct": 6.0,
            "in_sample_trades": 20,
            "out_of_sample_return_pct": self.out_ret,
            "out_of_sample_trades": self.trades,
            "out_of_sample_drawdown": -120.0,
            "verdict": "holds_up",
        }


class AuthoredAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)
        self.svc = AuthoredAgentService(
            self.path,
            _CustomStrategies(),
            _Optimizer(),
            lambda symbol, exchange: "ds_reliance",
        )

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_registers_as_versioned_strategy_agent(self) -> None:
        result = self.svc.register_from_spec(spec_id="spec_1")
        self.assertEqual(result["name"], "my_momentum_idea")
        self.assertEqual(result["version"], "1.0")
        self.assertEqual(result["agent_id"], "my_momentum_idea@1.0")
        listed = self.svc.list_authored()["authored"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["config"]["spec_id"], "spec_1")

    def test_reregistration_versions_instead_of_mutating(self) -> None:
        first = self.svc.register_from_spec(spec_id="spec_1")
        second = self.svc.register_from_spec(spec_id="spec_1")
        self.assertEqual(first["version"], "1.0")
        self.assertEqual(second["version"], "2.0")
        # Both versions survive: lineage is never lost.
        self.assertEqual(len(self.svc.list_authored()["authored"]), 2)

    def test_unvalidated_spec_is_refused(self) -> None:
        svc = AuthoredAgentService(
            self.path,
            _CustomStrategies(missing=["exotic_indicator"]),
            _Optimizer(),
            lambda s, e: "ds",
        )
        with self.assertRaises(ValueError) as ctx:
            svc.register_from_spec(spec_id="spec_1")
        self.assertIn("exotic_indicator", str(ctx.exception))

    def test_run_produces_walk_forward_findings_and_evidence(self) -> None:
        registered = self.svc.register_from_spec(spec_id="spec_1")
        result = self.svc.run_authored(
            registered["agent_id"], AgentTask(task_type="validate")
        )
        self.assertEqual(result.status, "ok")
        # Same shape as built-in strategy agents -> same scorer applies.
        self.assertIn("out_of_sample_return_pct", result.findings)
        self.assertEqual(result.evidence[0]["spec_id"], "spec_1")

    def test_missing_market_data_is_partial_not_invented(self) -> None:
        svc = AuthoredAgentService(
            self.path, _CustomStrategies(), _Optimizer(), lambda s, e: None
        )
        registered = svc.register_from_spec(spec_id="spec_1")
        result = svc.run_authored(
            registered["agent_id"], AgentTask(task_type="validate")
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("no stored market data" in g for g in result.gaps))

    def test_authored_agent_scores_on_the_same_leaderboard_rules(self) -> None:
        from iimc_trading_platform.services.agent_evaluation_service import (
            AgentEvaluationService,
        )

        registered = self.svc.register_from_spec(spec_id="spec_1")
        result = self.svc.run_authored(
            registered["agent_id"], AgentTask(task_type="validate")
        )
        card = AgentEvaluationService(self.path).score_run(
            {"status": result.status, "findings": result.findings}, "strategy"
        )
        self.assertEqual(card["status"], "scored")
        # Scored off the OOS number (4.0), not the flattering in-sample 6.0.
        self.assertEqual(card["metrics"]["out_of_sample_return_pct"], 4.0)


class CommitteeTest(unittest.TestCase):
    def test_agreement_is_reported(self) -> None:
        def runner(member, symbol, exchange):
            return {"technicals": {"available": True, "trend": "uptrend"}}

        result = CommitteeService(runner).run("RELIANCE")
        self.assertEqual(result["disagreements"], [])
        self.assertTrue(result["agreements"])
        self.assertIn("constructive", result["agreements"][0])

    def test_a_lone_read_is_not_called_a_consensus(self) -> None:
        """One member agreeing with itself carries less weight — say so."""

        def runner(member, symbol, exchange):
            if member == "market_researcher":
                return {"technicals": {"available": True, "trend": "uptrend"}}
            return {"technicals": {"available": False}}

        result = CommitteeService(runner).run("RELIANCE")
        self.assertIn("Only one member", result["agreements"][0])
        self.assertIn("constructive", result["agreements"][0])

    def test_disagreement_is_preserved_not_averaged(self) -> None:
        def runner(member, symbol, exchange):
            if member == "market_researcher":
                return {"technicals": {"available": True, "trend": "uptrend"}}
            return {"verdict": "overfit"}

        result = CommitteeService(runner).run("RELIANCE")
        self.assertEqual(result["agreements"], [])
        self.assertEqual(len(result["disagreements"]), 1)
        positions = result["disagreements"][0]["positions"]
        self.assertEqual(
            {p["stance"] for p in positions}, {"constructive", "cautious"}
        )
        self.assertIn("averaged", result["disagreements"][0]["note"])

    def test_member_failure_becomes_a_gap_not_a_silent_drop(self) -> None:
        def runner(member, symbol, exchange):
            if member == "strategy_validator":
                raise ValueError("no dataset")
            return {"technicals": {"available": True, "trend": "uptrend"}}

        result = CommitteeService(runner).run("RELIANCE")
        self.assertTrue(any("strategy_validator" in g for g in result["gaps"]))

    def test_no_directional_read_is_a_gap(self) -> None:
        def runner(member, symbol, exchange):
            return {"technicals": {"available": False}}

        result = CommitteeService(runner).run("RELIANCE")
        self.assertEqual(len(result["gaps"]), 2)
        self.assertEqual(result["agreements"], [])

    def test_empty_symbol_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CommitteeService(lambda *a: {}).run("  ")


if __name__ == "__main__":
    unittest.main()
