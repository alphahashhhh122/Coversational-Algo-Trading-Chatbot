from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain import SignalDirection


@dataclass(frozen=True)
class RawSignal:
    timestamp: datetime
    symbol: str
    signal_type: str
    direction: SignalDirection
    price: float
    confidence: float
    reason: str
    features: dict[str, Any]


class StrategyPlugin(ABC):
    name: str
    version: str
    description: str
    parameter_schema: dict[str, dict[str, Any]]

    def validate_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        unknown = set(parameters) - set(self.parameter_schema)
        if unknown:
            raise ValueError(
                f"Unknown parameters for {self.name}: {sorted(unknown)}"
            )

        validated: dict[str, Any] = {}
        for name, spec in self.parameter_schema.items():
            value = parameters.get(name, spec.get("default"))
            if value is None:
                raise ValueError(f"Missing required parameter: {name}")

            expected_type = spec["type"]
            if expected_type == "integer":
                if isinstance(value, bool):
                    raise ValueError(f"{name} must be an integer")
                value = int(value)
            elif expected_type == "number":
                if isinstance(value, bool):
                    raise ValueError(f"{name} must be numeric")
                value = float(value)
            else:
                raise ValueError(f"Unsupported parameter type: {expected_type}")

            if "minimum" in spec and value < spec["minimum"]:
                raise ValueError(f"{name} must be >= {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                raise ValueError(f"{name} must be <= {spec['maximum']}")
            validated[name] = value

        self.validate_cross_parameters(validated)
        return validated

    def validate_cross_parameters(self, parameters: dict[str, Any]) -> None:
        return None

    @abstractmethod
    def generate(
        self,
        candles: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> list[RawSignal]:
        """Generate signals without database or network side effects."""

