"""A strategy-discovery agent.

Given a dataset and a template, it backtests a small parameter grid, ranks the
runs by historical return (guarding against too-few-trade overfits), and reports
the best configuration with the full leaderboard. It only ever runs research
backtests over stored data — it never trades — and reports real metrics without
fabrication.
"""

from __future__ import annotations

from typing import Any

from .backtest_service import BacktestService

# Small, sensible grids per template. Kept short so a run stays interactive.
_GRIDS: dict[str, list[dict[str, Any]]] = {
    "ema_crossover": [
        {"fast_period": 9, "slow_period": 21, "stop_loss_pct": 0.02},
        {"fast_period": 5, "slow_period": 20, "stop_loss_pct": 0.02},
        {"fast_period": 12, "slow_period": 26, "stop_loss_pct": 0.02},
        {"fast_period": 9, "slow_period": 21, "stop_loss_pct": 0.03},
        {"fast_period": 7, "slow_period": 25, "stop_loss_pct": 0.015},
        {"fast_period": 10, "slow_period": 30, "stop_loss_pct": 0.025},
    ],
    "sma_crossover": [
        {"fast_period": 10, "slow_period": 30, "stop_loss_pct": 0.02},
        {"fast_period": 20, "slow_period": 50, "stop_loss_pct": 0.02},
        {"fast_period": 5, "slow_period": 20, "stop_loss_pct": 0.02},
        {"fast_period": 10, "slow_period": 40, "stop_loss_pct": 0.03},
        {"fast_period": 15, "slow_period": 45, "stop_loss_pct": 0.02},
    ],
}


class StrategyOptimizerService:
    def __init__(self, backtest_service: BacktestService) -> None:
        self.backtest_service = backtest_service

    @staticmethod
    def supports(strategy_name: str) -> bool:
        return strategy_name in _GRIDS

    def optimize(
        self,
        *,
        dataset_id: str,
        strategy_name: str = "ema_crossover",
        instrument: dict[str, Any] | None = None,
        max_candidates: int = 6,
        min_trades: int = 3,
    ) -> dict[str, Any]:
        grid = _GRIDS.get(strategy_name)
        if grid is None:
            raise ValueError(
                f"I can only optimise {sorted(_GRIDS)} right now, not "
                f"{strategy_name!r}."
            )
        # Load candles once, then run fast in-memory (no-persistence) backtests
        # for each candidate so the whole search stays interactive.
        _dataset, candles = self.backtest_service.load_dataset_candles(
            dataset_id, instrument=instrument
        )
        results: list[dict[str, Any]] = []
        for parameters in grid[:max_candidates]:
            try:
                run = self.backtest_service.simulate_only(
                    strategy_name=strategy_name,
                    candles=candles,
                    parameters=parameters,
                )
            except Exception as exc:  # noqa: BLE001 - reported, not fabricated
                results.append(
                    {"parameters": parameters, "error": str(exc)[:140]}
                )
                continue
            trades = int(run.get("total_trades") or 0)
            results.append(
                {
                    "parameters": parameters,
                    "return_pct": run.get("return_pct"),
                    "net_pnl": run.get("net_pnl"),
                    "max_drawdown": run.get("max_drawdown"),
                    "total_trades": trades,
                    "reliable": trades >= min_trades,
                }
            )
        scored = [r for r in results if r.get("return_pct") is not None]
        reliable = [r for r in scored if r.get("reliable")]
        pool = reliable or scored
        best = (
            max(pool, key=lambda r: r["return_pct"]) if pool else None
        )
        return {
            "strategy": strategy_name,
            "dataset_id": dataset_id,
            "candidates_tried": len(results),
            "results": sorted(
                scored,
                key=lambda r: r["return_pct"],
                reverse=True,
            )
            + [r for r in results if "return_pct" not in r],
            "best": best,
            "used_unreliable_best": bool(best) and not best.get("reliable"),
            "no_synthetic_fallback": True,
        }
