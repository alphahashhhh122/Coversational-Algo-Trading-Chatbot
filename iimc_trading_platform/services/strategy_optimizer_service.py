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


def _walk_forward_verdict(
    in_ret: float | None,
    out_ret: float | None,
    out_trades: int,
    min_trades: int,
) -> str:
    """A plain, honest label for how the config held up out-of-sample."""

    if out_ret is None or out_trades < min_trades:
        return "inconclusive"  # too few out-of-sample trades to judge
    if in_ret is not None and in_ret > 0 and out_ret <= 0:
        return "overfit"  # profitable in-sample, lost money out-of-sample
    if in_ret is not None and in_ret > 0 and out_ret >= in_ret * 0.5:
        return "holds_up"  # kept most of its edge out-of-sample
    if out_ret > 0:
        return "weaker_but_positive"
    return "poor"


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

    def walk_forward_spec(
        self,
        *,
        dataset_id: str,
        spec: dict[str, Any],
        split_ratio: float = 0.7,
        min_trades: int = 3,
    ) -> dict[str, Any]:
        """Walk-forward a *fixed* rule spec (an authored strategy).

        Unlike :meth:`walk_forward` there is no grid to fit — the spec is given.
        The train window therefore measures how the author's rules did on older
        data and the test window is still untouched, so a hand-tuned spec that
        only worked on the data its author eyeballed is still caught. The result
        deliberately matches :meth:`walk_forward`'s shape so authored agents are
        scored by exactly the same rules as built-in ones.
        """

        if not 0.5 <= split_ratio <= 0.85:
            raise ValueError("split_ratio must be between 0.5 and 0.85")
        _dataset, candles = self.backtest_service.load_dataset_candles(dataset_id)
        if len(candles) < 60:
            raise ValueError(
                "I need more history for a reliable train/test split "
                "(at least 60 bars)."
            )
        split = int(len(candles) * split_ratio)
        train, test = candles[:split], candles[split:]
        parameters = {"spec": spec}
        train_run = self.backtest_service.simulate_only(
            strategy_name="rule_spec", candles=train, parameters=parameters
        )
        test_run = self.backtest_service.simulate_only(
            strategy_name="rule_spec", candles=test, parameters=parameters
        )
        in_ret = train_run.get("return_pct")
        out_ret = test_run.get("return_pct")
        out_trades = int(test_run.get("total_trades") or 0)
        return {
            "strategy": "rule_spec",
            "dataset_id": dataset_id,
            "status": "ok",
            "split_ratio": split_ratio,
            "train_bars": len(train),
            "test_bars": len(test),
            "parameters": {"authored": True},
            "in_sample_return_pct": in_ret,
            "in_sample_trades": int(train_run.get("total_trades") or 0),
            "out_of_sample_return_pct": out_ret,
            "out_of_sample_trades": out_trades,
            "out_of_sample_drawdown": test_run.get("max_drawdown"),
            # Risk and benchmark figures the scorer needs to rank honestly.
            "out_of_sample_excess_return_pct": test_run.get("excess_return_pct"),
            "out_of_sample_benchmark_pct": test_run.get("buy_and_hold_return_pct"),
            "out_of_sample_sharpe": test_run.get("sharpe_ratio"),
            "out_of_sample_drawdown_pct": test_run.get("max_drawdown_pct"),
            "out_of_sample_win_rate_pct": test_run.get("win_rate_pct"),
            "verdict": _walk_forward_verdict(in_ret, out_ret, out_trades, min_trades),
            "no_synthetic_fallback": True,
        }

    def walk_forward(
        self,
        *,
        dataset_id: str,
        strategy_name: str = "ema_crossover",
        instrument: dict[str, Any] | None = None,
        split_ratio: float = 0.7,
        min_trades: int = 3,
    ) -> dict[str, Any]:
        """Out-of-sample check: optimise on older data, then test on newer data.

        Splits the stored history into an in-sample (train) and an out-of-sample
        (test) window, picks the best grid config on the train window, then
        evaluates that *same* config on the untouched test window. The gap
        between the two is the honest signal: a config that wins in-sample but
        loses out-of-sample is overfit, and this reports exactly that rather than
        celebrating the in-sample number.
        """

        grid = _GRIDS.get(strategy_name)
        if grid is None:
            raise ValueError(
                f"I can only validate {sorted(_GRIDS)} right now, not "
                f"{strategy_name!r}."
            )
        if not 0.5 <= split_ratio <= 0.85:
            raise ValueError("split_ratio must be between 0.5 and 0.85")
        _dataset, candles = self.backtest_service.load_dataset_candles(
            dataset_id, instrument=instrument
        )
        if len(candles) < 60:
            raise ValueError(
                "I need more history for a reliable train/test split "
                "(at least 60 bars)."
            )
        split = int(len(candles) * split_ratio)
        train, test = candles[:split], candles[split:]

        # Optimise on the train window only.
        train_scored: list[dict[str, Any]] = []
        for parameters in grid:
            try:
                run = self.backtest_service.simulate_only(
                    strategy_name=strategy_name, candles=train, parameters=parameters
                )
            except Exception:  # noqa: BLE001 - a bad candidate is skipped, not fatal
                continue
            train_scored.append(
                {
                    "parameters": parameters,
                    "return_pct": run.get("return_pct"),
                    "total_trades": int(run.get("total_trades") or 0),
                }
            )
        in_sample_pool = [
            r for r in train_scored if r["total_trades"] >= min_trades
        ] or train_scored
        best = (
            max(in_sample_pool, key=lambda r: r["return_pct"])
            if in_sample_pool
            else None
        )
        if best is None:
            return {
                "strategy": strategy_name,
                "dataset_id": dataset_id,
                "status": "no_candidate",
                "no_synthetic_fallback": True,
            }

        # Evaluate the winner on the untouched test window.
        test_run = self.backtest_service.simulate_only(
            strategy_name=strategy_name, candles=test, parameters=best["parameters"]
        )
        in_ret = best["return_pct"]
        out_ret = test_run.get("return_pct")
        out_trades = int(test_run.get("total_trades") or 0)
        verdict = _walk_forward_verdict(in_ret, out_ret, out_trades, min_trades)
        return {
            "strategy": strategy_name,
            "dataset_id": dataset_id,
            "status": "ok",
            "split_ratio": split_ratio,
            "train_bars": len(train),
            "test_bars": len(test),
            "parameters": best["parameters"],
            "in_sample_return_pct": in_ret,
            "in_sample_trades": best["total_trades"],
            "out_of_sample_return_pct": out_ret,
            "out_of_sample_trades": out_trades,
            "out_of_sample_drawdown": test_run.get("max_drawdown"),
            # Risk and benchmark figures the scorer needs to rank honestly.
            "out_of_sample_excess_return_pct": test_run.get("excess_return_pct"),
            "out_of_sample_benchmark_pct": test_run.get("buy_and_hold_return_pct"),
            "out_of_sample_sharpe": test_run.get("sharpe_ratio"),
            "out_of_sample_drawdown_pct": test_run.get("max_drawdown_pct"),
            "out_of_sample_win_rate_pct": test_run.get("win_rate_pct"),
            "verdict": verdict,
            "no_synthetic_fallback": True,
        }
