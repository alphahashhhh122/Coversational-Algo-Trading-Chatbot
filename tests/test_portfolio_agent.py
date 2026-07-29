from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.arena_service import ArenaService
from iimc_trading_platform.services.portfolio_agent_service import (
    _pearson,
    _weights,
    PortfolioAgentService,
)


def _series(pattern: list[float], start_price: float = 100.0) -> list[dict]:
    """Candles whose closes follow ``pattern`` as per-bar returns."""
    base = datetime(2026, 1, 1)
    candles = [{"timestamp": base, "close": start_price}]
    price = start_price
    for i, ret in enumerate(pattern, start=1):
        price = price * (1 + ret)
        candles.append(
            {"timestamp": base + timedelta(minutes=5 * i), "close": price}
        )
    return candles


class _Backtest:
    def __init__(self, by_dataset: dict[str, list[dict]]) -> None:
        self.by_dataset = by_dataset

    def load_dataset_candles(self, dataset_id, instrument=None):
        if dataset_id not in self.by_dataset:
            raise ValueError(f"unknown dataset {dataset_id}")
        return {"symbol": dataset_id}, self.by_dataset[dataset_id]

    def simulate_only(self, *, strategy_name, candles, parameters,
                      starting_equity=1_000_000.0):
        return {
            "total_trades": 10,
            "net_pnl": 100.0,
            "max_drawdown": -50.0,
            # Deterministic and distinct per series, so leg attribution is
            # checkable.
            "return_pct": len(candles) / 1000,
        }


class PortfolioAnalysisTest(unittest.TestCase):
    def _service(self, series: dict[str, list[dict]]) -> PortfolioAgentService:
        return PortfolioAgentService(
            Path("unused.duckdb"),
            _Backtest(series),
            lambda symbol, exchange: symbol if symbol in series else None,
        )

    def test_identical_movers_are_perfectly_correlated(self) -> None:
        moves = [0.01, -0.02, 0.03, -0.01] * 10
        svc = self._service({"A": _series(moves), "B": _series(moves)})
        result = svc.analyse(["A", "B"])
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(
            result["correlations"][0]["correlation"], 1.0, places=4
        )
        self.assertIn("almost together", result["diversification"])

    def test_opposite_movers_are_negatively_correlated(self) -> None:
        moves = [0.01, -0.02, 0.03, -0.01] * 10
        svc = self._service(
            {"A": _series(moves), "B": _series([-m for m in moves])}
        )
        result = svc.analyse(["A", "B"])
        self.assertLess(result["correlations"][0]["correlation"], -0.9)
        self.assertIn("negative", result["correlations"][0]["reading"])

    def test_calmer_symbol_gets_more_weight_under_inverse_volatility(self) -> None:
        svc = self._service({
            "CALM": _series([0.001, -0.001] * 25),
            "WILD": _series([0.05, -0.05] * 25),
        })
        result = svc.analyse(["CALM", "WILD"], scheme="inverse_volatility")
        self.assertGreater(result["weights"]["CALM"], result["weights"]["WILD"])
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0, places=4)

    def test_equal_weight_scheme_ignores_volatility(self) -> None:
        svc = self._service({
            "CALM": _series([0.001, -0.001] * 25),
            "WILD": _series([0.05, -0.05] * 25),
        })
        result = svc.analyse(["CALM", "WILD"], scheme="equal_weight")
        self.assertAlmostEqual(result["weights"]["CALM"], 0.5, places=4)

    def test_symbol_without_data_is_reported_not_invented(self) -> None:
        moves = [0.01, -0.01] * 25
        svc = self._service({"A": _series(moves), "B": _series(moves)})
        result = svc.analyse(["A", "B", "MISSING"])
        self.assertEqual(result["usable_symbols"], ["A", "B"])
        self.assertTrue(any("MISSING" in g for g in result["gaps"]))

    def test_too_little_overlap_refuses_to_correlate(self) -> None:
        short = [0.01, -0.01]  # far below the minimum observation count
        svc = self._service({"A": _series(short), "B": _series(short)})
        result = svc.analyse(["A", "B"])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["correlations"], [])

    def test_needs_two_symbols(self) -> None:
        with self.assertRaises(ValueError):
            self._service({}).analyse(["ONLYONE"])

    def test_concentration_is_higher_for_lopsided_weights(self) -> None:
        svc = self._service({
            "CALM": _series([0.0001, -0.0001] * 25),
            "WILD": _series([0.08, -0.08] * 25),
        })
        lopsided = svc.analyse(["CALM", "WILD"], scheme="inverse_volatility")
        even = svc.analyse(["CALM", "WILD"], scheme="equal_weight")
        # Equal weights are the least concentrated split possible.
        self.assertGreater(
            lopsided["concentration_hhi"], even["concentration_hhi"]
        )

    def test_proposes_weights_but_places_nothing(self) -> None:
        """Safety: this is research output, not an order path."""
        public = [
            m for m in dir(PortfolioAgentService) if not m.startswith("_")
        ]
        for forbidden in ("order", "submit", "execute", "trade", "buy", "sell"):
            self.assertFalse([m for m in public if forbidden in m.lower()])


class BasketArenaTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)
        self.svc = ArenaService(
            self.path,
            _Backtest({
                "ds_A": _series([0.01] * 40),
                "ds_B": _series([0.02] * 60),
            }),
        )

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_basket_season_reports_per_leg_attribution(self) -> None:
        season = self.svc.create_season(name="Basket", symbols=["A", "B"])
        self.assertTrue(season["is_basket"])
        self.svc.enroll(season_id=season["season_id"], agent_id="alpha@1.0")
        result = self.svc.tick(
            season["season_id"], datasets={"A": "ds_A", "B": "ds_B"}
        )
        entry = result["entries"][0]
        self.assertEqual(entry["data_status"], "ok")
        legs = {leg["symbol"]: leg for leg in entry["legs"]}
        self.assertEqual(set(legs), {"A", "B"})
        # The blended figure is the mean of the legs that had data.
        expected = (legs["A"]["return_pct"] + legs["B"]["return_pct"]) / 2
        self.assertAlmostEqual(entry["return_pct"], expected, places=6)

    def test_a_leg_without_data_does_not_sink_the_entry(self) -> None:
        season = self.svc.create_season(name="Partial", symbols=["A", "GONE"])
        self.svc.enroll(season_id=season["season_id"], agent_id="alpha@1.0")
        result = self.svc.tick(
            season["season_id"], datasets={"A": "ds_A", "GONE": None}
        )
        entry = result["entries"][0]
        self.assertEqual(entry["data_status"], "ok")
        statuses = {leg["symbol"]: leg["data_status"] for leg in entry["legs"]}
        self.assertEqual(statuses["GONE"], "data_missing")
        # Scored only on the leg that had data — the missing one is not a zero.
        a_leg = next(l for l in entry["legs"] if l["symbol"] == "A")
        self.assertAlmostEqual(entry["return_pct"], a_leg["return_pct"], places=6)

    def test_entry_with_no_usable_leg_is_data_missing(self) -> None:
        season = self.svc.create_season(name="Empty", symbols=["X", "Y"])
        self.svc.enroll(season_id=season["season_id"], agent_id="alpha@1.0")
        result = self.svc.tick(
            season["season_id"], datasets={"X": None, "Y": None}
        )
        self.assertEqual(result["entries"][0]["data_status"], "data_missing")
        self.assertIsNone(result["entries"][0]["return_pct"])

    def test_single_symbol_seasons_still_work(self) -> None:
        season = self.svc.create_season(name="Solo", symbol="A")
        self.assertFalse(season["is_basket"])
        self.svc.enroll(season_id=season["season_id"], agent_id="alpha@1.0")
        result = self.svc.tick(season["season_id"], dataset_id="ds_A")
        self.assertEqual(result["entries"][0]["data_status"], "ok")

    def test_season_requires_a_symbol(self) -> None:
        with self.assertRaises(ValueError):
            self.svc.create_season(name="Nothing")


class WeightingTest(unittest.TestCase):
    def test_inverse_volatility_favours_the_calmer_name(self) -> None:
        weights = _weights(["CALM", "WILD"], {"CALM": 0.01, "WILD": 0.04},
                           "inverse_volatility")
        self.assertGreater(weights["CALM"], weights["WILD"])
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)

    def test_no_requested_symbol_is_silently_dropped(self) -> None:
        """Weights that sum to 1 over the wrong set look complete but aren't."""
        weights = _weights(["A", "B", "C"], {"A": 0.01, "B": 0.02},
                           "inverse_volatility")
        self.assertEqual(set(weights), {"A", "B", "C"})
        self.assertEqual(weights["C"], 0.0)

    def test_equal_weight_splits_evenly(self) -> None:
        weights = _weights(["A", "B", "C"], {}, "equal_weight")
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=5)
        self.assertEqual(len(set(weights.values())), 1)

    def test_unmeasurable_volatility_falls_back_to_equal_weight(self) -> None:
        weights = _weights(["A", "B"], {"A": 0.0, "B": 0.0},
                           "inverse_volatility")
        self.assertEqual(weights, {"A": 0.5, "B": 0.5})


class CorrelationTest(unittest.TestCase):
    def test_matches_the_standard_library(self) -> None:
        import statistics

        a, b = [1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 1.0, 4.0, 3.0, 5.0]
        self.assertAlmostEqual(_pearson(a, b), statistics.correlation(a, b), places=9)

    def test_a_constant_series_has_no_correlation_to_report(self) -> None:
        self.assertIsNone(_pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))

    def test_one_observation_is_not_enough(self) -> None:
        self.assertIsNone(_pearson([1.0], [2.0]))


if __name__ == "__main__":
    unittest.main()
