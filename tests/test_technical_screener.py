from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import (
    _normalize_intent_text,
    _parse_technical_screen,
    grounded_tool_response,
)
from iimc_trading_platform.services.instrument_names import _pretty
from iimc_trading_platform.services.screener_service import (
    NIFTY_50,
    ScreenerService,
    resolve_universe,
)


class _MockClient:
    """Falling series for RELIANCE (low RSI), rising for everything else."""

    def historical(self, *, symbol, exchange, interval, start_date, end_date):
        if symbol == "RELIANCE":
            closes = [100 - i * 0.5 for i in range(40)]
        else:
            closes = [100 + i * 0.5 for i in range(40)]
        return {"data": [{"close": c, "volume": 1000} for c in closes]}


class ScreenRoutingTest(unittest.TestCase):
    def _parse(self, message: str):
        return _parse_technical_screen(_normalize_intent_text(message))

    def test_nifty_rsi_below(self) -> None:
        args = self._parse("find NIFTY 50 stocks where RSI is below 30")
        self.assertEqual(args["condition"], "rsi_below")
        self.assertEqual(args["threshold"], 30.0)
        self.assertEqual(args["universe"], "nifty50")

    def test_threshold_after_comparator(self) -> None:
        args = self._parse("screen nifty 50 for rsi below 25")
        self.assertEqual(args["threshold"], 25.0)

    def test_rsi_above_defaults_to_70(self) -> None:
        args = self._parse("nifty 50 stocks where RSI above 70")
        self.assertEqual(args["condition"], "rsi_above")
        self.assertEqual(args["threshold"], 70.0)

    def test_below_ema_with_period(self) -> None:
        args = self._parse("NIFTY 50 stocks trading below their 50 day EMA")
        self.assertEqual(args["condition"], "price_below_ema")
        self.assertEqual(args["period"], 50)

    def test_watchlist_opt_in(self) -> None:
        args = self._parse("screen my watchlist for rsi below 30")
        self.assertIsNone(args["universe"])

    def test_non_screen_returns_none(self) -> None:
        self.assertIsNone(self._parse("what is the price of reliance"))


class ScreenUniverseTest(unittest.TestCase):
    def test_resolve_nifty_variants(self) -> None:
        for name in ("nifty50", "NIFTY 50", "nifty_50"):
            resolved = resolve_universe(name)
            self.assertIsNotNone(resolved)
            self.assertEqual(len(resolved), len(NIFTY_50))
            self.assertTrue(all(r["exchange"] == "NSE" for r in resolved))

    def test_unknown_universe(self) -> None:
        self.assertIsNone(resolve_universe("sp500"))


class ScreenScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "screen.duckdb"
        initialize_database(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_nifty_matches_only_downtrend(self) -> None:
        svc = ScreenerService(self.db, _MockClient())
        result = svc.scan(condition="rsi_below", threshold=30.0, universe="nifty50")
        self.assertEqual(result["universe"], "nifty50")
        self.assertEqual(result["universe_size"], len(NIFTY_50))
        symbols = [m["symbol"] for m in result["matches"]]
        self.assertIn("RELIANCE", symbols)
        self.assertNotIn("TCS", symbols)

    def test_empty_watchlist_is_a_clear_error(self) -> None:
        svc = ScreenerService(self.db, _MockClient())
        with self.assertRaisesRegex(ValueError, "which stocks to scan"):
            svc.scan(condition="rsi_below")

    def test_grounded_response_names_universe(self) -> None:
        svc = ScreenerService(self.db, _MockClient())
        result = svc.scan(condition="rsi_below", threshold=30.0, universe="nifty50")
        answer = grounded_tool_response("run_technical_screen", result)
        self.assertIn("NIFTY 50", answer)
        self.assertIn("RELIANCE", answer)

    def test_matches_carry_company_name_key(self) -> None:
        svc = ScreenerService(self.db, _MockClient())
        result = svc.scan(condition="rsi_below", threshold=30.0, universe="nifty50")
        for match in result["matches"]:
            # Value may be None where the master contract is absent, but the
            # key is always present so callers can rely on it.
            self.assertIn("company_name", match)


class CompanyNameFormatTest(unittest.TestCase):
    def test_pretty_strips_suffixes_and_titlecases(self) -> None:
        self.assertEqual(_pretty("RELIANCE INDUSTRIES LTD"), "Reliance Industries")
        self.assertEqual(_pretty("TATA STEEL LIMITED"), "Tata Steel")
        self.assertEqual(_pretty("AXIS BANK LIMITED"), "Axis Bank")


if __name__ == "__main__":
    unittest.main()
