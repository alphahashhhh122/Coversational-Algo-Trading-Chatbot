from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.sdk import ATLClient, ATLError
from iimc_trading_platform.services.contest_service import (
    ContestService,
    dataset_fingerprint,
)


class _Backtest:
    def __init__(self, candles=None) -> None:
        self.candles = candles or [
            {"timestamp": f"t{i}", "open": i, "high": i, "low": i, "close": i, "volume": 1}
            for i in range(50)
        ]

    def load_dataset_candles(self, dataset_id, instrument=None):
        return {"symbol": "RELIANCE"}, self.candles


class DatasetFingerprintTest(unittest.TestCase):
    def test_same_candles_same_hash(self) -> None:
        bt = _Backtest()
        self.assertEqual(
            dataset_fingerprint(bt.candles), dataset_fingerprint(bt.candles)
        )

    def test_changed_candle_changes_hash(self) -> None:
        original = _Backtest().candles
        tampered = [dict(c) for c in original]
        tampered[10]["close"] = 999.0
        self.assertNotEqual(
            dataset_fingerprint(original), dataset_fingerprint(tampered)
        )


class ContestTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)
        initialize_database(self.path)
        self.svc = ContestService(self.path, _Backtest())

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_creation_freezes_the_dataset(self) -> None:
        contest = self.svc.create(
            name="July Open", symbol="RELIANCE", dataset_id="ds1"
        )
        self.assertEqual(contest["status"], "open")
        self.assertIsNotNone(contest["dataset_hash"])
        self.assertEqual(contest["frozen_rows"], 50)
        self.assertTrue(self.svc.is_open(contest["contest_id"]))

    def test_closed_contest_refuses_late_entries(self) -> None:
        contest = self.svc.create(
            name="Closed", symbol="RELIANCE", dataset_id="ds1", open_for_days=-1
        )
        # Deadline already passed -> not open for entries.
        self.assertFalse(self.svc.is_open(contest["contest_id"]))

    def test_close_snapshots_standings_with_evidence(self) -> None:
        contest = self.svc.create(
            name="July Open", symbol="RELIANCE", dataset_id="ds1"
        )
        leaderboard = {
            "ranked": [
                {
                    "agent_id": "alpha@1.0", "version": "1.0", "run_id": "arun_a",
                    "composite": 9.0, "rank": 1, "metrics": {"oos": 9.0},
                },
                {
                    "agent_id": "beta@1.0", "version": "1.0", "run_id": "arun_b",
                    "composite": 4.0, "rank": 2, "metrics": {"oos": 4.0},
                },
            ]
        }
        closed = self.svc.close(contest["contest_id"], leaderboard)
        self.assertEqual(closed["entrants"], 2)
        results = self.svc.results(contest["contest_id"])
        self.assertEqual(results["status"], "closed")
        self.assertEqual([r["rank"] for r in results["results"]], [1, 2])
        # The evidence link survives into the frozen record.
        self.assertEqual(results["results"][0]["run_id"], "arun_a")
        self.assertIsNotNone(results["dataset_hash"])

    def test_closed_contest_cannot_be_rewritten(self) -> None:
        contest = self.svc.create(name="X", symbol="RELIANCE", dataset_id="ds1")
        cid = contest["contest_id"]
        self.svc.close(cid, {"ranked": [
            {"agent_id": "alpha@1.0", "run_id": "arun_a", "composite": 9.0, "rank": 1}
        ]})
        # A later, different leaderboard must not overwrite history.
        again = self.svc.close(cid, {"ranked": [
            {"agent_id": "cheater@1.0", "run_id": "arun_z", "composite": 99.0, "rank": 1}
        ]})
        self.assertEqual(again["status"], "already_closed")
        results = self.svc.results(cid)["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["agent_id"], "alpha@1.0")

    def test_contest_opens_without_data_and_says_so(self) -> None:
        class Boom:
            def load_dataset_candles(self, dataset_id, instrument=None):
                raise ValueError("no data yet")

        svc = ContestService(self.path, Boom())
        contest = svc.create(name="Y", symbol="TCS", dataset_id="ds_missing")
        self.assertIsNone(contest["dataset_hash"])
        self.assertEqual(contest["status"], "open")

    def test_unknown_contest_close_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.svc.close("contest_nope", {"ranked": []})


class SdkTest(unittest.TestCase):
    """The SDK is a thin, dependency-free wrapper — verified by faking transport."""

    def test_urls_and_payloads(self) -> None:
        calls: list[tuple[str, str, dict | None]] = []

        def fake_request(self, method, path, body):
            calls.append((method, path, body))
            return {"agents": [], "ranked": [], "unranked": [], "runs": [],
                    "seasons": [], "standings": []}

        with patch.object(ATLClient, "_request", fake_request):
            client = ATLClient("http://example.test")
            client.list_agents()
            client.list_agents("strategy")
            client.run_agent("market_researcher", symbol="RELIANCE")
            client.leaderboard("strategy")
            client.committee("TCS", members=["market_researcher"])
            client.tick("season_1")

        self.assertEqual(calls[0][:2], ("GET", "/agents"))
        self.assertEqual(calls[1][1], "/agents?category=strategy")
        self.assertEqual(calls[2][:2], ("POST", "/agents/market_researcher/run"))
        self.assertEqual(calls[2][2]["symbol"], "RELIANCE")
        self.assertEqual(calls[3][1], "/leaderboard?category=strategy")
        self.assertEqual(calls[4][2]["members"], ["market_researcher"])
        self.assertEqual(calls[5][:2], ("POST", "/arena/seasons/season_1/tick"))

    def test_no_order_or_approval_surface(self) -> None:
        """Safety: the SDK must expose no way to approve or place an order."""
        methods = [m for m in dir(ATLClient) if not m.startswith("_")]
        for forbidden in ("approve", "order", "submit", "execute", "trade", "buy", "sell"):
            self.assertFalse(
                [m for m in methods if forbidden in m.lower()],
                f"SDK must not expose a method containing {forbidden!r}",
            )

    def test_unreachable_server_gives_actionable_error(self) -> None:
        client = ATLClient("http://127.0.0.1:9")  # nothing listens here
        with self.assertRaises(ATLError) as ctx:
            client.list_agents()
        self.assertIn("Is the server running?", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
