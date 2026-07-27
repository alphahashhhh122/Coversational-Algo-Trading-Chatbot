from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.data_health_service import DataHealthService
from iimc_trading_platform.services.strategy_optimizer_service import _GRIDS
from iimc_trading_platform.services.universe_backfill_service import (
    UniverseBackfillService,
)


class _Importer:
    """Imports succeed except for symbols listed in ``fail``."""

    def __init__(self, fail: set[str] | None = None, rows: int = 250) -> None:
        self.fail = fail or set()
        self.rows = rows
        self.calls: list[str] = []

    def import_history(self, *, symbol, exchange, asset_class, interval,
                       start_date, end_date, dataset_id=None):
        self.calls.append(symbol)
        if symbol in self.fail:
            raise ValueError(f"broker rejected {symbol}")
        return {"row_count": self.rows}


class _DbTest(unittest.TestCase):
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


class UniverseBackfillTest(_DbTest):
    def test_takes_bounded_bites_and_is_resumable(self) -> None:
        importer = _Importer()
        svc = UniverseBackfillService(self.path, importer)

        first = svc.run(max_symbols=3)
        self.assertEqual(first["attempted"], 3)
        self.assertEqual(len(importer.calls), 3)

        second = svc.run(max_symbols=3)
        # Resumes with *different* symbols rather than redoing the first three.
        self.assertEqual(len(set(importer.calls)), 6)
        self.assertEqual(second["attempted"], 3)

        status = svc.status()
        self.assertEqual(status["imported"], 6)
        self.assertEqual(status["pending"], status["total"] - 6)

    def test_a_failure_is_recorded_and_does_not_stop_the_rest(self) -> None:
        importer = _Importer(fail={"ADANIENT"})
        svc = UniverseBackfillService(self.path, importer)
        result = svc.run(max_symbols=3)

        statuses = {r["symbol"]: r["status"] for r in result["results"]}
        self.assertEqual(statuses["ADANIENT"], "failed")
        self.assertEqual(len([s for s in statuses.values() if s == "ok"]), 2)
        status = svc.status()
        self.assertEqual(status["failed"], 1)
        self.assertEqual(status["imported"], 2)

    def test_failed_symbols_are_retried_by_default(self) -> None:
        importer = _Importer(fail={"ADANIENT"})
        svc = UniverseBackfillService(self.path, importer)
        svc.run(max_symbols=1)
        importer.fail = set()  # broker recovered
        svc.run(max_symbols=1)
        self.assertEqual(svc.status()["imported"], 1)
        self.assertEqual(svc.status()["failed"], 0)

    def test_skipping_failures_is_possible(self) -> None:
        importer = _Importer(fail={"ADANIENT"})
        svc = UniverseBackfillService(self.path, importer)
        svc.run(max_symbols=1)
        svc.run(max_symbols=1, retry_failed=False)
        self.assertNotIn("ADANIENT", importer.calls[1:])

    def test_unknown_universe_rejected(self) -> None:
        with self.assertRaises(ValueError):
            UniverseBackfillService(self.path, _Importer()).run(universe="nope")


class DataHealthTest(_DbTest):
    def _add_price(self, symbol: str, rows: int = 100) -> None:
        con = connect(self.path)
        try:
            # Column order: dataset_id, data_domain, data_type, symbol,
            # exchange, interval, start_ts, end_ts, row_count, storage_table,
            # source_id, quality_status, quality_report_path, updated_at.
            con.execute(
                "INSERT INTO data_catalog VALUES (?, 'market_data', 'ohlcv', ?, "
                "'NSE', 'D', ?, ?, ?, 'ohlcv', 'src', 'validated', NULL, ?)",
                [
                    f"ds_{symbol}", symbol,
                    datetime(2026, 1, 1), datetime(2026, 6, 1), rows,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ],
            )
        finally:
            con.close()

    def test_reports_coverage_and_names_the_gaps(self) -> None:
        self._add_price("RELIANCE")
        report = DataHealthService(self.path).coverage()
        self.assertEqual(report["with_price_history"], 1)
        self.assertLess(report["price_coverage_pct"], 100)
        self.assertTrue(any("no price history" in g for g in report["gaps"]))
        covered = next(
            s for s in report["symbols"] if s["symbol"] == "RELIANCE"
        )
        self.assertTrue(covered["has_price_history"])
        self.assertIn("backtest", covered["ready_for"])
        # Without fundamentals, fundamental analysis is honestly not offered.
        self.assertNotIn("fundamental_analysis", covered["ready_for"])

    def test_symbol_without_data_is_ready_for_nothing(self) -> None:
        report = DataHealthService(self.path).coverage()
        any_symbol = report["symbols"][0]
        self.assertFalse(any_symbol["has_price_history"])
        self.assertEqual(any_symbol["ready_for"], [])


class OptimizerCoverageTest(unittest.TestCase):
    def test_grids_cover_the_registered_templates(self) -> None:
        # Phase B widened the search space beyond the two crossover templates.
        self.assertIn("rsi_mean_reversion", _GRIDS)
        self.assertIn("momentum_roc", _GRIDS)
        self.assertGreaterEqual(len(_GRIDS), 4)

    def test_grid_parameters_match_the_strategy_schemas(self) -> None:
        """A grid with the wrong parameter names would fail only at runtime."""
        from iimc_trading_platform.strategies.registry import (
            build_strategy_registry,
        )

        registry = build_strategy_registry()
        for name, grid in _GRIDS.items():
            strategy = registry.get(name)
            allowed = set(strategy.parameter_schema)
            for candidate in grid:
                unknown = set(candidate) - allowed
                self.assertFalse(
                    unknown, f"{name} grid has unknown parameters {unknown}"
                )
                # And every candidate must validate against the real schema.
                strategy.validate_parameters(candidate)


if __name__ == "__main__":
    unittest.main()
