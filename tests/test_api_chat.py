from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.custom_strategy_service import (
    CustomStrategyService,
)

from _harness import AppHarness


class ApiChatTest(unittest.TestCase):
    # The app is built once per class (~4.2s) and the database is reset
    # between tests, which is equivalent to a fresh database per test but
    # ~40x cheaper. See tests/_harness.py.
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = AppHarness()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness.close()

    def setUp(self) -> None:
        self.db_path = self.harness.db_path
        self.artifacts_dir = self.harness.artifacts_dir
        # Some tests build a second app with their own config overrides.
        self.temp_dir = self.harness.temp_dir
        self.client = self.harness.client
        self.harness.reset(seed=self._insert_dataset)

    def test_chat_dataset_question_calls_catalog_tool_and_returns_evidence(self) -> None:
        response = self.client.post(
            "/chat",
            json={
                "session_id": "session_test",
                "message": "What NIFTY datasets are available?",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "list_datasets")
        self.assertIn("nifty_options", payload["answer"])
        self.assertEqual(len(payload["tool_calls"]), 1)

        tool_call_id = payload["tool_calls"][0]["tool_call_id"]
        con = connect(self.db_path)
        try:
            stored = con.execute(
                """
                SELECT tool_name, status, session_id
                FROM tool_calls
                WHERE tool_call_id = ?
                """,
                [tool_call_id],
            ).fetchone()
            audit_actions = [
                row[0]
                for row in con.execute(
                    """
                    SELECT action
                    FROM audit_events
                    WHERE entity_type = 'tool_call'
                      AND entity_id = ?
                    ORDER BY created_at, audit_id
                    """,
                    [tool_call_id],
                ).fetchall()
            ]
        finally:
            con.close()

        self.assertEqual(stored, ("list_datasets", "succeeded", "session_test"))
        self.assertEqual(audit_actions, ["started", "succeeded"])

    def test_chat_can_run_compound_read_only_catalog_question(self) -> None:
        response = self.client.post(
            "/chat",
            json={
                "session_id": "session_compound",
                "message": "What datasets and strategies are available?",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "multi_tool")
        self.assertEqual(
            [item["tool_name"] for item in payload["tool_calls"]],
            ["list_datasets", "list_strategies"],
        )
        self.assertTrue(
            all(item["status"] == "succeeded" for item in payload["tool_calls"])
        )
        self.assertIn("nifty_options", payload["answer"])
        self.assertIn("deterministic strategy plugins", payload["answer"])
        self.assertIn("list_datasets", payload["data"]["tool_results"])
        self.assertIn("list_strategies", payload["data"]["tool_results"])

    def test_chat_unsupported_request_does_not_call_tool(self) -> None:
        response = self.client.post(
            "/chat",
            json={"message": "Can you place a live order immediately?"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "unsupported")
        self.assertEqual(payload["tool_calls"], [])
        self.assertIn("prepare a live order intent", payload["answer"])
        self.assertIn("explicit human approval", payload["answer"])
        self.assertEqual(payload["orchestration_mode"], "offline_fallback")
        self.assertTrue(payload["evaluation"]["passed"])

    def test_chat_can_inspect_paper_trading_intents(self) -> None:
        response = self.client.post(
            "/chat",
            json={"message": "Show paper trading OpenAlgo intents"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "list_sandbox_intents")
        self.assertIn("No OpenAlgo sandbox", payload["answer"])
        self.assertEqual(payload["data"], {"intents": []})

    def test_chat_can_return_combined_research_context(self) -> None:
        response = self.client.post(
            "/chat",
            json={
                "message": (
                    "Give market research context for NIFTY NFO options 5m"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "get_research_context")
        self.assertTrue(payload["data"]["readiness"]["local_dataset_exists"])
        self.assertEqual(payload["data"]["news"]["articles"], [])
        self.assertTrue(payload["answer"])

    def test_chat_can_return_platform_summary(self) -> None:
        response = self.client.post(
            "/chat",
            json={"message": "Give me the platform summary and capabilities"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "get_platform_summary")
        self.assertIn("asset_coverage", payload["data"])
        self.assertIn("execution_paths", payload["data"])
        self.assertIn("Platform status", payload["answer"])

    def test_chat_can_return_execution_readiness(self) -> None:
        response = self.client.post(
            "/chat",
            json={"message": "Can we paper trade NIFTY options, what is blocked?"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "get_execution_readiness")
        stages = {item["stage"]: item for item in payload["data"]["stages"]}
        self.assertTrue(stages["backtest"]["can_start"])
        self.assertFalse(stages["paper_trading"]["can_start"])
        self.assertIn("Next blocker", payload["answer"])

    def test_chat_can_prepare_analyzer_ready_paper_order_intent(self) -> None:
        # Explicitly exercises the opt-out (no-approval) configuration.
        self._insert_approved_semi_auto_risk_decision()
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=Path(self.temp_dir.name),
                    openalgo_api_key="configured",
                    require_paper_approval=False,
                )
            )
        )

        response = client.post(
            "/chat",
            json={
                "message": (
                    "Prepare paper order for risk_chat BUY 2 NIFTY NFO "
                    "MIS market strategy ema_crossover"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "prepare_sandbox_order_intent")
        self.assertEqual(payload["data"]["decision_id"], "risk_chat")
        self.assertEqual(payload["data"]["status"], "approved")
        self.assertIn("Prepared sandbox order intent", payload["answer"])

        con = connect(self.db_path)
        try:
            stored = con.execute(
                """
                SELECT i.status, i.approval_id,
                       COUNT(a.approval_id) AS approval_count
                FROM order_intents AS i
                LEFT JOIN approval_requests AS a
                  ON a.approval_id = i.approval_id
                WHERE i.decision_id = 'risk_chat'
                GROUP BY i.status, i.approval_id
                """
            ).fetchone()
        finally:
            con.close()
        self.assertEqual(
            stored,
            (
                "approved",
                None,
                0,
            ),
        )

    def test_default_config_requires_human_approval_for_paper(self) -> None:
        self._insert_approved_semi_auto_risk_decision()
        client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts_dir,
                    openalgo_root=Path(self.temp_dir.name),
                    openalgo_api_key="configured",
                )
            )
        )

        response = client.post(
            "/chat",
            json={
                "message": (
                    "Prepare paper order for risk_chat BUY 2 NIFTY NFO "
                    "MIS market strategy ema_crossover"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["status"], "pending_approval")
        self.assertIsNotNone(payload["data"]["approval_id"])

        con = connect(self.db_path)
        try:
            approval_count = con.execute(
                """
                SELECT COUNT(*)
                FROM approval_requests
                WHERE status = 'pending'
                """
            ).fetchone()[0]
        finally:
            con.close()
        self.assertGreaterEqual(approval_count, 1)

    def test_chat_can_create_persisted_research_brief(self) -> None:
        response = self.client.post(
            "/chat",
            json={"message": "Create a research brief for NIFTY NFO options 5m"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "create_research_brief")
        self.assertTrue(payload["data"]["brief_id"].startswith("brief_"))
        self.assertTrue(payload["data"]["guards"]["no_synthetic_fallback"])

        con = connect(self.db_path)
        try:
            stored = con.execute(
                """
                SELECT COUNT(*)
                FROM research_briefs
                WHERE brief_id = ?
                """,
                [payload["data"]["brief_id"]],
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(stored, 1)

    def test_chat_can_run_persisted_custom_strategy_spec_backtest(self) -> None:
        created = CustomStrategyService(self.db_path).create_spec(
            name="chat_custom_ema",
            description="EMA crossover created before chat execution.",
            symbol="NIFTY",
            timeframe="5m",
            indicators=[
                {"type": "EMA", "period": 3, "source": "price"},
                {"type": "EMA", "period": 8, "source": "price"},
            ],
            entry_rules=[
                {"left": "EMA_3", "operator": ">", "right": "EMA_8"}
            ],
            exit_rules=[
                {"left": "EMA_3", "operator": "<", "right": "EMA_8"}
            ],
        )

        response = self.client.post(
            "/chat",
            json={
                "message": (
                    f"Backtest custom strategy spec {created['spec_id']} "
                    "on dataset nifty_options"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "run_custom_strategy_spec")
        self.assertEqual(
            payload["data"]["custom_strategy_spec_id"],
            created["spec_id"],
        )
        self.assertEqual(payload["data"]["strategy"], "rule_spec")
        self.assertEqual(payload["data"]["status"], "completed")
        self.assertIn("No generated code", payload["answer"])

    def test_datasets_endpoint_returns_tool_evidence(self) -> None:
        response = self.client.get("/datasets")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("tool_call_id", payload)
        self.assertEqual(payload["datasets"][0]["dataset_id"], "nifty_options")

    def test_workspace_and_runs_endpoint_are_available(self) -> None:
        workspace = self.client.get("/")
        self.assertEqual(workspace.status_code, 200)
        self.assertIn("Trading Research Workspace", workspace.text)

        runs = self.client.get("/runs")
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(runs.json(), {"runs": []})

        snapshots = self.client.get("/openalgo/snapshots")
        self.assertEqual(snapshots.status_code, 200)
        self.assertEqual(
            snapshots.json(),
            {"configured": False, "snapshots": []},
        )

    def _insert_dataset(self) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO raw_file_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "source_1",
                    "chat.csv",
                    "chat.csv",
                    "hash",
                    100,
                    datetime(2026, 4, 23, 9, 15),
                    16,
                    16,
                    0,
                    0,
                ],
            )
            con.execute(
                """
                INSERT INTO data_catalog VALUES (
                    'nifty_options', 'market_data', 'options_ohlcv',
                    'NIFTY', 'NFO', '5m', ?, ?, 66080,
                    'options_ohlcv', 'source_1',
                    'clean_with_warnings', 'quality.json', CURRENT_TIMESTAMP
                )
                """,
                [
                    datetime(2026, 4, 23, 9, 15),
                    datetime(2026, 5, 22, 15, 25),
                ],
            )
            con.execute(
                """
                INSERT INTO data_quality_reports VALUES (
                    'run_1', 'source_1', 'nifty_options', 'quality.json',
                    69262, 66080, 66080, 3182, 0, 90,
                    'clean_with_warnings', CURRENT_TIMESTAMP
                )
                """
            )
            prices = [
                100,
                99,
                98,
                97,
                98,
                100,
                103,
                106,
                109,
                111,
                110,
                108,
                105,
                102,
                99,
                97,
            ]
            rows = []
            start = datetime(2026, 4, 23, 9, 15)
            for index, price in enumerate(prices):
                timestamp = start + timedelta(minutes=5 * index)
                rows.append(
                    [
                        "NIFTY",
                        "NFO",
                        "MONTH_E1",
                        "5m",
                        timestamp,
                        "ATM",
                        25_000.0,
                        "CALL",
                        price - 1,
                        price + 1,
                        price - 2,
                        price,
                        1000 + index,
                        5000,
                        15.0,
                        price,
                        "source_1",
                        "chat.csv",
                        "clean",
                        timestamp,
                    ]
                )
            con.executemany(
                """
                INSERT INTO options_ohlcv VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                rows,
            )
        finally:
            con.close()

    def _insert_approved_semi_auto_risk_decision(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO strategy_signals VALUES (
                    'sig_chat', 'run_chat', ?, 'NIFTY', 'entry', 'long',
                    0.9, 'test entry signal', '{}', ?
                )
                """,
                [now, now],
            )
            con.execute(
                """
                INSERT INTO risk_decisions VALUES (
                    'risk_chat', 'run_chat', 'sig_chat', TRUE, 5, 5,
                    'approved for paper workflow', ?, CURRENT_TIMESTAMP,
                    'risk_policy_v1'
                )
                """,
                [
                    json.dumps(
                        {
                            "execution_mode_check": {"mode": "semi_auto"},
                            "quantity_check": {"approved_quantity": 5},
                            "symbol_check": {"symbol": "NIFTY"},
                        },
                        sort_keys=True,
                    )
                ],
            )
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
