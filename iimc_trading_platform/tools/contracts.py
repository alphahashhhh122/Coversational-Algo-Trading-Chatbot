"""What a tool *is*: the input base, the definition, and the registry.

Separated from the catalogue so the two can be read independently, and so
modules that declare tools can import these types without importing the
factory that builds them — which would be a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

ToolHandler = Callable[[ToolInput], dict[str, Any]]

@dataclass(frozen=True)
class ToolCapabilityMetadata:
    actions: tuple[str, ...] = ()
    asset_classes: tuple[str, ...] = ()
    execution_modes: tuple[str, ...] = ()
    required_data: tuple[str, ...] = ()
    required_providers: tuple[str, ...] = ()
    requires_approval: bool = False
    risk_level: Literal["low", "medium", "high"] = "low"

    def as_dict(self) -> dict[str, Any]:
        return {
            "actions": list(self.actions),
            "asset_classes": list(self.asset_classes),
            "execution_modes": list(self.execution_modes),
            "required_data": list(self.required_data),
            "required_providers": list(self.required_providers),
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
        }

    def summary(self) -> str:
        parts = [f"risk={self.risk_level}"]
        if self.actions:
            parts.append(f"actions={','.join(self.actions)}")
        if self.asset_classes:
            parts.append(f"assets={','.join(self.asset_classes)}")
        if self.execution_modes:
            parts.append(f"modes={','.join(self.execution_modes)}")
        if self.required_data:
            parts.append(f"data={','.join(self.required_data)}")
        if self.required_providers:
            parts.append(f"providers={','.join(self.required_providers)}")
        if self.requires_approval:
            parts.append("approval=required")
        return "; ".join(parts)

@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: ToolHandler
    side_effects: str
    retry_safe: bool
    required_role: str = "viewer"
    capabilities: ToolCapabilityMetadata = ToolCapabilityMetadata()

    def validate(self, payload: dict[str, Any] | None) -> ToolInput:
        if payload is None:
            payload = {}
        return self.input_model.model_validate(payload)

    @property
    def is_read_only(self) -> bool:
        return self.side_effects == "none" or self.side_effects.startswith(
            "read-only"
        )

    def openai_schema(self, *, strict: bool = True) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": (
                f"{self.description} Side effects: {self.side_effects}. "
                f"Retry safe: {str(self.retry_safe).lower()}. "
                f"Required role: {self.required_role}. "
                f"Capabilities: {self.capabilities.summary()}."
            ),
            "parameters": (
                _strict_schema(self.input_model.model_json_schema())
                if strict
                else _provider_compatible_schema(
                    self.input_model.model_json_schema()
                )
            ),
            "strict": strict,
        }

class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]):
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool: {tool.name}")
            self._tools[tool.name] = tool

    def get(self, tool_name: str) -> ToolDefinition:
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise ValueError(f"Unknown tool: {tool_name}") from exc

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": _strict_schema(
                    tool.input_model.model_json_schema()
                ),
                "side_effects": tool.side_effects,
                "retry_safe": tool.retry_safe,
                "required_role": tool.required_role,
                "capabilities": tool.capabilities.as_dict(),
            }
            for tool in self._tools.values()
        ]

    def openai_tools(self, *, strict: bool = True) -> list[dict[str, Any]]:
        return [
            tool.openai_schema(strict=strict)
            for tool in self._tools.values()
        ]

    def subset(self, allowed_names: set[str]) -> ToolRegistry:
        return ToolRegistry(
            [
                tool
                for name, tool in self._tools.items()
                if name in allowed_names
            ]
        )

    def allowed_for_role(self, role: str) -> set[str]:
        rank = {
            "viewer": 1,
            "researcher": 2,
            "approver": 3,
            "admin": 4,
        }
        active_rank = rank.get(role, 0)
        return {
            name
            for name, tool in self._tools.items()
            if active_rank >= rank[tool.required_role]
        }

    def call(self, tool_name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        tool = self.get(tool_name)
        validated = tool.validate(payload)
        return tool.handler(validated)

def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Pydantic schema for OpenAI strict function tools."""
    normalized = {
        key: _strict_schema(value) if isinstance(value, dict) else (
            [
                _strict_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
            if isinstance(value, list)
            else value
        )
        for key, value in schema.items()
        if key != "default"
    }
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized

def _provider_compatible_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep Pydantic's optional-field semantics for non-strict providers."""
    return {
        key: _provider_compatible_schema(value)
        if isinstance(value, dict)
        else [
            _provider_compatible_schema(item)
            if isinstance(item, dict)
            else item
            for item in value
        ]
        if isinstance(value, list)
        else value
        for key, value in schema.items()
    }


def _require_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        raise ValueError("Run not found")
    return result
