"""Repo-level safety invariants for the agent platform.

These encode the promise the whole design rests on: **no agent — registered,
scheduled, authored, remote, or racing in the arena — has a code path to the
broker.** Order approval is a deliberate human action in the web UI.

They are asserted structurally (imports and call names via the AST, exposed
method/tool names) rather than by reading prose, so a future refactor that
quietly adds a broker call fails the build instead of shipping.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_AGENT_MODULES = [
    "iimc_trading_platform/agents/base.py",
    "iimc_trading_platform/agents/roster.py",
    "iimc_trading_platform/services/agent_registry_service.py",
    "iimc_trading_platform/services/agent_evaluation_service.py",
    "iimc_trading_platform/services/arena_service.py",
    "iimc_trading_platform/services/contest_service.py",
    "iimc_trading_platform/services/authored_agent_service.py",
    "iimc_trading_platform/services/committee_service.py",
    "iimc_trading_platform/services/supervisor_service.py",
    "iimc_trading_platform/services/daily_digest_service.py",
    "iimc_trading_platform/services/portfolio_agent_service.py",
    "iimc_trading_platform/sdk.py",
]

_FORBIDDEN_CALLS = {
    "place_order",
    "place_smart_order",
    "submit_order",
    "submit_intent",
    "approve_pending_order",
    "approve",
}


def _names_used(path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Attribute):
            called.add(node.attr)
        elif isinstance(node, ast.Name):
            called.add(node.id)
    return imported, called


class AgentSafetyInvariantTest(unittest.TestCase):
    def test_no_agent_module_imports_a_broker_client(self) -> None:
        for module in _AGENT_MODULES:
            imported, _ = _names_used(Path(module))
            offenders = {
                name
                for name in imported
                if "openalgo" in name.lower() or "OpenAlgoClient" in name
            }
            self.assertFalse(
                offenders, f"{module} must not import a broker client: {offenders}"
            )

    def test_no_agent_module_calls_order_placement(self) -> None:
        for module in _AGENT_MODULES:
            _, called = _names_used(Path(module))
            offenders = called & _FORBIDDEN_CALLS
            self.assertFalse(
                offenders, f"{module} must not call {offenders}"
            )

    def test_sdk_exposes_no_trading_methods(self) -> None:
        from iimc_trading_platform.sdk import ATLClient

        public = [m for m in dir(ATLClient) if not m.startswith("_")]
        for forbidden in ("approve", "order", "submit", "execute", "trade", "buy", "sell"):
            self.assertFalse(
                [m for m in public if forbidden in m.lower()],
                f"SDK must not expose a method containing {forbidden!r}",
            )

    def test_mcp_surface_exposes_no_trading_tools(self) -> None:
        from iimc_trading_platform.mcp_server import _AGENT_TOOL_DEFINITIONS

        names = [t["name"].lower() for t in _AGENT_TOOL_DEFINITIONS]
        for forbidden in ("approve", "order", "submit", "execute", "trade"):
            self.assertFalse(
                [n for n in names if forbidden in n],
                f"MCP must not expose a callable containing {forbidden!r}",
            )

    def test_agent_side_backtests_disable_live_trading(self) -> None:
        """Every backtest an agent path constructs must be research-only."""
        source = Path("iimc_trading_platform/api.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        agent_backtest_calls = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "_ArenaBacktests":
                continue
            agent_backtest_calls += 1
            live = [
                kw for kw in node.keywords if kw.arg == "allow_live_trading"
            ]
            self.assertTrue(
                live, "agent-side backtests must set allow_live_trading"
            )
            self.assertIs(
                getattr(live[0].value, "value", None),
                False,
                "agent-side backtests must set allow_live_trading=False",
            )
        self.assertGreaterEqual(
            agent_backtest_calls, 1, "expected agent-side backtest construction"
        )


if __name__ == "__main__":
    unittest.main()
