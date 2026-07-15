from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iimc_trading_platform.db import connect
from iimc_trading_platform.evaluator import ResponseEvaluator
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.infrastructure.openalgo import OpenAlgoClient
from iimc_trading_platform.infrastructure.openalgo import OpenAlgoResponseError
from iimc_trading_platform.services.openalgo_service import (
    OpenAlgoSnapshotService,
)
from iimc_trading_platform.services.openalgo_readiness_service import (
    OpenAlgoReadinessService,
)
from iimc_trading_platform.orchestration import (
    OfflineOrchestrator,
    OpenAIResponsesOrchestrator,
    OrchestrationDecision,
    ToolInvocation,
)
from iimc_trading_platform.services.chat_service import ChatService
from iimc_trading_platform.tools.registry import (
    DatasetFreshnessInput,
    EmptyInput,
    ToolDefinition,
    ToolRegistry,
    build_default_tool_registry,
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


class _FunctionCall:
    type = "function_call"
    name = "list_datasets"
    arguments = "{}"
    call_id = "provider_call_1"


class _Responses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return type(
                "Response",
                (),
                {
                    "output": [_FunctionCall()],
                    "output_text": "",
                },
            )()
        return type(
            "Response",
            (),
            {
                "output": [],
                "output_text": "Found the governed dataset.",
            },
            )()


class _FakeConversationService:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def ensure_session(self, session_id: str) -> None:
        return None

    def history(self, session_id: str) -> list[dict]:
        return []

    def append(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> str:
        self.messages.append((role, content))
        return f"msg_{len(self.messages)}"


class _FakeToolExecutionService:
    def execute(self, *, tool_name, request, handler, session_id=None):
        return "tool_safety", handler()


class _LyingReadinessOrchestrator:
    mode = "fake_llm"

    def select_tool(self, message, history, registry):
        return OrchestrationDecision("get_execution_readiness", {})

    def compose_response(self, message, decision, tool_result):
        return "Live trading is disabled and OpenAlgo is not configured."


class _MixedCompoundOrchestrator:
    mode = "fake_llm"

    def select_tool(self, message, history, registry):
        invocations = [
            ToolInvocation("read_status", {}),
            ToolInvocation("change_state", {}),
        ]
        return OrchestrationDecision(
            tool_name="read_status",
            arguments={},
            tool_calls=invocations,
        )

    def compose_response(self, message, decision, tool_result):
        return "unused"


class _NullArgumentOrchestrator:
    mode = "fake_llm"

    def select_tool(self, message, history, registry):
        return OrchestrationDecision(
            tool_name="read_status",
            arguments={},
            tool_calls=[ToolInvocation("read_status", None)],
        )

    def compose_response(self, message, decision, tool_result):
        return "unused"


class _InventedDatasetOrchestrator:
    mode = "fake_llm"

    def select_tool(self, message, history, registry):
        return OrchestrationDecision(
            "assess_dataset_freshness",
            {"dataset_id": "current_dataset", "purpose": "current_market"},
        )

    def compose_response(self, message, decision, tool_result):
        return "unused"


class OrchestrationContractsTest(unittest.TestCase):
    def test_openai_tool_schemas_are_strict_objects(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        for tool in registry.openai_tools():
            self._assert_strict_objects(tool["parameters"])

    def test_groq_compatible_tool_schema_preserves_optional_indicator_fields(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        tools = {tool["name"]: tool for tool in registry.openai_tools(strict=False)}
        indicator = tools["create_custom_strategy_spec"]["parameters"]["$defs"]["CustomIndicatorSpec"]

        self.assertEqual(indicator["required"], ["type"])
        self.assertIn("default", indicator["properties"]["period"])

    def test_openai_tool_descriptions_include_governance_context(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        tools = {tool["name"]: tool for tool in registry.openai_tools()}

        description = tools["run_backtest"]["description"]
        self.assertIn("Required role: researcher", description)
        self.assertIn("Capabilities:", description)
        self.assertIn("risk=medium", description)
        self.assertIn("actions=backtest", description)

    def test_tool_roles_are_a_single_authorization_source(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))

        viewer = registry.allowed_for_role("viewer")
        researcher = registry.allowed_for_role("researcher")

        self.assertIn("list_datasets", viewer)
        self.assertNotIn("run_backtest", viewer)
        self.assertIn("run_backtest", researcher)
        self.assertIn("run_robustness_experiment", researcher)

    def test_resume_facing_tools_expose_capability_metadata(self) -> None:
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="configured",
        )
        tools = {tool["name"]: tool for tool in registry.list_tools()}

        backtest = tools["run_backtest"]["capabilities"]
        self.assertIn("backtest", backtest["actions"])
        self.assertIn("historical_ohlcv", backtest["required_data"])
        self.assertEqual(backtest["risk_level"], "medium")

        live = tools["prepare_live_order_intent"]["capabilities"]
        self.assertIn("live", live["execution_modes"])
        self.assertTrue(live["requires_approval"])
        self.assertIn("openalgo", live["required_providers"])

    def test_null_tool_arguments_validate_as_empty_payload(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))

        empty_payload = registry.get("get_platform_summary").validate(None)
        self.assertEqual(empty_payload.model_dump(), {})

        with self.assertRaises(Exception):
            registry.get("get_dataset_detail").validate(None)

    def test_evaluator_replaces_incorrect_signed_metric(self) -> None:
        result = {
            "run_id": "run_1",
            "net_pnl": -100.0,
            "max_drawdown": 150.0,
            "return_pct": -0.01,
            "total_trades": 2,
        }
        evaluated = ResponseEvaluator().evaluate(
            answer=(
                "net pnl: 100; max drawdown: 150; "
                "return pct: 0.01; total trades: 2"
            ),
            tool_name="run_backtest",
            tool_result=result,
            tool_call_id="tool_1",
        )
        self.assertFalse(evaluated.passed)
        self.assertIn("ungrounded_metric:net_pnl", evaluated.warnings)
        self.assertIn("Net P&L: -100.0", evaluated.answer)

    def test_evaluator_replaces_incorrect_nested_metric_alias(self) -> None:
        result = {
            "run_id": "run_alias",
            "net_pnl": 42.0,
            "max_drawdown": 10.0,
            "return_pct": 0.5,
            "total_trades": 3,
            "metrics": {"sharpe_ratio": -0.25},
        }
        evaluated = ResponseEvaluator().evaluate(
            answer=(
                "Net P&L: 42; max drawdown: 10; return: 0.5; "
                "closed trades: 3; Sharpe: 2.5"
            ),
            tool_name="run_backtest",
            tool_result=result,
            tool_call_id="tool_alias",
        )

        self.assertFalse(evaluated.passed)
        self.assertIn("ungrounded_metric:sharpe_ratio", evaluated.warnings)
        self.assertIn("Backtest run_alias completed", evaluated.answer)

    def test_chat_service_grounds_safety_critical_tool_responses(self) -> None:
        readiness_result = {
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "stages": [
                {"stage": "research", "can_start": True},
                {"stage": "backtest", "can_start": False},
            ],
            "next_blocker": {
                "stage": "backtest",
                "next_action": "Ingest real historical data.",
            },
            "no_synthetic_fallback": True,
        }
        registry = ToolRegistry(
            [
                ToolDefinition(
                    name="get_execution_readiness",
                    description="Readiness",
                    input_model=EmptyInput,
                    handler=lambda value: readiness_result,
                    side_effects="read-only",
                    retry_safe=True,
                )
            ]
        )
        service = ChatService(
            registry,
            _FakeToolExecutionService(),
            _LyingReadinessOrchestrator(),
            _FakeConversationService(),
        )

        result = service.answer("Can we live trade BTCUSDT?")

        self.assertEqual(result.intent, "get_execution_readiness")
        self.assertIn("Execution readiness for BTCUSDT crypto checked", result.answer)
        self.assertIn("Next blocker: backtest", result.answer)
        self.assertNotIn("Live trading is disabled", result.answer)

    def test_chat_rejects_compound_requests_that_include_a_write(self) -> None:
        registry = ToolRegistry(
            [
                ToolDefinition(
                    name="read_status",
                    description="Read status",
                    input_model=EmptyInput,
                    handler=lambda value: {"status": "ok"},
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="change_state",
                    description="Change state",
                    input_model=EmptyInput,
                    handler=lambda value: (_ for _ in ()).throw(
                        AssertionError("state-changing tool must not run")
                    ),
                    side_effects="creates a trading artifact",
                    retry_safe=False,
                ),
            ]
        )
        service = ChatService(
            registry,
            _FakeToolExecutionService(),
            _MixedCompoundOrchestrator(),
            _FakeConversationService(),
        )

        result = service.answer("Check status and change state")

        self.assertEqual(result.intent, "compound_request_rejected")
        self.assertEqual(result.tool_calls, [])
        self.assertIn("only read-only checks", result.answer)

    def test_chat_normalizes_null_provider_arguments_for_empty_tools(self) -> None:
        registry = ToolRegistry(
            [
                ToolDefinition(
                    name="read_status",
                    description="Read status",
                    input_model=EmptyInput,
                    handler=lambda value: {"status": "ok"},
                    side_effects="read-only database query",
                    retry_safe=True,
                )
            ]
        )
        service = ChatService(
            registry,
            _FakeToolExecutionService(),
            _NullArgumentOrchestrator(),
            _FakeConversationService(),
        )

        result = service.answer("Check status")

        self.assertEqual(result.intent, "read_status")
        self.assertEqual(result.tool_calls[0].status, "succeeded")

    def test_responses_orchestrator_uses_function_call_output(self) -> None:
        orchestrator = OpenAIResponsesOrchestrator(
            "test-key",
            "gpt-5.5",
        )
        responses = _Responses()
        orchestrator.client = type(
            "Client",
            (),
            {"responses": responses},
        )()
        registry = build_default_tool_registry(Path("unused.duckdb"))

        decision = orchestrator.select_tool(
            "What data is available?",
            [],
            registry,
        )
        answer = orchestrator.compose_response(
            "What data is available?",
            decision,
            {"datasets": []},
        )

        self.assertEqual(decision.tool_name, "list_datasets")
        self.assertEqual(answer, "Found the governed dataset.")
        output = responses.calls[1]["input"][-1]
        self.assertEqual(output["type"], "function_call_output")
        self.assertEqual(output["call_id"], "provider_call_1")

    def test_offline_router_prepares_sandbox_intent_only_with_decision_id(self) -> None:
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="configured",
        )
        decision = OfflineOrchestrator().select_tool(
            (
                "Prepare paper order for risk_abc123 BUY 2 NIFTY NFO "
                "MIS market strategy ema_crossover"
            ),
            [],
            registry,
        )

        self.assertEqual(decision.tool_name, "prepare_sandbox_order_intent")
        self.assertEqual(decision.arguments["decision_id"], "risk_abc123")
        self.assertEqual(decision.arguments["symbol"], "NIFTY")
        self.assertEqual(decision.arguments["exchange"], "NFO")
        self.assertEqual(decision.arguments["side"], "BUY")
        self.assertEqual(decision.arguments["quantity"], 2)
        self.assertEqual(decision.arguments["product"], "MIS")
        self.assertEqual(decision.arguments["order_type"], "MARKET")

    def test_offline_router_requests_dataset_id_for_conversational_freshness_question(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))

        decision = OfflineOrchestrator().select_tool(
            "Is this dataset fresh for current market use?",
            [],
            registry,
        )

        self.assertIsNone(decision.tool_name)
        self.assertEqual(decision.arguments, {})
        self.assertIn("dataset_id", decision.direct_response or "")

    def test_offline_router_parses_dataset_after_on_dataset_phrase(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))

        decision = OfflineOrchestrator().select_tool(
            "Backtest custom strategy spec custom_alpha on dataset nifty_options",
            [],
            registry,
        )

        self.assertEqual(decision.tool_name, "run_custom_strategy_spec")
        self.assertEqual(decision.arguments["dataset_id"], "nifty_options")

    def test_chat_rejects_model_invented_record_identifier(self) -> None:
        registry = ToolRegistry(
            [
                ToolDefinition(
                    name="assess_dataset_freshness",
                    description="Assess dataset freshness.",
                    input_model=DatasetFreshnessInput,
                    handler=lambda value: {"status": "fresh"},
                    side_effects="read-only",
                    retry_safe=True,
                )
            ]
        )
        service = ChatService(
            registry,
            _FakeToolExecutionService(),
            _InventedDatasetOrchestrator(),
            _FakeConversationService(),
        )

        result = service.answer("Is this dataset fresh for current market use?")

        self.assertEqual(result.intent, "clarification")
        self.assertEqual(result.tool_calls, [])
        self.assertIn("dataset_id", result.answer)

    def test_offline_router_refuses_to_prepare_sandbox_intent_without_decision_id(self) -> None:
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="configured",
        )
        decision = OfflineOrchestrator().select_tool(
            "Prepare a paper order for NIFTY",
            [],
            registry,
        )

        self.assertIsNone(decision.tool_name)
        self.assertIn("approved risk decision_id", decision.direct_response)
        self.assertIn("cannot approve or submit", decision.direct_response)

    def test_offline_router_parses_generic_readiness_request(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))

        decision = OfflineOrchestrator().select_tool(
            (
                "Can we live trade BTCUSDT crypto 1 hour from "
                "2026-06-01 to 2026-06-10, what is blocked?"
            ),
            [],
            registry,
        )

        self.assertEqual(decision.tool_name, "get_execution_readiness")
        self.assertEqual(decision.arguments["symbol"], "BTCUSDT")
        self.assertEqual(decision.arguments["asset_class"], "crypto")
        self.assertEqual(decision.arguments["exchange"], "CRYPTO")
        self.assertEqual(decision.arguments["interval"], "1h")
        self.assertEqual(decision.arguments["start_date"], "2026-06-01")
        self.assertEqual(decision.arguments["end_date"], "2026-06-10")

    def test_offline_router_names_and_generalizes_custom_strategy_specs(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))

        decision = OfflineOrchestrator().select_tool(
            (
                "Create custom strategy called breakout_gold using EMA and "
                "RSI for gold 15 minutes"
            ),
            [],
            registry,
        )

        self.assertEqual(decision.tool_name, "create_custom_strategy_spec")
        self.assertEqual(decision.arguments["name"], "breakout_gold")
        self.assertEqual(decision.arguments["symbol"], "GOLD")
        self.assertEqual(decision.arguments["timeframe"], "15m")
        indicator_types = {
            indicator["type"]
            for indicator in decision.arguments["indicators"]
        }
        self.assertEqual(indicator_types, {"EMA", "RSI"})

    def test_offline_router_maps_named_feature_dataset_to_rule_spec(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))

        decision = OfflineOrchestrator().select_tool(
            (
                "Create custom strategy called sentiment_reversal using news "
                "sentiment feature dataset reliance_news_features for RELIANCE "
                "5 minutes"
            ),
            [],
            registry,
        )

        self.assertEqual(decision.tool_name, "create_custom_strategy_spec")
        self.assertEqual(decision.arguments["indicators"], [])
        self.assertEqual(
            decision.arguments["feature_inputs"],
            [
                {
                    "name": "news_sentiment",
                    "dataset_id": "reliance_news_features",
                    "feature_name": "news_sentiment",
                    "alignment": "asof",
                    "max_age_hours": 24.0,
                }
            ],
        )
        self.assertEqual(
            decision.arguments["entry_rules"][0]["left"],
            "news_sentiment",
        )

    @patch("iimc_trading_platform.infrastructure.openalgo.urlopen")
    def test_openalgo_client_proves_analyzer_mode_before_order(
        self,
        mocked_urlopen,
    ) -> None:
        mocked_urlopen.side_effect = [
            _FakeResponse(
                {
                    "status": "success",
                    "data": {
                        "analyze_mode": True,
                        "mode": "analyze",
                    },
                }
            ),
            _FakeResponse(
                {
                    "status": "success",
                    "mode": "analyze",
                    "orderid": "sandbox_1",
                }
            ),
        ]
        client = OpenAlgoClient("http://127.0.0.1:5000", "secret")

        result = client.place_sandbox_order(
            strategy="IIMC",
            symbol="NHPC",
            action="BUY",
            exchange="NSE",
            price_type="MARKET",
            product="MIS",
            quantity=1,
        )

        self.assertEqual(result["orderid"], "sandbox_1")
        urls = [
            call.args[0].full_url
            for call in mocked_urlopen.call_args_list
        ]
        self.assertEqual(
            urls,
            [
                "http://127.0.0.1:5000/api/v1/analyzer",
                "http://127.0.0.1:5000/api/v1/placeorder",
            ],
        )

    @patch("iimc_trading_platform.infrastructure.openalgo.urlopen")
    def test_openalgo_client_refuses_live_mode(
        self,
        mocked_urlopen,
    ) -> None:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "status": "success",
                "data": {
                    "analyze_mode": False,
                    "mode": "live",
                },
            }
        )
        client = OpenAlgoClient("http://127.0.0.1:5000", "secret")

        with self.assertRaisesRegex(
            OpenAlgoResponseError,
            "analyzer mode",
        ):
            client.place_sandbox_order(
                strategy="IIMC",
                symbol="NHPC",
                action="BUY",
                exchange="NSE",
                price_type="MARKET",
                product="MIS",
                quantity=1,
            )
        self.assertEqual(mocked_urlopen.call_count, 1)

    @patch(
        "iimc_trading_platform.infrastructure.openalgo.urlopen",
        return_value=_FakeResponse(
            {
                "status": "success",
                "data": {"availablecash": "1000.00"},
            }
        ),
    )
    def test_openalgo_snapshot_is_sanitized_and_persisted(
        self,
        mocked_urlopen,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.duckdb"
            initialize_database(db_path)
            service = OpenAlgoSnapshotService(
                db_path,
                OpenAlgoClient("http://127.0.0.1:5000", "secret"),
            )
            result = service.capture("funds")

            request = mocked_urlopen.call_args.args[0]
            self.assertIn(b'"apikey": "secret"', request.data)
            con = connect(db_path)
            try:
                stored = con.execute(
                    """
                    SELECT snapshot_type, payload_json
                    FROM openalgo_snapshots
                    WHERE snapshot_id = ?
                    """,
                    [result["snapshot_id"]],
                ).fetchone()
            finally:
                con.close()
            self.assertEqual(stored[0], "funds")
            history = service.list()
            self.assertTrue(history["configured"])
            self.assertEqual(
                history["snapshots"][0]["snapshot_id"],
                result["snapshot_id"],
            )
            self.assertNotIn("secret", stored[1])
            self.assertEqual(
                json.loads(stored[1]),
                {"availablecash": "1000.00"},
            )

    @patch("iimc_trading_platform.infrastructure.openalgo.urlopen")
    def test_openalgo_readiness_probes_quote_and_history(
        self,
        mocked_urlopen,
    ) -> None:
        mocked_urlopen.side_effect = [
            _FakeResponse(
                {
                    "status": "success",
                    "data": {"analyze_mode": True, "mode": "analyze"},
                }
            ),
            _FakeResponse({"status": "success", "data": {"cash": 1000}}),
            _FakeResponse({"status": "success", "data": []}),
            _FakeResponse({"status": "success", "data": []}),
            _FakeResponse({"status": "success", "data": []}),
            _FakeResponse(
                {
                    "status": "success",
                    "data": {
                        "symbol": "RELIANCE",
                        "exchange": "NSE",
                        "lotsize": 1,
                    },
                }
            ),
            _FakeResponse({"status": "success", "data": {"ltp": 100.0}}),
            _FakeResponse(
                {
                    "status": "success",
                    "data": [
                        {
                            "timestamp": "2026-06-01T09:15:00",
                            "close": 100.0,
                        }
                    ],
                }
            ),
        ]
        service = OpenAlgoReadinessService(
            AppConfig(openalgo_api_key="secret")
        )

        result = service.readiness(
            symbol="RELIANCE",
            exchange="NSE",
            asset_class="equity",
            interval="5m",
            start_date="2026-06-01",
            end_date="2026-06-02",
        )

        self.assertTrue(result["quote_available"])
        self.assertTrue(result["historical_available"])
        self.assertEqual(result["quote_status"]["item_count"], 1)
        self.assertEqual(result["historical_status"]["item_count"], 1)
        urls = [
            call.args[0].full_url
            for call in mocked_urlopen.call_args_list
        ]
        self.assertEqual(
            urls[-3:],
            [
                "http://127.0.0.1:5000/api/v1/symbol",
                "http://127.0.0.1:5000/api/v1/quotes",
                "http://127.0.0.1:5000/api/v1/history",
            ],
        )

    @patch("iimc_trading_platform.infrastructure.openalgo.urlopen")
    def test_openalgo_search_and_option_symbol_endpoints(
        self,
        mocked_urlopen,
    ) -> None:
        mocked_urlopen.side_effect = [
            _FakeResponse(
                {
                    "status": "success",
                    "message": "Found 1 matching symbols",
                    "data": [
                        {
                            "symbol": "NIFTY30DEC2526000CE",
                            "exchange": "NFO",
                            "instrumenttype": "CE",
                            "expiry": "30-DEC-25",
                            "strike": 26000,
                            "lotsize": 65,
                        }
                    ],
                }
            ),
            _FakeResponse(
                {
                    "status": "success",
                    "symbol": "NIFTY30DEC2525950CE",
                    "exchange": "NFO",
                    "lotsize": 65,
                    "tick_size": 5,
                    "freeze_qty": 1800,
                    "underlying_ltp": 25966.4,
                }
            ),
        ]
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="secret",
        )

        search = registry.call(
            "search_instruments",
            {"query": "NIFTY 26000 CE", "exchange": "NFO"},
        )
        resolved = registry.call(
            "resolve_option_symbol",
            {
                "underlying": "NIFTY",
                "exchange": "NFO",
                "expiry_date": "30DEC25",
                "offset": "ATM",
                "option_type": "CE",
            },
        )

        self.assertEqual(search["match_count"], 1)
        self.assertEqual(
            search["matches"][0]["symbol"],
            "NIFTY30DEC2526000CE",
        )
        self.assertEqual(
            resolved["resolved_symbol"],
            "NIFTY30DEC2525950CE",
        )
        self.assertEqual(resolved["underlying_exchange"], "NSE_INDEX")
        payloads = [
            json.loads(call.args[0].data.decode("utf-8"))
            for call in mocked_urlopen.call_args_list
        ]
        self.assertEqual(payloads[0]["query"], "NIFTY 26000 CE")
        self.assertEqual(payloads[1]["exchange"], "NSE_INDEX")

    def _assert_strict_objects(self, node) -> None:
        if isinstance(node, dict):
            self.assertNotIn("default", node)
            if isinstance(node.get("properties"), dict):
                self.assertFalse(node.get("additionalProperties", True))
                self.assertEqual(
                    set(node["required"]),
                    set(node["properties"]),
                )
            for value in node.values():
                self._assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                self._assert_strict_objects(value)


if __name__ == "__main__":
    unittest.main()
