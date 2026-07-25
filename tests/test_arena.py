from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.arena_service import ArenaService


class _Backtest:
    """Returns a fixed return per strategy so ranking is deterministic."""

    def __init__(self, returns: dict[str, float] | None = None, boom: bool = False) -> None:
        self.returns = returns or {}
        self.boom = boom

    def load_dataset_candles(self, dataset_id, instrument=None):
        if self.boom:
            raise ValueError("no candles for that window")
        return {"symbol": "X"}, list(range(200))

    def simulate_only(self, *, strategy_name, candles, parameters, starting_equity=1_000_000.0):
        return {
            "total_trades": 10,
            "net_pnl": 100.0,
            "max_drawdown": -50.0,
            "return_pct": self.returns.get(strategy_name, 1.0),
        }


class ArenaTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def _season(self, svc: ArenaService) -> str:
        return svc.create_season(name="Test Season", symbol="RELIANCE")["season_id"]

    def test_season_enrollment_is_idempotent(self) -> None:
        svc = ArenaService(self.path, _Backtest())
        season_id = self._season(svc)
        first = svc.enroll(season_id=season_id, agent_id="alpha@1.0")
        again = svc.enroll(season_id=season_id, agent_id="alpha@1.0")
        self.assertEqual(first["status"], "enrolled")
        self.assertEqual(again["status"], "already_enrolled")
        self.assertEqual(again["entry_id"], first["entry_id"])
        self.assertEqual(svc.list_seasons()["seasons"][0]["entries"], 1)

    def test_tick_ranks_by_return_on_real_data(self) -> None:
        svc = ArenaService(
            self.path, _Backtest({"ema_crossover": 5.0, "sma_crossover": 9.0})
        )
        season_id = self._season(svc)
        svc.enroll(season_id=season_id, agent_id="alpha@1.0", strategy_name="ema_crossover")
        svc.enroll(season_id=season_id, agent_id="beta@1.0", strategy_name="sma_crossover")
        svc.tick(season_id, dataset_id="ds1")
        standings = svc.standings(season_id)["standings"]
        self.assertEqual([s["agent_id"] for s in standings], ["beta@1.0", "alpha@1.0"])
        self.assertEqual(standings[0]["rank"], 1)
        # Equity reflects the starting bankroll and the return.
        self.assertAlmostEqual(standings[0]["equity"], 1_090_000.0, places=2)

    def test_missing_data_is_recorded_not_interpolated(self) -> None:
        svc = ArenaService(self.path, _Backtest(boom=True))
        season_id = self._season(svc)
        svc.enroll(season_id=season_id, agent_id="alpha@1.0")
        result = svc.tick(season_id, dataset_id="ds1")
        self.assertEqual(result["entries"][0]["data_status"], "data_missing")
        self.assertIsNone(result["entries"][0]["equity"])
        board = svc.standings(season_id)
        # A missing day never becomes a zero that looks like a real result.
        self.assertEqual(board["standings"], [])
        self.assertEqual(len(board["unavailable"]), 1)

    def test_tick_without_dataset_is_honest(self) -> None:
        svc = ArenaService(self.path, _Backtest())
        season_id = self._season(svc)
        svc.enroll(season_id=season_id, agent_id="alpha@1.0")
        result = svc.tick(season_id, dataset_id=None)
        self.assertEqual(result["entries"][0]["data_status"], "data_missing")
        self.assertIn("no dataset", result["entries"][0]["reason"])

    def test_repeated_tick_same_day_overwrites_not_duplicates(self) -> None:
        svc = ArenaService(self.path, _Backtest({"ema_crossover": 3.0}))
        season_id = self._season(svc)
        svc.enroll(season_id=season_id, agent_id="alpha@1.0")
        svc.tick(season_id, dataset_id="ds1")
        svc.tick(season_id, dataset_id="ds1")
        self.assertEqual(len(svc.standings(season_id)["standings"]), 1)

    def test_unknown_season_rejected(self) -> None:
        svc = ArenaService(self.path, _Backtest())
        with self.assertRaises(ValueError):
            svc.tick("season_nope", dataset_id="ds1")

    def test_arena_has_no_broker_path(self) -> None:
        """Safety invariant: no order-placement path exists in the arena.

        Checked against the parsed AST (imports + attribute/function names), so
        prose in docstrings can discuss the guarantee without tripping it.
        """
        import ast

        tree = ast.parse(
            Path("iimc_trading_platform/services/arena_service.py").read_text(
                encoding="utf-8"
            )
        )
        imported: set[str] = set()
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Attribute):
                called.add(node.attr)
            elif isinstance(node, ast.Name):
                called.add(node.id)

        forbidden_imports = {"OpenAlgoClient", "openalgo", "sandbox_execution_service"}
        self.assertFalse(
            {name for name in imported if any(f.lower() in name.lower() for f in forbidden_imports)},
            "arena must not import any broker client",
        )
        for forbidden in ("place_order", "submit_order", "place_smart_order", "quote"):
            self.assertNotIn(forbidden, called, f"arena must not call {forbidden}")


if __name__ == "__main__":
    unittest.main()
