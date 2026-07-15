from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import (
    OfflineOrchestrator,
    grounded_tool_response,
)
from iimc_trading_platform.services.custom_strategy_service import (
    CustomStrategyService,
)
from iimc_trading_platform.strategies.rule_spec import validate_rule_spec
from iimc_trading_platform.tools.registry import build_default_tool_registry


class CustomStrategySpecTest(unittest.TestCase):
    def test_supported_custom_spec_is_persisted_as_draft_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            service = CustomStrategyService(db_path)

            created = service.create_spec(
                name="ema_rsi_momentum",
                description="EMA trend with RSI and momentum filters.",
                symbol="NIFTY",
                timeframe="5m",
                indicators=[
                    {"type": "EMA", "period": 9, "source": "close"},
                    {"type": "EMA", "period": 21, "source": "close"},
                    {"type": "RSI", "period": 14, "source": "close"},
                    {"type": "ROC", "period": 10, "source": "close"},
                ],
                entry_rules=[
                    {
                        "left": "EMA_9",
                        "operator": "crosses_above",
                        "right": "EMA_21",
                    },
                    {"left": "RSI_14", "operator": "<", "right": 60},
                    {"left": "ROC_10", "operator": ">", "right": 0},
                ],
                exit_rules=[
                    {
                        "left": "EMA_9",
                        "operator": "crosses_below",
                        "right": "EMA_21",
                    }
                ],
            )
            listed = service.list_specs()

        self.assertEqual(created["status"], "draft_executable")
        self.assertFalse(created["validation"]["requires_human_review"])
        self.assertEqual(len(listed["custom_strategy_specs"]), 1)
        self.assertEqual(
            listed["custom_strategy_specs"][0]["spec_id"],
            created["spec_id"],
        )

    def test_unsupported_custom_spec_is_saved_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            service = CustomStrategyService(db_path)

            created = service.create_spec(
                name="iv_skew_strategy",
                description="Options IV skew idea requiring unsupported data.",
                symbol="NIFTY",
                timeframe="5m",
                indicators=[
                    {"type": "IV_SKEW", "period": 14, "source": "iv"}
                ],
                entry_rules=[
                    {"left": "IV_SKEW_14", "operator": ">", "right": 0}
                ],
                exit_rules=[
                    {"left": "IV_SKEW_14", "operator": "<=", "right": 0}
                ],
            )

        self.assertEqual(created["status"], "requires_review")
        self.assertTrue(created["validation"]["requires_human_review"])
        missing = {item["kind"] for item in created["missing_capabilities"]}
        self.assertIn("indicator", missing)
        self.assertIn("data_field", missing)

    def test_unsupported_draft_response_names_its_governed_feature_path(self) -> None:
        answer = grounded_tool_response(
            "create_custom_strategy_spec",
            {
                "spec_id": "custom_options_surface",
                "missing_capabilities": [
                    {"kind": "indicator", "value": "IV_SKEW"},
                    {"kind": "data_field", "value": "oi"},
                    {"kind": "indicator", "value": "EARNINGS_FUNDAMENTALS"},
                    {"kind": "indicator", "value": "NEWS_SENTIMENT"},
                ],
            },
        )

        self.assertIn("not executable", answer)
        self.assertIn("/datasets/features", answer)
        self.assertIn("fundamentals feature series", answer)
        self.assertIn("news/sentiment numeric feature series", answer)

    def test_custom_strategy_tool_routes_and_stores_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            registry = build_default_tool_registry(db_path)
            decision = OfflineOrchestrator().select_tool(
                "Create custom strategy using EMA RSI momentum on NIFTY 5m",
                [],
                registry,
            )
            result = registry.call(decision.tool_name, decision.arguments)

        self.assertEqual(decision.tool_name, "create_custom_strategy_spec")
        self.assertEqual(result["status"], "draft_executable")
        self.assertIn("arbitrary LLM-generated code is not executed", result["execution_policy"])

    def test_capability_question_returns_current_rule_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            registry = build_default_tool_registry(db_path)
            decision = OfflineOrchestrator().select_tool(
                "What custom strategy indicators and rules are supported?",
                [],
                registry,
            )
            result = registry.call(decision.tool_name, decision.arguments)

        self.assertEqual(decision.tool_name, "get_custom_strategy_capabilities")
        self.assertIn("MACD", result["supported_indicators"])
        self.assertEqual(result["supported_position_sides"], ["long", "short"])
        self.assertIn(
            "options",
            result["data_contracts"]["rule_backtesting"][
                "supported_asset_classes"
            ],
        )

    def test_common_ohlcv_indicators_are_executable_without_generated_code(self) -> None:
        validation = validate_rule_spec(
            {
                "indicators": [
                    {"name": "BB_MIDDLE", "type": "BB_MIDDLE", "period": 20, "source": "close", "stddev": 2},
                    {"name": "VWAP", "type": "VWAP", "source": "close"},
                    {"name": "ATR_14", "type": "ATR", "period": 14, "source": "close"},
                    {"name": "MACD_LINE", "type": "MACD", "source": "close", "fast_period": 12, "slow_period": 26, "signal_period": 9},
                    {"name": "MACD_SIGNAL", "type": "MACD_SIGNAL", "source": "close", "fast_period": 12, "slow_period": 26, "signal_period": 9},
                ],
                "entry_rules": [
                    {"left": "MACD_LINE", "operator": "crosses_above", "right": "MACD_SIGNAL"},
                    {"left": "price", "operator": ">", "right": "BB_MIDDLE"},
                ],
                "exit_rules": [
                    {"left": "MACD_LINE", "operator": "crosses_below", "right": "MACD_SIGNAL"},
                    {"left": "price", "operator": "<", "right": "VWAP"},
                ],
            }
        )

        self.assertFalse(validation["missing_capabilities"])
        self.assertTrue(validation["can_execute_without_new_code"])
        self.assertIn("MACD", validation["supported_indicators"])
        self.assertIn("VWAP", validation["supported_indicators"])

    def test_supported_custom_spec_runs_native_rule_spec_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            dataset_id = _seed_trending_dataset(db_path)
            service = CustomStrategyService(db_path)
            created = service.create_spec(
                name="ema_breakout",
                description="EMA crossover rule spec.",
                symbol="NIFTY",
                timeframe="5m",
                indicators=[
                    {"type": "EMA", "period": 3, "source": "price"},
                    {"type": "EMA", "period": 8, "source": "price"},
                ],
                entry_rules=[
                    {
                        "left": "EMA_3",
                        "operator": ">",
                        "right": "EMA_8",
                    }
                ],
                exit_rules=[
                    {
                        "left": "EMA_3",
                        "operator": "<",
                        "right": "EMA_8",
                    }
                ],
            )

            result = service.run_backtest(
                spec_id=created["spec_id"],
                dataset_id=dataset_id,
                requested_quantity=1,
            )

        self.assertEqual(result["strategy"], "rule_spec")
        self.assertEqual(result["custom_strategy_spec_id"], created["spec_id"])
        self.assertEqual(result["status"], "completed")
        self.assertIn("no generated code", result["execution_policy"])
        self.assertGreater(result["metrics"]["signal_count"], 0)

    def test_short_custom_spec_uses_sell_to_open_and_buy_to_cover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            dataset_id = _seed_trending_dataset(db_path)
            service = CustomStrategyService(db_path)
            created = service.create_spec(
                name="short_mean_reversion",
                description="Short when price falls below its short average.",
                symbol="NIFTY",
                timeframe="5m",
                position_side="short",
                indicators=[{"type": "SMA", "period": 3, "source": "price"}],
                entry_rules=[
                    {"left": "price", "operator": "<", "right": "SMA_3"}
                ],
                exit_rules=[
                    {"left": "price", "operator": ">", "right": "SMA_3"}
                ],
            )
            result = service.run_backtest(
                spec_id=created["spec_id"],
                dataset_id=dataset_id,
                requested_quantity=1,
            )
            con = connect(db_path)
            try:
                sides = [
                    row[0]
                    for row in con.execute(
                        """
                        SELECT side FROM order_events
                        WHERE run_id = ?
                        ORDER BY created_at, order_id
                        """,
                        [result["run_id"]],
                    ).fetchall()
                ]
            finally:
                con.close()

        self.assertEqual(created["status"], "draft_executable")
        self.assertEqual(created["spec"]["position_side"], "short")
        self.assertEqual(result["status"], "completed")
        self.assertIn("SELL", sides)
        self.assertIn("BUY", sides)

def _seed_trending_dataset(db_path: Path) -> str:
    source_id = "source_custom_strategy"
    dataset_id = "nifty_custom_strategy_5m"
    start = datetime(2026, 1, 1, 9, 15)
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
                source_id,
                "custom.csv",
                "clean",
                timestamp,
            ]
        )

    con = connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO raw_file_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source_id,
                "custom.csv",
                "custom.csv",
                "hash",
                100,
                start,
                len(rows),
                len(rows),
                0,
                0,
            ],
        )
        con.executemany(
            """
            INSERT INTO options_ohlcv VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
        con.execute(
            """
            INSERT INTO data_catalog VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                dataset_id,
                "market_data",
                "options_ohlcv",
                "NIFTY",
                "NFO",
                "5m",
                rows[0][4],
                rows[-1][4],
                len(rows),
                "options_ohlcv",
                source_id,
                "clean",
                None,
                start,
            ],
        )
    finally:
        con.close()
    return dataset_id


if __name__ == "__main__":
    unittest.main()
