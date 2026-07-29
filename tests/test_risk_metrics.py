from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from iimc_trading_platform.services.simulation_service import (
    candle_dates,
    daily_risk_statistics,
    max_drawdown,
    trade_statistics,
)

_EQUITY = 1_000_000.0


def _trades(pnls: list[float], *, every: int = 1) -> list[tuple[datetime, float]]:
    return [
        (datetime(2026, 1, 1) + timedelta(days=i * every), pnl)
        for i, pnl in enumerate(pnls)
    ]


def _calendar(days: int) -> list[datetime]:
    return [datetime(2026, 1, 1) + timedelta(days=i) for i in range(days)]


class SharpeBasisTest(unittest.TestCase):
    """Sharpe must be measured over the days the strategy was live.

    Counting only the days it happened to trade annualises the wrong sample:
    a strategy trading five days in a hundred scored 32.7 where the truth was
    3.3, and the less it traded the better it looked. That number feeds the
    leaderboard.
    """

    def test_flat_days_are_part_of_the_series(self) -> None:
        trades = _trades([1500.0, 800.0, 2100.0, 400.0, 1200.0], every=20)
        with_calendar = daily_risk_statistics(
            trades, starting_equity=_EQUITY, session_dates=_calendar(100)
        )
        self.assertEqual(with_calendar["daily_observations"], 100)
        self.assertLess(with_calendar["sharpe_ratio"], 5)

    def test_ignoring_flat_days_inflates_the_ratio(self) -> None:
        """The old behaviour, kept only so its label can be honest."""
        trades = _trades([1500.0, 800.0, 2100.0, 400.0, 1200.0], every=20)
        sparse = daily_risk_statistics(trades, starting_equity=_EQUITY)
        dense = daily_risk_statistics(
            trades, starting_equity=_EQUITY, session_dates=_calendar(100)
        )
        self.assertGreater(sparse["sharpe_ratio"], dense["sharpe_ratio"] * 5)

    def test_the_basis_says_which_was_used(self) -> None:
        trades = _trades([100.0, -50.0, 75.0])
        self.assertEqual(
            daily_risk_statistics(trades, starting_equity=_EQUITY)[
                "risk_metric_basis"
            ],
            "traded_days_only",
        )
        self.assertEqual(
            daily_risk_statistics(
                trades, starting_equity=_EQUITY, session_dates=_calendar(30)
            )["risk_metric_basis"],
            "daily_realized_returns",
        )

    def test_trading_every_day_gives_the_same_answer_either_way(self) -> None:
        """The correction must not move a strategy that traded daily."""
        pnls = [120.0, -60.0, 300.0, -20.0, 90.0]
        trades = _trades(pnls)
        sparse = daily_risk_statistics(trades, starting_equity=_EQUITY)
        dense = daily_risk_statistics(
            trades, starting_equity=_EQUITY, session_dates=_calendar(len(pnls))
        )
        self.assertAlmostEqual(
            sparse["sharpe_ratio"], dense["sharpe_ratio"], places=6
        )

    def test_two_trades_on_one_day_are_one_observation(self) -> None:
        same_day = [
            (datetime(2026, 1, 1, 10), 100.0),
            (datetime(2026, 1, 1, 15), 50.0),
        ]
        result = daily_risk_statistics(
            same_day, starting_equity=_EQUITY, session_dates=_calendar(5)
        )
        self.assertEqual(result["daily_observations"], 5)


class UncomputableMetricsTest(unittest.TestCase):
    """A ratio with no denominator has no value — and 0.0 is a value."""

    def test_no_trades_reports_unknown_not_zero(self) -> None:
        result = daily_risk_statistics(
            [], starting_equity=_EQUITY, session_dates=_calendar(50)
        )
        self.assertIsNone(result["sharpe_ratio"])
        self.assertIsNone(result["sortino_ratio"])

    def test_a_single_day_has_no_deviation_to_divide_by(self) -> None:
        result = daily_risk_statistics(
            [(datetime(2026, 1, 1), 100.0)],
            starting_equity=_EQUITY,
            session_dates=[datetime(2026, 1, 1)],
        )
        self.assertIsNone(result["sharpe_ratio"])

    def test_no_losing_day_means_no_sortino(self) -> None:
        """Sortino divides by downside deviation; with no downside there is none."""
        result = daily_risk_statistics(
            _trades([100.0, 200.0, 150.0]),
            starting_equity=_EQUITY,
            session_dates=_calendar(3),
        )
        self.assertIsNone(result["sortino_ratio"])
        self.assertIsNotNone(result["sharpe_ratio"])

    def test_zero_starting_equity_is_not_a_division(self) -> None:
        result = daily_risk_statistics(_trades([100.0]), starting_equity=0.0)
        self.assertEqual(result["daily_observations"], 0)
        self.assertIsNone(result["sharpe_ratio"])


class CandleDatesTest(unittest.TestCase):
    def test_reads_dict_and_object_candles(self) -> None:
        class _Candle:
            timestamp = datetime(2026, 1, 2)

        dates = candle_dates(
            [{"timestamp": datetime(2026, 1, 1)}, _Candle()]
        )
        self.assertEqual(len(dates), 2)

    def test_empty_and_none_are_safe(self) -> None:
        self.assertEqual(candle_dates([]), [])
        self.assertEqual(candle_dates(None), [])

    def test_candles_without_timestamps_are_skipped(self) -> None:
        self.assertEqual(candle_dates([{"close": 1.0}]), [])


class TradeStatisticsTest(unittest.TestCase):
    """Hand-checked arithmetic, so a refactor cannot quietly change meaning."""

    def test_win_rate_profit_factor_and_expectancy(self) -> None:
        stats = trade_statistics(
            [100.0, -50.0, 200.0, -25.0],
            starting_equity=_EQUITY,
            max_drawdown=50.0,
        )
        self.assertEqual(stats["win_rate_pct"], 50.0)
        # 300 gross profit / 75 gross loss
        self.assertEqual(stats["profit_factor"], 4.0)
        self.assertEqual(stats["average_win"], 150.0)
        self.assertEqual(stats["average_loss"], -37.5)
        self.assertEqual(stats["expectancy"], 56.25)

    def test_no_trades_is_all_zeroes_not_a_crash(self) -> None:
        stats = trade_statistics([], starting_equity=_EQUITY, max_drawdown=0.0)
        self.assertEqual(stats["win_rate_pct"], 0.0)

    def test_max_drawdown_is_peak_to_trough(self) -> None:
        self.assertEqual(max_drawdown([100.0, 150.0, 90.0, 120.0]), 60.0)
        self.assertEqual(max_drawdown([100.0, 110.0, 120.0]), 0.0)
        self.assertEqual(max_drawdown([]), 0.0)


if __name__ == "__main__":
    unittest.main()
