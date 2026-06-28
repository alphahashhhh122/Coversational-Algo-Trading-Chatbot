from __future__ import annotations

from .base import StrategyPlugin
from .builtins import (
    EMACrossoverStrategy,
    MomentumStrategy,
    RSIMeanReversionStrategy,
    SMACrossoverStrategy,
)


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, StrategyPlugin] = {}

    def register(self, strategy: StrategyPlugin) -> None:
        if strategy.name in self._strategies:
            raise ValueError(f"Strategy already registered: {strategy.name}")
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> StrategyPlugin:
        try:
            return self._strategies[name]
        except KeyError as exc:
            raise ValueError(
                f"Unknown strategy {name!r}. "
                f"Available: {sorted(self._strategies)}"
            ) from exc

    def list(self) -> list[dict]:
        return [
            {
                "strategy_id": strategy.name,
                "name": strategy.name,
                "version": strategy.version,
                "description": strategy.description,
                "parameters": strategy.parameter_schema,
            }
            for strategy in self._strategies.values()
        ]


def build_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    for strategy in [
        EMACrossoverStrategy(),
        SMACrossoverStrategy(),
        RSIMeanReversionStrategy(),
        MomentumStrategy(),
    ]:
        registry.register(strategy)
    return registry

