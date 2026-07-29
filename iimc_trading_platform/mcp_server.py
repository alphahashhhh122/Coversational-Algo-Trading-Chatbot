"""Minimal MCP stdio server exposing the governed tool registry.

Speaks the Model Context Protocol (JSON-RPC 2.0 over newline-delimited
stdio), so the platform's tools can be attached to any MCP client, for
example Claude Desktop or Claude Code:

    {
      "mcpServers": {
        "iimc-trading": {
          "command": "python",
          "args": ["-m", "iimc_trading_platform.mcp_server"]
        }
      }
    }

Safety: only viewer- and researcher-level tools are exposed. Approval
decisions, paper-order submission, and any live-trading surface remain
behind the platform UI's human-approval workflow and are never callable
through this server.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .config import load_config
from .tools.registry import ToolRegistry, build_default_tool_registry

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "iimc-trading-platform", "version": "1.0.0"}
EXPOSED_ROLE = "researcher"


def build_registry() -> ToolRegistry:
    config = load_config()
    registry = build_default_tool_registry(
        config.database_path,
        allow_live_trading=False,
        openalgo_base_url=config.openalgo_base_url,
        openalgo_api_key=config.openalgo_api_key,
        artifacts_dir=config.artifacts_dir,
        app_config=config,
    )
    return registry.subset(registry.allowed_for_role(EXPOSED_ROLE))


def _tool_definitions(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "name": tool["name"],
            "description": (
                f"{tool['description']} Side effects: {tool['side_effects']}."
            ),
            "inputSchema": tool["input_schema"],
        }
        for tool in registry.list_tools()
    ] + _AGENT_TOOL_DEFINITIONS


# The agent platform is exposed alongside the chat tools so an MCP client can
# browse the roster, run an agent, and read the evidence-linked leaderboard.
# All of these are read-only or research-only — there is no approval or
# order-submission surface here, by design.
_AGENT_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_agents",
        "description": (
            "List the registered agents (research, strategy, monitor) with "
            "their category, version, and run counts. Read-only."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_agent",
        "description": (
            "Run a registered agent by name (e.g. market_researcher, "
            "strategy_validator) and return its findings, evidence, and honest "
            "gaps. Research-only; agents never place orders."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "symbol": {"type": "string"},
                "exchange": {"type": "string", "default": "NSE"},
            },
            "required": ["agent"],
        },
    },
    {
        "name": "get_leaderboard",
        "description": (
            "The agent leaderboard. Strategy agents are ranked on "
            "out-of-sample results only; agents without enough evidence appear "
            "as inconclusive rather than ranked. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
        },
    },
    {
        "name": "get_digest",
        "description": (
            "The latest supervisor digest: what changed, what data is stale, "
            "and what degraded, with each item attributed to the run or "
            "finding behind it. Read-only."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call_agent_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch the agent-platform MCP tools."""

    from .agents.base import AgentTask
    from .agents.roster import build_founding_roster
    from .services.agent_evaluation_service import AgentEvaluationService
    from .services.agent_registry_service import AgentRegistryService

    config = load_config()
    if name == "list_agents":
        return AgentRegistryService(config.database_path).list()
    if name == "get_leaderboard":
        return AgentEvaluationService(config.database_path).leaderboard(
            category=arguments.get("category")
        )
    if name == "get_digest":
        from .services.daily_digest_service import DailyDigestService

        latest = DailyDigestService(config.database_path).latest()
        # No digest yet is a fact, not an error - say so rather than
        # composing one on the fly and calling it "the latest".
        return latest or {
            "digest_id": None,
            "sections": [],
            "note": "No digest has been generated yet.",
        }
    if name == "run_agent":
        registry = build_default_tool_registry(
            config.database_path,
            allow_live_trading=False,
            openalgo_base_url=config.openalgo_base_url,
            openalgo_api_key=config.openalgo_api_key,
            artifacts_dir=config.artifacts_dir,
            app_config=config,
        )
        roster = {a.name: a for a in build_founding_roster(registry)}
        agent = roster.get(str(arguments.get("agent", "")))
        if agent is None:
            raise ValueError(
                f"Unknown agent. Available: {', '.join(sorted(roster))}"
            )
        result = agent.run(
            AgentTask(
                task_type="mcp",
                symbol=arguments.get("symbol"),
                symbols=tuple(
                    [arguments["symbol"]] if arguments.get("symbol") else []
                ),
                exchange=arguments.get("exchange", "NSE"),
            )
        )
        record = AgentRegistryService(config.database_path).record_run(
            agent,
            AgentTask(task_type="mcp", symbol=arguments.get("symbol")),
            result,
        )
        return {
            "run_id": record,
            "status": result.status,
            "findings": result.findings,
            "evidence": result.evidence,
            "gaps": result.gaps,
        }
    raise ValueError(f"Unknown agent tool {name!r}")


def handle_request(
    registry: ToolRegistry,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": _tool_definitions(registry)})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            if name in {t["name"] for t in _AGENT_TOOL_DEFINITIONS}:
                payload = _call_agent_tool(name, arguments)
            else:
                payload = registry.call(name, arguments)
        except Exception as exc:  # surface tool failures as MCP tool errors
            return _result(request_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, default=str, indent=2),
                }
            ],
            "isError": False,
        })
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    registry = build_registry()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            # JSON-RPC 2.0 §5.1: a parse error gets -32700, not silence.
            # Dropping the line left the client waiting for a reply that was
            # never coming, which looks like a hung server rather than a bad
            # request.
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": f"Parse error: {exc.msg}",
                        },
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        response = handle_request(registry, request)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
