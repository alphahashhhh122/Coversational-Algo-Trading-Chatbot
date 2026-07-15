from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any, Iterable

from .base import StrategyPlugin


def discover_local_strategy_plugins(
    directory: Path | None,
) -> tuple[list[StrategyPlugin], list[dict[str, str]]]:
    """Load explicitly trusted, local strategy modules without affecting startup."""
    if directory is None or not directory.exists():
        return [], []
    if not directory.is_dir():
        return [], [{"path": str(directory), "status": "ignored", "message": "Not a directory"}]

    strategies: list[StrategyPlugin] = []
    diagnostics: list[dict[str, str]] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = _load_module(path)
            loaded = _module_strategies(module)
            for strategy in loaded:
                if not isinstance(strategy, StrategyPlugin):
                    raise TypeError(
                        "Plugins must return StrategyPlugin instances through "
                        "build_strategy(), build_strategies(), or STRATEGIES."
                    )
                strategies.append(strategy)
            diagnostics.append(
                {
                    "path": str(path),
                    "status": "loaded",
                    "message": f"Loaded {len(loaded)} strategy plugin(s)",
                }
            )
        except Exception as exc:
            diagnostics.append(
                {
                    "path": str(path),
                    "status": "failed",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
    return strategies, diagnostics


def _load_module(path: Path) -> Any:
    module_name = "iimc_local_strategy_" + hashlib.sha256(
        str(path.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Could not create a module loader")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_strategies(module: Any) -> list[StrategyPlugin]:
    if callable(getattr(module, "build_strategies", None)):
        values = module.build_strategies()
    elif callable(getattr(module, "build_strategy", None)):
        values = [module.build_strategy()]
    elif hasattr(module, "STRATEGIES"):
        values = module.STRATEGIES
    else:
        raise ValueError(
            "Define build_strategy(), build_strategies(), or STRATEGIES."
        )
    if isinstance(values, StrategyPlugin):
        return [values]
    if not isinstance(values, Iterable):
        raise TypeError("Plugin factory must return a strategy or iterable of strategies")
    return list(values)
