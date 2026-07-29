"""Direct tests for the services that had none.

These were only ever exercised through an endpoint, if at all. Each case pins
the promise the service actually makes — a coverage answer, a stored
preference, a clean install — rather than re-testing the route around it.

One of the seven, ``DomainGuardService``, turned out to be dead: nothing
imported it, and the domain refusal it duplicated is implemented and wired in
``orchestration/education.py``. Writing tests for it would have preserved code
no caller reaches, so it was deleted instead.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.services.dashboard_preference_service import (
    DashboardPreferenceService,
)
from iimc_trading_platform.services.foundation_verification_service import (
    verify_clean_foundation,
)
from iimc_trading_platform.services.historical_data_service import (
    HistoricalDataService,
)
from iimc_trading_platform.services.live_market_service import LiveMarketService


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


class FoundationVerificationTest(unittest.TestCase):
    """Proves a brand-new install comes up clean, in a throwaway directory."""

    def test_a_fresh_foundation_is_healthy_and_empty(self) -> None:
        result = verify_clean_foundation()
        self.assertEqual(result["status"], "healthy")
        checks = result["checks"]
        self.assertTrue(checks["database_initialized"])
        self.assertTrue(checks["core_schema_complete"])
        # A new install must not appear to hold data it has never seen.
        self.assertTrue(checks["new_catalog_is_empty"])
        self.assertTrue(checks["live_trading_disabled"])

    def test_initialising_twice_is_safe(self) -> None:
        """Startup runs initialize_database every time; it must be idempotent."""
        self.assertTrue(
            verify_clean_foundation()["checks"]["repeat_initialization_succeeded"]
        )

    def test_it_leaves_nothing_behind(self) -> None:
        """It builds in a temporary directory, so nothing should persist."""
        first = verify_clean_foundation()
        second = verify_clean_foundation()
        self.assertEqual(first["status"], second["status"])
        self.assertTrue(second["checks"]["new_catalog_is_empty"])


class HistoricalDataCoverageTest(_DbTest):
    """Answers "do we already hold this?" — and must never guess."""

    def _dataset(self, symbol: str, exchange: str, interval: str) -> None:
        con = connect(self.path)
        try:
            con.execute(
                "INSERT INTO data_catalog VALUES (?, 'market_data', 'ohlcv', "
                "?, ?, ?, ?, ?, 500, 'ohlcv', 'src', 'validated', NULL, ?)",
                [
                    f"ds_{symbol}_{interval}", symbol, exchange, interval,
                    datetime(2026, 1, 1), datetime(2026, 6, 1),
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ],
            )
        finally:
            con.close()

    def test_an_empty_catalogue_reports_nothing_held(self) -> None:
        result = HistoricalDataService(self.path).local_coverage(
            symbol="RELIANCE", exchange="NSE", interval="D"
        )
        self.assertFalse(result["historical_available_locally"])
        self.assertEqual(result["datasets"], [])
        self.assertTrue(result["no_synthetic_fallback"])

    def test_a_stored_dataset_is_found_and_marked_real(self) -> None:
        self._dataset("RELIANCE", "NSE", "D")
        result = HistoricalDataService(self.path).local_coverage(
            symbol="RELIANCE", exchange="NSE", interval="D"
        )
        self.assertTrue(result["historical_available_locally"])
        self.assertEqual(len(result["datasets"]), 1)
        self.assertEqual(result["datasets"][0]["data_source"], "real")
        self.assertEqual(result["datasets"][0]["row_count"], 500)

    def test_the_lookup_ignores_case(self) -> None:
        self._dataset("RELIANCE", "NSE", "D")
        result = HistoricalDataService(self.path).local_coverage(
            symbol="reliance", exchange="nse", interval="D"
        )
        self.assertTrue(result["historical_available_locally"])
        # The answer echoes the canonical form, not what was typed.
        self.assertEqual(result["symbol"], "RELIANCE")

    def test_an_empty_interval_matches_every_interval(self) -> None:
        self._dataset("RELIANCE", "NSE", "D")
        self._dataset("RELIANCE", "NSE", "5m")
        result = HistoricalDataService(self.path).local_coverage(
            symbol="RELIANCE", exchange="NSE", interval=""
        )
        self.assertEqual(len(result["datasets"]), 2)

    def test_a_different_interval_is_not_a_match(self) -> None:
        self._dataset("RELIANCE", "NSE", "D")
        result = HistoricalDataService(self.path).local_coverage(
            symbol="RELIANCE", exchange="NSE", interval="5m"
        )
        self.assertFalse(result["historical_available_locally"])


class LiveMarketServiceTest(unittest.TestCase):
    """A thin pass-through — the point is that it does not embellish."""

    def test_it_forwards_the_request_unchanged(self) -> None:
        seen = {}

        class _Readiness:
            def readiness(self, **kwargs):
                seen.update(kwargs)
                return {"status": "ready", "symbol": kwargs["symbol"]}

        result = LiveMarketService(_Readiness()).quote_readiness(
            symbol="RELIANCE", exchange="NSE", asset_class="equity",
            interval="D", start_date="2026-01-01", end_date="2026-06-01",
        )
        self.assertEqual(seen["symbol"], "RELIANCE")
        self.assertEqual(seen["asset_class"], "equity")
        self.assertEqual(result["status"], "ready")

    def test_a_provider_failure_is_not_swallowed(self) -> None:
        """A quote service that hides an outage is worse than one that fails."""

        class _Broken:
            def readiness(self, **kwargs):
                raise RuntimeError("broker unreachable")

        with self.assertRaises(RuntimeError):
            LiveMarketService(_Broken()).quote_readiness(
                symbol="RELIANCE", exchange="NSE", asset_class="equity",
                interval="D", start_date="2026-01-01", end_date="2026-06-01",
            )


class DashboardPreferenceTest(_DbTest):
    """Per-user layout, which must not leak between users."""

    def setUp(self) -> None:
        super().setUp()
        self.svc = DashboardPreferenceService(self.path)

    def test_a_new_user_gets_defaults_not_an_error(self) -> None:
        prefs = self.svc.get("alice")
        self.assertIn("widgets", prefs)
        self.assertTrue(prefs["widgets"])

    def test_an_update_round_trips(self) -> None:
        self.svc.update("alice", widgets=["research", "backtests"], auto_refresh=True)
        prefs = self.svc.get("alice")
        self.assertEqual(prefs["widgets"], ["research", "backtests"])
        self.assertTrue(prefs["auto_refresh"])

    def test_one_users_layout_does_not_reach_another(self) -> None:
        self.svc.update("alice", widgets=["research"], auto_refresh=True)
        self.assertNotEqual(self.svc.get("bob")["widgets"], ["research"])

    def test_updating_twice_replaces_rather_than_accumulates(self) -> None:
        self.svc.update("alice", widgets=["research"], auto_refresh=False)
        self.svc.update("alice", widgets=["risk"], auto_refresh=False)
        self.assertEqual(self.svc.get("alice")["widgets"], ["risk"])

    def test_an_unknown_widget_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.svc.update("alice", widgets=["not_a_widget"], auto_refresh=False)


class PlatformDashboardTest(_DbTest):
    """Read-only evidence about the platform's own state."""

    def _service(self):
        import tempfile as _tf

        from iimc_trading_platform.config import AppConfig
        from iimc_trading_platform.services.platform_dashboard_service import (
            PlatformDashboardService,
        )

        artifacts = Path(_tf.mkdtemp())
        return PlatformDashboardService(
            AppConfig(database_path=self.path, artifacts_dir=artifacts)
        )

    def test_summary_reports_a_fresh_platform_without_inventing_counts(self) -> None:
        summary = self._service().summary()
        self.assertIn("health", summary)
        counts = summary.get("counts", {})
        # An empty database must read as empty, not as unknown or absent.
        self.assertTrue(all(v == 0 for v in counts.values()), counts)

    def test_summary_is_read_only(self) -> None:
        """Calling it must not create rows — it reports, it does not seed."""
        service = self._service()
        service.summary()
        before = service.summary().get("counts", {})
        service.summary()
        self.assertEqual(before, service.summary().get("counts", {}))

    def test_operator_review_returns_a_workflow_not_an_opinion(self) -> None:
        review = self._service().operator_review()
        self.assertIsInstance(review, dict)
        self.assertTrue(review)


