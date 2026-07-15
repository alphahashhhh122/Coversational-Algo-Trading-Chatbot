from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.infrastructure.openalgo import OpenAlgoResponseError
from iimc_trading_platform.services.execution_readiness_service import (
    ExecutionReadinessService,
)
from iimc_trading_platform.services.openalgo_readiness_service import (
    OpenAlgoReadinessService,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _PartiallyFailingOpenAlgoClient:
    def analyzer_status(self) -> dict:
        return {"mode": "live", "analyze_mode": False}

    def account_snapshot(self, snapshot_type: str) -> dict:
        if snapshot_type == "funds":
            return {"data": {"availablecash": 10000}}
        if snapshot_type == "orderbook":
            raise OpenAlgoResponseError("broker orderbook failed")
        return {"data": []}


class _ReadyCapabilities:
    def platform_status(self, **kwargs: str) -> dict:
        return {
            "symbol": kwargs["symbol"].upper(),
            "exchange": kwargs["exchange"].upper(),
            "asset_class": kwargs["asset_class"],
            "interval": kwargs["interval"],
            "local_dataset_exists": True,
            "supported_by_architecture": True,
        }


class _ReadyOpenAlgo:
    def monitor(self) -> dict:
        return {
            "configured": True,
            "ok": True,
            "status": "available",
            "analyzer_mode": True,
            "live_trading_enabled": False,
            "safe_failure": False,
        }


class ReadinessAndNewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "test.duckdb"
        self.artifacts_dir = self.root / "artifacts"
        self.artifacts_dir.mkdir()
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_openalgo_unavailable_is_structured_safe_failure(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=self.root,
                    openalgo_api_key="configured",
                )
            )
        )
        with patch(
            "iimc_trading_platform.infrastructure.openalgo.urlopen",
            side_effect=URLError("connection refused"),
        ):
            response = client.get("/platform/openalgo/monitor")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "unavailable")
        self.assertTrue(payload["safe_failure"])
        self.assertTrue(payload["no_synthetic_fallback"])

    def test_paper_readiness_requires_a_current_risk_approved_signal(self) -> None:
        service = ExecutionReadinessService(
            AppConfig(
                database_path=self.db_path,
                artifacts_dir=self.artifacts_dir,
                openalgo_root=self.root,
                paper_signal_max_age_minutes=20,
            ),
            _ReadyCapabilities(),
            _ReadyOpenAlgo(),
        )

        result = service.readiness(
            symbol="RELIANCE",
            exchange="NSE",
            asset_class="equity",
            interval="5m",
            start_date="2026-07-01",
            end_date="2026-07-15",
        )
        paper = next(stage for stage in result["stages"] if stage["stage"] == "paper_trading")

        self.assertFalse(paper["can_start"])
        self.assertIn("current_paper_signal_missing", paper["blockers"])
        self.assertFalse(result["paper_signal"]["eligible"])

    def test_openalgo_monitor_reports_partial_account_degradation(self) -> None:
        service = OpenAlgoReadinessService(
            AppConfig(
                database_path=self.db_path,
                artifacts_dir=self.artifacts_dir,
                openalgo_root=self.root,
                openalgo_api_key="configured",
                allow_live_trading=True,
            )
        )
        with patch.object(
            service,
            "_client",
            return_value=_PartiallyFailingOpenAlgoClient(),
        ):
            payload = service.monitor()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "degraded")
        self.assertTrue(payload["safe_failure"])
        self.assertFalse(payload["analyzer_mode"])
        self.assertTrue(payload["live_mode"])
        self.assertTrue(payload["live_trading_enabled"])
        self.assertTrue(payload["checks"]["analyzer"]["ok"])
        self.assertTrue(payload["checks"]["funds"]["ok"])
        self.assertFalse(payload["checks"]["orderbook"]["ok"])
        self.assertEqual(
            payload["checks"]["orderbook"]["status"],
            "provider_error",
        )
        self.assertIn("orderbook", payload["message"])

    def test_market_news_configured_fetches_and_persists_articles(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=self.root,
                    market_news_provider="mock_news",
                    market_news_api_url="https://example.test/news",
                    market_news_api_key="secret-news-key",
                    market_news_max_articles=2,
                )
            )
        )
        provider_payload = {
            "articles": [
                {
                    "title": "RBI keeps policy stance unchanged",
                    "source": {"name": "Mock Wire"},
                    "url": "https://example.test/a",
                    "publishedAt": "2026-06-26T10:00:00",
                },
                {
                    "title": "NIFTY volatility cools",
                    "source": "Mock Desk",
                    "url": "https://example.test/b",
                    "published_at": "2026-06-26T10:05:00",
                },
            ]
        }
        with patch(
            "iimc_trading_platform.services.market_news_service.urlopen",
            return_value=_FakeResponse(provider_payload),
        ):
            response = client.post(
                "/market-news/fetch",
                params={"query": "NIFTY", "symbol": "NIFTY"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["article_count"], 2)
        self.assertNotIn("secret-news-key", json.dumps(payload))
        self.assertTrue(Path(payload["raw_artifact_path"]).exists())

        latest = client.get("/market-news/latest").json()
        self.assertEqual(len(latest["articles"]), 2)

    def test_event_registry_fetch_uses_provider_payload_and_persists(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=self.root,
                    market_news_provider="eventregistry",
                    market_news_api_url=(
                        "https://eventregistry.org/api/v1/article/getArticles"
                    ),
                    market_news_api_key="event-registry-key",
                    market_news_max_articles=1,
                )
            )
        )
        provider_payload = {
            "articles": {
                "results": [
                    {
                        "title": "NIFTY closes higher as banks rally",
                        "source": {"title": "Event Wire"},
                        "url": "https://example.test/nifty",
                        "dateTime": "2026-06-28T09:30:00Z",
                    }
                ]
            }
        }
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return _FakeResponse(provider_payload)

        with patch(
            "iimc_trading_platform.services.market_news_service.urlopen",
            side_effect=fake_urlopen,
        ):
            response = client.post(
                "/market-news/fetch",
                params={"query": "NIFTY banks", "symbol": "NIFTY"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "eventregistry")
        self.assertEqual(payload["article_count"], 1)
        self.assertEqual(payload["articles"][0]["source"], "Event Wire")
        self.assertNotIn("event-registry-key", json.dumps(payload))

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            captured["url"],
            "https://eventregistry.org/api/v1/article/getArticles",
        )
        self.assertEqual(captured["body"]["action"], "getArticles")
        self.assertEqual(captured["body"]["keyword"], "NIFTY banks")
        self.assertEqual(captured["body"]["articlesCount"], 1)
        self.assertEqual(captured["body"]["apiKey"], "event-registry-key")
        self.assertEqual(
            captured["headers"]["Content-type"],
            "application/json",
        )

    def test_event_registry_broadens_over_specific_indian_market_query(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=self.root,
                    market_news_provider="eventregistry",
                    market_news_api_url=(
                        "https://eventregistry.org/api/v1/article/getArticles"
                    ),
                    market_news_api_key="event-registry-key",
                    market_news_max_articles=1,
                )
            )
        )
        provider_payload = {"articles": {"results": []}}
        captured = {}

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(provider_payload)

        with patch(
            "iimc_trading_platform.services.market_news_service.urlopen",
            side_effect=fake_urlopen,
        ):
            client.post(
                "/market-news/fetch",
                params={
                    "query": "India stock market NIFTY Sensex",
                    "symbol": "NIFTY",
                },
            )

        self.assertEqual(captured["body"]["keyword"], "Indian stock market")

    def test_market_news_unconfigured_fetch_is_safe_failure(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=self.root,
                )
            )
        )
        response = client.post("/market-news/fetch", params={"query": "NIFTY"})

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "news_provider_not_configured")
        self.assertEqual(payload["articles"], [])
        self.assertTrue(payload["no_synthetic_fallback"])


if __name__ == "__main__":
    unittest.main()
