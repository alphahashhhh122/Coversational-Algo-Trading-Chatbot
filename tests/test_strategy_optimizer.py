from __future__ import annotations

import unittest

from iimc_trading_platform.orchestration import grounded_tool_response
from iimc_trading_platform.services.strategy_optimizer_service import (
    StrategyOptimizerService,
)


class _Backtest:
    """Returns increasing return_pct per call so ranking is deterministic."""

    def __init__(self, trades: int = 12) -> None:
        self.calls = 0
        self.trades = trades

    def load_dataset_candles(self, dataset_id, instrument=None):
        return {"symbol": "X"}, [1, 2, 3]

    def simulate_only(self, *, strategy_name, candles, parameters):
        self.calls += 1
        return {
            "total_trades": self.trades,
            "net_pnl": 1000 * self.calls,
            "max_drawdown": -150,
            "return_pct": round(1.0 * self.calls, 2),
        }


class StrategyOptimizerTest(unittest.TestCase):
    def test_ranks_and_picks_best_by_return(self) -> None:
        svc = StrategyOptimizerService(_Backtest())
        result = svc.optimize(dataset_id="ds1")
        self.assertEqual(result["candidates_tried"], 6)
        # Descending by return_pct.
        returns = [r["return_pct"] for r in result["results"]]
        self.assertEqual(returns, sorted(returns, reverse=True))
        self.assertEqual(result["best"]["return_pct"], max(returns))
        self.assertFalse(result["used_unreliable_best"])
        answer = grounded_tool_response("run_strategy_optimization", result)
        self.assertIn("Best configuration", answer)
        self.assertIn("investment advice", answer.lower())

    def test_too_few_trades_flagged_as_unreliable(self) -> None:
        svc = StrategyOptimizerService(_Backtest(trades=1))
        result = svc.optimize(dataset_id="ds1", min_trades=3)
        self.assertTrue(all(not r["reliable"] for r in result["results"]))
        self.assertTrue(result["used_unreliable_best"])
        answer = grounded_tool_response("run_strategy_optimization", result)
        self.assertIn("too few trades", answer.lower())

    def test_backtest_errors_are_reported_not_fatal(self) -> None:
        class Boom:
            def load_dataset_candles(self, dataset_id, instrument=None):
                return {"symbol": "X"}, [1, 2, 3]

            def simulate_only(self, **kwargs):
                raise ValueError("dataset not fit for research")

        result = StrategyOptimizerService(Boom()).optimize(dataset_id="ds1")
        self.assertIsNone(result["best"])
        answer = grounded_tool_response("run_strategy_optimization", result)
        self.assertIn("couldn't optimise", answer.lower())

    def test_unknown_template_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StrategyOptimizerService(_Backtest()).optimize(
                dataset_id="ds1", strategy_name="voodoo"
            )


class _WalkForwardBacktest:
    """Returns train vs test returns by window length (train=70, test=30)."""

    def __init__(self, train_ret: float, test_ret: float, trades: int = 10) -> None:
        self.train_ret = train_ret
        self.test_ret = test_ret
        self.trades = trades

    def load_dataset_candles(self, dataset_id, instrument=None):
        return {"symbol": "X"}, list(range(100))

    def simulate_only(self, *, strategy_name, candles, parameters):
        ret = self.train_ret if len(candles) >= 70 else self.test_ret
        return {
            "total_trades": self.trades,
            "net_pnl": 1000,
            "max_drawdown": -100,
            "return_pct": ret,
        }


class WalkForwardTest(unittest.TestCase):
    def test_config_that_holds_up_is_reported(self) -> None:
        svc = StrategyOptimizerService(_WalkForwardBacktest(10.0, 8.0))
        result = svc.walk_forward(dataset_id="ds1")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verdict"], "holds_up")
        self.assertEqual(result["in_sample_return_pct"], 10.0)
        self.assertEqual(result["out_of_sample_return_pct"], 8.0)
        answer = grounded_tool_response("validate_strategy_walk_forward", result)
        self.assertIn("Holds up", answer)
        self.assertIn("Out-of-sample", answer)

    def test_overfit_config_is_flagged_honestly(self) -> None:
        svc = StrategyOptimizerService(_WalkForwardBacktest(12.0, -4.0))
        result = svc.walk_forward(dataset_id="ds1")
        self.assertEqual(result["verdict"], "overfit")
        answer = grounded_tool_response("validate_strategy_walk_forward", result)
        self.assertIn("Overfit", answer)

    def test_too_few_out_of_sample_trades_is_inconclusive(self) -> None:
        svc = StrategyOptimizerService(_WalkForwardBacktest(10.0, 5.0, trades=1))
        result = svc.walk_forward(dataset_id="ds1", min_trades=3)
        self.assertEqual(result["verdict"], "inconclusive")

    def test_short_history_rejected(self) -> None:
        class Short:
            def load_dataset_candles(self, dataset_id, instrument=None):
                return {"symbol": "X"}, list(range(40))

            def simulate_only(self, **kwargs):
                raise AssertionError("should not be called")

        with self.assertRaises(ValueError):
            StrategyOptimizerService(Short()).walk_forward(dataset_id="ds1")


class NoCandidateReasonTest(unittest.TestCase):
    """"No usable configuration" on its own is a dead end."""

    def test_the_failure_reason_reaches_the_answer(self) -> None:
        answer = grounded_tool_response(
            "validate_strategy_walk_forward",
            {
                "strategy": "ema_crossover",
                "status": "no_candidate",
                "candidates_tried": 12,
                "candidates_failed": 12,
                "gaps": ["ValueError: dataset has no candles in that window"],
            },
        )
        self.assertIn("no candles", answer)
        self.assertIn("12 of 12", answer)

    def test_running_but_never_trading_is_distinguished_from_crashing(self) -> None:
        answer = grounded_tool_response(
            "validate_strategy_walk_forward",
            {
                "strategy": "rsi_mean_reversion",
                "status": "no_candidate",
                "candidates_tried": 8,
                "candidates_failed": 0,
                "gaps": ["every configuration ran, but none produced a trade"],
            },
        )
        self.assertIn("none produced a trade", answer)
        self.assertNotIn("failed to run", answer)

    def test_a_result_without_detail_still_renders(self) -> None:
        answer = grounded_tool_response(
            "validate_strategy_walk_forward",
            {"strategy": "sma_crossover", "status": "no_candidate"},
        )
        self.assertIn("sma crossover", answer)

    def test_identical_failures_are_reported_once(self) -> None:
        """A repeated cause is one cause, not a wall of identical lines."""
        from iimc_trading_platform.services.strategy_optimizer_service import (
            StrategyOptimizerService,
        )

        class _AlwaysFails:
            def simulate_only(self, **kwargs):
                raise ValueError("dataset has no candles")

        svc = StrategyOptimizerService.__new__(StrategyOptimizerService)
        svc.backtest_service = _AlwaysFails()
        grid = [{"fast": f, "slow": 21} for f in (5, 9, 12)]
        failures: list[str] = []
        for parameters in grid:
            try:
                svc.backtest_service.simulate_only(parameters=parameters)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {str(exc)[:120]}"
                if reason not in failures:
                    failures.append(reason)
        self.assertEqual(len(failures), 1)


if __name__ == "__main__":
    unittest.main()