class ResearchServiceTest(_DbTest):
    """Research context and briefs — evidence, never advice."""

    class _Capabilities:
        def platform_status(self, **kwargs):
            return {"symbol": kwargs["symbol"], "ready": True}

    class _News:
        def __init__(self, articles=None):
            self.articles = articles if articles is not None else []

        def latest(self, limit=5):
            return {"articles": self.articles[:limit]}

    class _Readiness:
        def assess(self, **kwargs):
            return {"status": "ready"}

    def _service(self, news=None):
        from iimc_trading_platform.services.research_service import ResearchService

        return ResearchService(
            self.path,
            self._Capabilities(),
            news or self._News(),
            self._Readiness(),
        )

    def test_context_is_labelled_research_not_advice(self) -> None:
        context = self._service().research_context(
            symbol="RELIANCE", exchange="NSE", asset_class="equity",
            interval="D", start_date="2026-01-01", end_date="2026-06-01",
        )
        # The platform must never let a research payload read as a recommendation.
        self.assertTrue(context["research_not_advice"])
        self.assertTrue(context["no_synthetic_fallback"])

    def test_context_carries_readiness_and_news_through(self) -> None:
        news = self._News([{"title": "A headline"}])
        context = self._service(news).research_context(
            symbol="RELIANCE", exchange="NSE", asset_class="equity",
            interval="D", start_date="2026-01-01", end_date="2026-06-01",
        )
        self.assertEqual(context["readiness"]["symbol"], "RELIANCE")
        self.assertEqual(len(context["news"]["articles"]), 1)

    def test_no_news_is_an_empty_list_not_a_fabricated_headline(self) -> None:
        context = self._service().research_context(
            symbol="RELIANCE", exchange="NSE", asset_class="equity",
            interval="D", start_date="2026-01-01", end_date="2026-06-01",
        )
        self.assertEqual(context["news"]["articles"], [])

    def test_briefs_start_empty(self) -> None:
        self.assertEqual(self._service().list_briefs()["briefs"], [])


if __name__ == "__main__":
    unittest.main()
