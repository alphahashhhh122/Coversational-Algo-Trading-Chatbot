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


if __name__ == "__main__":
    unittest.main()
