from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.mcp_server import handle_request
from iimc_trading_platform.tools.registry import build_default_tool_registry

from _harness import AppHarness


class McpHttpEndpointsTest(unittest.TestCase):
    # App built once per class; database reset between tests. See _harness.py.
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = AppHarness()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness.close()

    def setUp(self) -> None:
        self.db_path = self.harness.db_path
        self.temp_dir = self.harness.temp_dir
        self.client = self.harness.client
        self.harness.reset()

    def test_mcp_tools_lists_mcp_shaped_definitions(self) -> None:
        response = self.client.get("/mcp/tools")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["server_name"], "iimc-trading-platform")
        self.assertGreater(len(payload["tools"]), 10)
        first = payload["tools"][0]
        self.assertIn("name", first)
        self.assertIn("description", first)
        self.assertIn("inputSchema", first)
        self.assertEqual(first["inputSchema"]["type"], "object")

    def test_mcp_call_executes_a_read_only_tool(self) -> None:
        response = self.client.post(
            "/mcp/call",
            json={"name": "list_datasets", "arguments": {}},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["isError"])
        self.assertEqual(payload["content"][0]["type"], "text")
        self.assertIn("datasets", payload["structuredContent"])

    def test_mcp_call_rejects_unknown_tool(self) -> None:
        response = self.client.post(
            "/mcp/call",
            json={"name": "not_a_real_tool", "arguments": {}},
        )

        self.assertEqual(response.status_code, 403)

    def test_mcp_call_wraps_tool_failures_as_mcp_errors(self) -> None:
        response = self.client.post(
            "/mcp/call",
            json={
                "name": "get_backtest_result",
                "arguments": {"run_id": "run_missing"},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["isError"])
        self.assertEqual(payload["content"][0]["type"], "text")


class McpStdioServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.duckdb"
        initialize_database(self.db_path)
        registry = build_default_tool_registry(self.db_path)
        self.registry = registry.subset(
            registry.allowed_for_role("researcher")
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialize_handshake(self) -> None:
        response = handle_request(
            self.registry,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )

        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertIn("protocolVersion", result)
        self.assertEqual(
            result["serverInfo"]["name"], "iimc-trading-platform",
        )

    def test_initialized_notification_has_no_response(self) -> None:
        response = handle_request(
            self.registry,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        self.assertIsNone(response)

    def test_tools_list_and_call(self) -> None:
        listing = handle_request(
            self.registry,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        tool_names = {
            tool["name"] for tool in listing["result"]["tools"]
        }
        self.assertIn("list_datasets", tool_names)
        self.assertIn("list_strategies", tool_names)

        called = handle_request(
            self.registry,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_datasets", "arguments": {}},
            },
        )
        result = called["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")

    def test_approver_only_tools_are_not_exposed(self) -> None:
        listing = handle_request(
            self.registry,
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        )
        tool_names = {
            tool["name"] for tool in listing["result"]["tools"]
        }
        full_registry = build_default_tool_registry(self.db_path)
        approver_only = {
            tool["name"]
            for tool in full_registry.list_tools()
            if tool["required_role"] in {"approver", "admin"}
        }
        self.assertFalse(tool_names & approver_only)

    def test_unknown_method_returns_json_rpc_error(self) -> None:
        response = handle_request(
            self.registry,
            {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
        )

        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()


class McpAgentToolsTest(unittest.TestCase):
    """The agent platform is reachable from any MCP client."""

    def test_agent_tools_are_listed(self) -> None:
        from iimc_trading_platform.mcp_server import _AGENT_TOOL_DEFINITIONS

        names = {t["name"] for t in _AGENT_TOOL_DEFINITIONS}
        self.assertEqual(
            names,
            {"list_agents", "run_agent", "get_leaderboard", "get_digest"},
        )
        for definition in _AGENT_TOOL_DEFINITIONS:
            self.assertIn("description", definition)
            self.assertEqual(definition["inputSchema"]["type"], "object")

    def test_agent_tools_expose_no_order_surface(self) -> None:
        """Safety: no callable on the MCP surface can approve or place orders.

        Asserted on tool *names* (what a client can actually invoke) rather
        than descriptions, so prose promising "agents never place orders"
        doesn't trip the check.
        """
        from iimc_trading_platform.mcp_server import _AGENT_TOOL_DEFINITIONS

        names = [t["name"].lower() for t in _AGENT_TOOL_DEFINITIONS]
        for forbidden in ("approve", "order", "submit", "execute", "trade"):
            self.assertFalse(
                [n for n in names if forbidden in n],
                f"MCP must not expose a callable containing {forbidden!r}",
            )

    def test_unknown_agent_is_rejected_with_guidance(self) -> None:
        from iimc_trading_platform.mcp_server import _call_agent_tool

        with self.assertRaises(ValueError) as ctx:
            _call_agent_tool("run_agent", {"agent": "not_a_real_agent"})
        self.assertIn("Available:", str(ctx.exception))
