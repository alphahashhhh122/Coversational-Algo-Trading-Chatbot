from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database


class PlatformApiRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "test.duckdb"
        self.artifacts_dir = root / "artifacts"
        self.artifacts_dir.mkdir()
        initialize_database(self.db_path)
        self._insert_dataset()
        self.client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=root,
                )
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_platform_routes_are_in_openapi_without_annotation_crash(self) -> None:
        payload = self.client.get("/openapi.json").json()
        paths = payload["paths"]

        self.assertIn("/platform/summary", paths)
        self.assertIn("/platform/dashboard", paths)
        self.assertIn("/platform/dashboard/summary", paths)
        self.assertIn("/platform/dashboard/preferences", paths)
        self.assertIn("/platform/operator-review", paths)
        self.assertNotIn("/platform/professor-demo", paths)
        self.assertNotIn("/platform/professor-review", paths)
        self.assertIn("/platform/status", paths)
        self.assertIn("/platform/execution/readiness", paths)
        self.assertIn("/platform/symbol/readiness", paths)
        self.assertIn("/platform/research/context", paths)
        self.assertIn("/platform/research/briefs", paths)
        self.assertIn("/platform/openalgo/monitor", paths)
        self.assertIn("/platform/instruments/search", paths)
        self.assertIn("/platform/instruments/symbol", paths)
        self.assertIn("/platform/instruments/quote", paths)
        self.assertIn("/platform/instruments/optionsymbol", paths)
        self.assertIn("/platform/backtest/run", paths)
        self.assertIn("/sandbox/intents", paths)
        self.assertIn("/sandbox/intents/{intent_id}/cancel", paths)
        self.assertIn("/market-news/status", paths)
        self.assertIn("/market-news/latest", paths)
        self.assertIn("/market-news/fetch", paths)

    def test_platform_summary_is_safe_without_external_keys(self) -> None:
        response = self.client.get("/platform/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ready_for_local_operation")
        self.assertFalse(payload["safety"]["live_trading_enabled"])
        self.assertTrue(payload["safety"]["no_synthetic_fallback"])
        self.assertFalse(payload["safety"]["openalgo_key_configured"])
        self.assertGreaterEqual(payload["counts"]["data_catalog"], 1)
        self.assertIn("asset_coverage", payload)
        self.assertTrue(
            payload["asset_coverage"]["options"]["local_data_available"]
        )
        self.assertFalse(
            payload["asset_coverage"]["crypto"]["local_data_available"]
        )
        self.assertTrue(payload["execution_paths"]["backtest"]["enabled"])
        self.assertFalse(payload["execution_paths"]["live_trading"]["enabled"])

    def test_operator_review_route_returns_workflow_contract(self) -> None:
        response = self.client.get("/platform/operator-review")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("operator_goal", payload)
        stages = [item["stage"] for item in payload["workflow"]]
        self.assertEqual(
            stages,
            [
                "data",
                "entry_exit_signal",
                "risk_management",
                "order_management",
                "execution_and_performance",
            ],
        )
        self.assertTrue(payload["safety"]["no_synthetic_fallback"])
        self.assertEqual(payload["latest_completed_run"], None)
        self.assertGreaterEqual(len(payload["ui_actions"]), 3)

    def test_knowledge_document_upload_and_search(self) -> None:
        response = self.client.post(
            "/knowledge/documents",
            json={
                "title": "Acme Industries Annual Report 2026",
                "text": (
                    "Acme Industries reported revenue growth of 18 percent "
                    "driven by specialty chemicals.\n\n"
                    "The board declared a dividend of 12 rupees per share "
                    "and net debt fell by 30 percent."
                ),
                "document_type": "text",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["document_id"].startswith("doc_"))
        self.assertTrue(payload["audit_id"].startswith("audit_"))
        self.assertGreaterEqual(payload["chunk_count"], 1)

        listing = self.client.get("/knowledge/documents").json()
        titles = {item["title"] for item in listing["documents"]}
        self.assertIn("Acme Industries Annual Report 2026", titles)

        search = self.client.post(
            "/knowledge/search",
            json={"query": "Acme specialty chemicals revenue"},
        )
        self.assertEqual(search.status_code, 200)
        matches = search.json()["matches"]
        self.assertTrue(
            any("specialty chemicals" in item["content"] for item in matches)
        )

    def test_knowledge_document_upload_rejects_empty_text(self) -> None:
        response = self.client.post(
            "/knowledge/documents",
            json={"title": "Empty upload", "text": "   "},
        )

        self.assertEqual(response.status_code, 400)

    def test_knowledge_document_pdf_without_pypdf_is_rejected(self) -> None:
        try:
            import pypdf  # noqa: F401
            self.skipTest("pypdf is installed; ImportError path not reachable")
        except ImportError:
            pass
        response = self.client.post(
            "/knowledge/documents",
            json={
                "title": "PDF upload",
                "content_base64": "JVBERi0xLjQK",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("pypdf", response.json()["detail"])

    def test_document_overview_returns_ordered_excerpts(self) -> None:
        upload = self.client.post(
            "/knowledge/documents",
            json={
                "title": "Zenith Motors FY26 Filing",
                "text": (
                    "Zenith Motors grew EV sales 40 percent.\n\n"
                    "Operating margin expanded to 14 percent on cost "
                    "controls.\n\n"
                    "The company plans two new plants in 2027."
                ),
            },
        )
        self.assertEqual(upload.status_code, 200)

        from iimc_trading_platform.services.knowledge_service import (
            KnowledgeService,
        )

        service = KnowledgeService(self.db_path)
        overview = service.document_overview("zenith motors fy26 filing")
        self.assertEqual(overview["title"], "Zenith Motors FY26 Filing")
        self.assertGreaterEqual(overview["chunk_count"], 1)
        self.assertGreater(overview["total_words"], 10)
        self.assertIn("EV sales", overview["chunks"][0]["content"])

        partial = service.document_overview("Zenith Motors")
        self.assertEqual(partial["document_id"], overview["document_id"])

        with self.assertRaises(ValueError):
            service.document_overview("No Such Document")

        chat = self.client.post(
            "/chat",
            json={
                "session_id": "session_doc_test",
                "message": "Analyze document Zenith Motors FY26 Filing",
            },
        )
        self.assertEqual(chat.status_code, 200)
        payload = chat.json()
        self.assertEqual(payload["intent"], "analyze_knowledge_document")
        self.assertIn("EV sales", payload["answer"])
        self.assertIn("search knowledge", payload["answer"].lower())

    def test_uploaded_document_survives_knowledge_sync_job(self) -> None:
        upload = self.client.post(
            "/knowledge/documents",
            json={
                "title": "Uploaded Earnings Transcript",
                "text": "Management guided for stable operating margins.",
            },
        )
        self.assertEqual(upload.status_code, 200)

        from iimc_trading_platform.config import AppConfig as _AppConfig
        from iimc_trading_platform.services import (
            build_job_service,
            register_default_jobs,
        )

        config = _AppConfig(
            database_path=self.db_path,
            artifacts_dir=self.artifacts_dir,
        )
        job_service = build_job_service(config)
        register_default_jobs(job_service, include_openalgo=False)
        sync_job_id = next(
            job["job_id"]
            for job in job_service.list_jobs()["jobs"]
            if job["job_type"] == "knowledge_sync"
        )
        job_service.run_now(sync_job_id, "test_worker")

        listing = self.client.get("/knowledge/documents").json()
        titles = {item["title"] for item in listing["documents"]}
        self.assertIn("Uploaded Earnings Transcript", titles)

    def test_platform_status_finds_known_local_dataset(self) -> None:
        response = self.client.get(
            "/platform/status",
            params={
                "symbol": "NIFTY",
                "exchange": "NFO",
                "asset_class": "options",
                "interval": "5m",
                "start_date": "2026-04-23",
                "end_date": "2026-05-23",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["supported_by_architecture"])
        self.assertTrue(payload["local_dataset_exists"])
        self.assertEqual(payload["rows_available"], 10)
        self.assertTrue(payload["no_synthetic_fallback"])

    def test_platform_status_rejects_unsupported_asset_class_cleanly(self) -> None:
        response = self.client.get(
            "/platform/status",
            params={
                "symbol": "NIFTY",
                "exchange": "NFO",
                "asset_class": "collectible",
                "interval": "5m",
                "start_date": "2026-04-23",
                "end_date": "2026-05-23",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["safe_failure"])
        self.assertFalse(payload["supported_by_architecture"])
        self.assertTrue(payload["no_synthetic_fallback"])

    def test_execution_readiness_returns_stage_blockers(self) -> None:
        response = self.client.get(
            "/platform/execution/readiness",
            params={
                "symbol": "NIFTY",
                "exchange": "NFO",
                "asset_class": "options",
                "interval": "5m",
                "start_date": "2026-04-23",
                "end_date": "2026-05-23",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        stages = {item["stage"]: item for item in payload["stages"]}
        self.assertTrue(stages["research"]["can_start"])
        self.assertTrue(stages["backtest"]["can_start"])
        self.assertFalse(stages["paper_trading"]["can_start"])
        self.assertIn(
            "openalgo_not_ready",
            stages["paper_trading"]["blockers"],
        )
        self.assertFalse(stages["live_trading"]["can_start"])
        self.assertTrue(payload["no_synthetic_fallback"])

    def test_execution_readiness_distinguishes_live_enabled_provider_blocker(self) -> None:
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=Path(self.temp_dir.name),
                    openalgo_api_key="configured",
                    allow_live_trading=True,
                )
            )
        )

        response = client.get(
            "/platform/execution/readiness",
            params={
                "symbol": "BTCUSDT",
                "exchange": "BINANCE",
                "asset_class": "crypto",
                "interval": "1h",
                "start_date": "2026-06-01",
                "end_date": "2026-06-10",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        stages = {item["stage"]: item for item in payload["stages"]}
        self.assertEqual(stages["live_trading"]["status"], "blocked")
        self.assertFalse(stages["live_trading"]["can_start"])
        self.assertIn("openalgo_not_ready", stages["live_trading"]["blockers"])
        self.assertTrue(payload["openalgo"]["live_trading_enabled"])

    def test_openalgo_monitor_without_credentials_is_safe_failure(self) -> None:
        response = self.client.get("/platform/openalgo/monitor")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "credential_required")
        self.assertTrue(payload["safe_failure"])
        self.assertTrue(payload["no_synthetic_fallback"])

    def test_instrument_routes_without_credentials_are_safe_failures(self) -> None:
        search = self.client.get(
            "/platform/instruments/search",
            params={"query": "NIFTY 26000 CE", "exchange": "NFO"},
        )
        option_symbol = self.client.get(
            "/platform/instruments/optionsymbol",
            params={
                "underlying": "NIFTY",
                "exchange": "NFO",
                "expiry_date": "30DEC25",
                "offset": "ATM",
                "option_type": "CE",
            },
        )

        self.assertEqual(search.status_code, 200)
        self.assertEqual(option_symbol.status_code, 200)
        self.assertEqual(search.json()["status"], "credential_required")
        self.assertEqual(option_symbol.json()["status"], "credential_required")
        self.assertTrue(search.json()["no_synthetic_fallback"])
        self.assertTrue(option_symbol.json()["no_synthetic_fallback"])

    def test_platform_research_context_combines_readiness_and_news(self) -> None:
        response = self.client.get(
            "/platform/research/context",
            params={
                "symbol": "NIFTY",
                "exchange": "NFO",
                "asset_class": "options",
                "interval": "5m",
                "start_date": "2026-04-23",
                "end_date": "2026-05-23",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["readiness"]["local_dataset_exists"])
        self.assertEqual(payload["readiness"]["rows_available"], 10)
        self.assertFalse(payload["news"]["news_configured"])
        self.assertEqual(payload["news"]["articles"], [])
        self.assertTrue(payload["research_not_advice"])
        self.assertTrue(payload["no_synthetic_fallback"])

    def test_research_brief_is_persisted_from_real_context(self) -> None:
        response = self.client.post(
            "/platform/research/briefs",
            json={
                "symbol": "NIFTY",
                "exchange": "NFO",
                "asset_class": "options",
                "interval": "5m",
                "start_date": "2026-04-23",
                "end_date": "2026-05-23",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["brief_id"].startswith("brief_"))
        self.assertEqual(
            payload["evidence"]["dataset_id"],
            "nifty_options",
        )
        self.assertTrue(payload["guards"]["no_synthetic_fallback"])

        latest = self.client.get("/platform/research/briefs").json()
        self.assertEqual(len(latest["briefs"]), 1)
        self.assertEqual(latest["briefs"][0]["brief_id"], payload["brief_id"])

    def test_sandbox_intents_list_is_available_for_operator_ui(self) -> None:
        response = self.client.get("/sandbox/intents")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"intents": []})

    def test_dashboard_preferences_are_persisted_per_user(self) -> None:
        default_response = self.client.get("/platform/dashboard/preferences")

        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(default_response.json()["source"], "default")
        self.assertIn("research", default_response.json()["widgets"])

        update_response = self.client.put(
            "/platform/dashboard/preferences",
            json={
                "widgets": ["news", "research", "news", "openalgo"],
                "auto_refresh": True,
            },
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(
            update_response.json()["widgets"],
            ["news", "research", "openalgo"],
        )
        self.assertTrue(update_response.json()["auto_refresh"])

        stored_response = self.client.get("/platform/dashboard/preferences")
        self.assertEqual(stored_response.status_code, 200)
        self.assertEqual(stored_response.json()["source"], "stored")
        self.assertEqual(
            stored_response.json()["widgets"],
            ["news", "research", "openalgo"],
        )

    def test_dashboard_preferences_reject_unknown_widgets(self) -> None:
        response = self.client.put(
            "/platform/dashboard/preferences",
            json={
                "widgets": ["research", "unsupported_widget"],
                "auto_refresh": False,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_custom_strategy_compile_review_save_edit_flow(self) -> None:
        compile_response = self.client.post(
            "/custom-strategy-specs/compile",
            json={
                "text": (
                    "Create a Reliance 5 minute strategy that buys when EMA 9 "
                    "crosses above EMA 21 and exits when EMA 9 crosses below "
                    "EMA 21 with a 2 percent stop loss"
                )
            },
        )
        self.assertEqual(compile_response.status_code, 200)
        compiled = compile_response.json()
        self.assertTrue(compiled["requires_confirmation"])
        self.assertTrue(compiled["can_execute_without_new_code"])
        spec = compiled["spec"]

        # Nothing was persisted by compilation.
        listed = self.client.get("/custom-strategy-specs").json()
        self.assertEqual(listed["custom_strategy_specs"], [])

        create_response = self.client.post(
            "/custom-strategy-specs",
            json={key: value for key, value in spec.items()},
        )
        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        self.assertEqual(created["status"], "draft_executable")
        spec_id = created["spec_id"]

        fetched = self.client.get(f"/custom-strategy-specs/{spec_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["spec"]["symbol"], "RELIANCE")

        edited = dict(spec)
        edited["risk"] = {"stop_loss_pct": 0.03, "trailing_stop_pct": 0.02}
        update_response = self.client.put(
            f"/custom-strategy-specs/{spec_id}",
            json=edited,
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()
        self.assertEqual(updated["status"], "draft_executable")
        self.assertEqual(
            updated["spec"]["risk"],
            {"stop_loss_pct": 0.03, "trailing_stop_pct": 0.02},
        )

    def test_custom_strategy_update_unknown_spec_is_404(self) -> None:
        response = self.client.put(
            "/custom-strategy-specs/custom_missing",
            json={
                "name": "x",
                "description": "x",
                "symbol": "NIFTY",
                "timeframe": "5m",
                "entry_rules": [
                    {"left": "price", "operator": ">", "right": 1}
                ],
                "exit_rules": [
                    {"left": "price", "operator": "<", "right": 1}
                ],
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_market_news_unconfigured_does_not_fake_articles(self) -> None:
        status = self.client.get("/market-news/status")
        latest = self.client.get("/market-news/latest")

        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["news_configured"])
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["articles"], [])

    def test_failed_backtest_response_says_no_synthetic_fallback(self) -> None:
        response = self.client.post(
            "/backtests",
            json={
                "dataset_id": "missing_dataset",
                "strategy_name": "ema_crossover",
                "parameters": {"fast_period": 9, "slow_period": 21},
            },
        )

        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertTrue(detail["no_synthetic_fallback"])
        self.assertFalse(detail["synthetic_result_created"])
        self.assertEqual(detail["error_type"], "ValueError")

    def _insert_dataset(self) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO raw_file_registry VALUES (
                    'source_1', 'NIFTY_MONTH_E1_5m.zip',
                    'NIFTY_MONTH_E1_5m.zip', 'sha256-test',
                    1024, CURRENT_TIMESTAMP, 10, 10, 0, 0
                )
                """
            )
            con.execute(
                """
                INSERT INTO data_catalog VALUES (
                    'nifty_options', 'market_data', 'options_ohlcv',
                    'NIFTY', 'NFO', '5m', ?, ?, 10,
                    'options_ohlcv', 'source_1',
                    'clean', 'quality.json', CURRENT_TIMESTAMP
                )
                """,
                [
                    datetime(2026, 4, 23, 9, 15),
                    datetime(2026, 4, 23, 10, 0),
                ],
            )
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
