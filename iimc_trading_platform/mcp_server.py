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
    ]


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
        except json.JSONDecodeError:
            continue
        response = handle_request(registry, request)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
