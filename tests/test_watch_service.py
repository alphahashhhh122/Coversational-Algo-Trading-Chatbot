from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.orchestration import grounded_tool_response
from iimc_trading_platform.services.watch_service import (
    WatchService,
    parse_watch_request,
)


class _Screener:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot

    def technical_snapshot(self, symbol: str, exchange: str = "NSE") -> dict:
        return {**self.snapshot, "symbol": symbol, "exchange": exchange}


def _snap(rsi=45.0, last=100.0, ema20=95.0, status="ok") -> dict:
    return {
        "status": status,
        "rsi": rsi,
        "last_close": last,
        "ema20": ema20,
        "ema50": ema20,
    }


class WatchServiceTest(unittest.TestCase):
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

    def test_create_and_list(self) -> None:
        svc = WatchService(self.path, _Screener(_snap()))
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        listed = svc.list()["watches"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["symbol"], "RELIANCE")
        self.assertEqual(listed[0]["status"], "active")

    def test_asking_twice_does_not_create_two_alerts(self) -> None:
        """A repeated request is one watch, not two that both fire."""
        svc = WatchService(self.path, _Screener(_snap()))
        first = svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        second = svc.create(symbol="reliance", condition="rsi_below", threshold=30)
        self.assertEqual(len(svc.list()["watches"]), 1)
        self.assertEqual(second["watch_id"], first["watch_id"])
        self.assertFalse(first["already_watching"])
        self.assertTrue(second["already_watching"])

    def test_a_different_level_is_a_different_watch(self) -> None:
        svc = WatchService(self.path, _Screener(_snap()))
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=40)
        self.assertEqual(len(svc.list()["watches"]), 2)

    def test_a_different_symbol_is_a_different_watch(self) -> None:
        svc = WatchService(self.path, _Screener(_snap()))
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        svc.create(symbol="TCS", condition="rsi_below", threshold=30)
        self.assertEqual(len(svc.list()["watches"]), 2)

    def test_recreating_after_removal_makes_a_new_watch(self) -> None:
        """Idempotence must not mean a removed watch can't come back."""
        svc = WatchService(self.path, _Screener(_snap()))
        first = svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        svc.remove_by_id(first["watch_id"])
        again = svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        self.assertNotEqual(again["watch_id"], first["watch_id"])
        self.assertFalse(again["already_watching"])
        self.assertEqual(len(svc.list()["watches"]), 1)

    def test_rsi_condition_requires_threshold(self) -> None:
        svc = WatchService(self.path, _Screener(_snap()))
        with self.assertRaises(ValueError):
            svc.create(symbol="RELIANCE", condition="rsi_below")

    def test_evaluate_fires_when_condition_met(self) -> None:
        # RSI is 25, watching for RSI below 30 -> should fire.
        svc = WatchService(self.path, _Screener(_snap(rsi=25.0)))
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        result = svc.evaluate()
        self.assertEqual(result["checked"], 1)
        self.assertEqual(len(result["fired"]), 1)
        self.assertEqual(result["fired"][0]["symbol"], "RELIANCE")
        # Once triggered it is no longer active.
        self.assertEqual(svc.list()["watches"][0]["status"], "triggered")

    def test_evaluate_does_not_fire_when_not_met(self) -> None:
        svc = WatchService(self.path, _Screener(_snap(rsi=55.0)))
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        result = svc.evaluate()
        self.assertEqual(result["fired"], [])
        self.assertEqual(svc.list()["watches"][0]["status"], "active")

    def test_unavailable_data_is_reported_not_fired(self) -> None:
        svc = WatchService(
            self.path, _Screener(_snap(status="unavailable"))
        )
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        result = svc.evaluate()
        self.assertEqual(result["fired"], [])
        self.assertTrue(result["errors"])

    def test_grounded_render_for_check(self) -> None:
        svc = WatchService(self.path, _Screener(_snap(rsi=20.0)))
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        answer = grounded_tool_response("check_watches", svc.evaluate())
        self.assertIn("Fired", answer)
        self.assertIn("RELIANCE", answer)

    def test_repeat_request_is_answered_honestly(self) -> None:
        """Don't claim to have set up something that already existed."""
        svc = WatchService(self.path, _Screener(_snap()))
        svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        repeat = svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        answer = grounded_tool_response("create_watch", repeat)
        self.assertIn("already watching", answer)
        self.assertNotIn("Now watching", answer)

    def test_first_request_still_confirms_normally(self) -> None:
        svc = WatchService(self.path, _Screener(_snap()))
        created = svc.create(symbol="RELIANCE", condition="rsi_below", threshold=30)
        answer = grounded_tool_response("create_watch", created)
        self.assertIn("Now watching", answer)
        self.assertIn("never trades", answer)


class ParseWatchRequestTest(unittest.TestCase):
    def test_parses_rsi_below(self) -> None:
        parsed = parse_watch_request("watch RELIANCE for RSI below 30")
        self.assertEqual(parsed, {"condition": "rsi_below", "threshold": 30.0})

    def test_parses_rsi_above(self) -> None:
        parsed = parse_watch_request("alert me when TCS RSI goes above 70")
        self.assertEqual(parsed, {"condition": "rsi_above", "threshold": 70.0})

    def test_parses_price_below_ema(self) -> None:
        parsed = parse_watch_request("watch INFY for price below the EMA")
        self.assertEqual(parsed["condition"], "price_below_ema20")

    def test_non_watch_returns_none(self) -> None:
        self.assertIsNone(parse_watch_request("what is the price of RELIANCE"))


if __name__ == "__main__":
    unittest.main()
