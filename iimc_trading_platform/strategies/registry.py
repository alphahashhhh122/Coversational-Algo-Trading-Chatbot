from __future__ import annotations

from pathlib import Path

from .base import StrategyPlugin
from .builtins import (
    EMACrossoverStrategy,
    MomentumStrategy,
    RSIMeanReversionStrategy,
    SMACrossoverStrategy,
)
from .rule_spec import RuleSpecStrategy
from .plugins import discover_local_strategy_plugins


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, StrategyPlugin] = {}
        self._origins: dict[str, str] = {}
        self.plugin_diagnostics: list[dict[str, str]] = []

    def register(self, strategy: StrategyPlugin, *, origin: str = "built_in") -> None:
        if strategy.name in self._strategies:
            raise ValueError(f"Strategy already registered: {strategy.name}")
        self._strategies[strategy.name] = strategy
        self._origins[strategy.name] = origin

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
                "parameter_schema": strategy.parameter_schema,
                "supported_asset_classes": list(strategy.supported_asset_classes),
                "required_candle_fields": list(strategy.required_candle_fields),
                "origin": self._origins[strategy.name],
            }
            for strategy in self._strategies.values()
        ]


def build_strategy_registry(
    plugin_directory: Path | None = None,
) -> StrategyRegistry:
    registry = StrategyRegistry()
    for strategy in [
        EMACrossoverStrategy(),
        SMACrossoverStrategy(),
        RSIMeanReversionStrategy(),
        MomentumStrategy(),
        RuleSpecStrategy(),
    ]:
        registry.register(strategy)
    plugins, diagnostics = discover_local_strategy_plugins(plugin_directory)
    registry.plugin_diagnostics = diagnostics
    for strategy in plugins:
        registry.register(strategy, origin="local_plugin")
    return registry
