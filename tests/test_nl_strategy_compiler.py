from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import (
    OfflineOrchestrator,
    grounded_tool_response,
)
from iimc_trading_platform.strategies.nl_compiler import compile_strategy_text
from iimc_trading_platform.strategies.rule_spec import (
    RuleSpecStrategy,
    validate_rule_spec,
)
from iimc_trading_platform.tools.registry import build_default_tool_registry


RELIANCE_EMA_TEXT = (
    "Create a Reliance 5 minute strategy that buys when EMA 9 crosses above "
    "EMA 21 and exits when EMA 9 crosses below EMA 21 with a 2 percent stop "
    "loss"
)


class NlStrategyCompilerTest(unittest.TestCase):
    def test_reliance_ema_crossover_compiles_to_executable_spec(self) -> None:
        compiled = compile_strategy_text(RELIANCE_EMA_TEXT)
        spec = compiled["spec"]
        validation = validate_rule_spec(spec)

        self.assertEqual(spec["symbol"], "RELIANCE")
        self.assertEqual(spec["timeframe"], "5m")
        self.assertEqual(spec["position_side"], "long")
        self.assertEqual(spec["risk"], {"stop_loss_pct": 0.02})
        self.assertEqual(spec["feature_inputs"], [])
        self.assertEqual(
            spec["entry_rules"],
            [
                {
                    "left": "EMA_9",
                    "operator": "crosses_above",
                    "right": "EMA_21",
                    "joiner": "AND",
                }
            ],
        )
        self.assertEqual(
            spec["exit_rules"],
            [
                {
                    "left": "EMA_9",
                    "operator": "crosses_below",
                    "right": "EMA_21",
                    "joiner": "AND",
                }
            ],
        )
        self.assertEqual(compiled["unparsed_clauses"], [])
        self.assertEqual(validation["missing_capabilities"], [])
        self.assertTrue(validation["can_execute_without_new_code"])

    def test_user_specified_periods_are_honored(self) -> None:
        compiled = compile_strategy_text(
            "Create a TCS 15 minute strategy that buys when the 13 EMA "
            "crosses above the 48 EMA and exits when the 13 EMA crosses "
            "below the 48 EMA"
        )
        periods = sorted(
            indicator["period"]
            for indicator in compiled["spec"]["indicators"]
        )
        self.assertEqual(periods, [13, 48])

    def test_rich_indicator_and_risk_vocabulary(self) -> None:
        compiled = compile_strategy_text(
            "Build a TCS 15 minute strategy that buys when RSI 14 is below "
            "30 and price is above VWAP, exits when RSI is above 70, with a "
            "1.5 percent stop loss, 3 percent take profit, 2% trailing stop "
            "and quantity 10"
        )
        spec = compiled["spec"]
        self.assertEqual(
            spec["risk"],
            {
                "stop_loss_pct": 0.015,
                "take_profit_pct": 0.03,
                "trailing_stop_pct": 0.02,
                "max_position_size": 10,
            },
        )
        self.assertEqual(
            spec["entry_rules"],
            [
                {"left": "RSI_14", "operator": "<", "right": 30.0, "joiner": "AND"},
                {"left": "price", "operator": ">", "right": "VWAP", "joiner": "AND"},
            ],
        )
        self.assertEqual(compiled["unparsed_clauses"], [])
        self.assertEqual(
            validate_rule_spec(spec)["missing_capabilities"], []
        )

    def test_session_window_and_short_side(self) -> None:
        compiled = compile_strategy_text(
            "Create a NIFTY strategy that goes short when price falls below "
            "the lower bollinger band between 9:30 and 14:30 and covers "
            "when price rises above the middle band"
        )
        spec = compiled["spec"]
        self.assertEqual(spec["position_side"], "short")
        self.assertEqual(spec["session"], {"start": "09:30", "end": "14:30"})
        self.assertEqual(
            validate_rule_spec(spec)["missing_capabilities"], []
        )

    def test_external_features_only_when_explicitly_requested(self) -> None:
        plain = compile_strategy_text(RELIANCE_EMA_TEXT)
        self.assertEqual(plain["spec"]["feature_inputs"], [])

        with_news = compile_strategy_text(
            "Create a strategy for HDFCBANK that buys when news sentiment "
            "is above 0.5 and exits when news sentiment is below 0"
        )
        feature_inputs = with_news["spec"]["feature_inputs"]
        self.assertEqual(len(feature_inputs), 1)
        self.assertEqual(feature_inputs[0]["name"], "news_sentiment")
        kinds = {
            item["kind"]
            for item in validate_rule_spec(with_news["spec"])[
                "missing_capabilities"
            ]
        }
        self.assertIn("feature_dataset", kinds)

    def test_unparseable_clauses_are_reported_not_guessed(self) -> None:
        compiled = compile_strategy_text("buy Reliance when it goes up")
        self.assertEqual(compiled["spec"]["entry_rules"], [])
        self.assertIn("it goes up", compiled["unparsed_clauses"])
        kinds = {
            item["kind"]
            for item in validate_rule_spec(compiled["spec"])[
                "missing_capabilities"
            ]
        }
        self.assertEqual(kinds, {"entry_rule", "exit_rule"})

    def test_invalid_risk_and_session_values_fail_validation(self) -> None:
        validation = validate_rule_spec(
            {
                "indicators": [
                    {"type": "EMA", "period": 9, "source": "close"}
                ],
                "entry_rules": [
                    {"left": "price", "operator": ">", "right": "EMA_9"}
                ],
                "exit_rules": [
                    {"left": "price", "operator": "<", "right": "EMA_9"}
                ],
                "risk": {"stop_loss_pct": 1.5},
                "session": {"start": "15:00", "end": "09:15"},
            }
        )
        kinds = {
            item["kind"] for item in validation["missing_capabilities"]
        }
        self.assertEqual(kinds, {"risk_control", "session"})


class RuleSpecRuntimeExtensionsTest(unittest.TestCase):
    def test_trailing_stop_exits_after_pullback_from_peak(self) -> None:
        spec = {
            "symbol": "TEST",
            "entry_rules": [
                {"left": "price", "operator": ">", "right": 1}
            ],
            "exit_rules": [
                {"left": "price", "operator": "<", "right": 0}
            ],
            "risk": {"trailing_stop_pct": 0.05},
            "position_side": "long",
        }
        strategy = RuleSpecStrategy()
        parameters = strategy.validate_parameters({"spec": spec})
        prices = [100.0, 100.0, 104.0, 110.0, 104.0, 103.0]
        signals = strategy.generate(_candles(prices), parameters)

        self.assertEqual(
            [signal.signal_type for signal in signals[:2]],
            ["entry", "exit"],
        )
        self.assertEqual(signals[1].price, 104.0)
        self.assertIn("trailing stop", signals[1].reason)

    def test_session_filter_blocks_entries_outside_window(self) -> None:
        spec = {
            "symbol": "TEST",
            "entry_rules": [
                {"left": "price", "operator": ">", "right": 1}
            ],
            "exit_rules": [
                {"left": "price", "operator": "<", "right": 0}
            ],
            "session": {"start": "10:00", "end": "11:00"},
            "position_side": "long",
        }
        strategy = RuleSpecStrategy()
        parameters = strategy.validate_parameters({"spec": spec})
        start = datetime(2026, 1, 5, 9, 15)
        candles = _candles(
            [100.0] * 12,
            start=start,
            step_minutes=15,
        )
        signals = strategy.generate(candles, parameters)

        entries = [
            signal for signal in signals if signal.signal_type == "entry"
        ]
        self.assertTrue(entries)
        self.assertGreaterEqual(entries[0].timestamp.strftime("%H:%M"), "10:00")


class CompilePreviewChatFlowTest(unittest.TestCase):
    def test_chat_creation_request_previews_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            registry = build_default_tool_registry(db_path)

            decision = OfflineOrchestrator().select_tool(
                RELIANCE_EMA_TEXT,
                [],
                registry,
            )
            self.assertEqual(
                decision.tool_name, "compile_custom_strategy_spec"
            )
            result = registry.call(decision.tool_name, decision.arguments)

        self.assertTrue(result["requires_confirmation"])
        self.assertTrue(result["can_execute_without_new_code"])
        answer = grounded_tool_response(
            "compile_custom_strategy_spec", result
        )
        self.assertIn("NOT been saved", answer)
        self.assertIn("EMA_9 crosses above EMA_21", answer)
        self.assertIn("stop loss 2%", answer)

    def test_compile_tool_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            registry = build_default_tool_registry(db_path)
            tool = registry.get("compile_custom_strategy_spec")
        self.assertTrue(tool.is_read_only)


def _candles(
    prices: list[float],
    *,
    start: datetime | None = None,
    step_minutes: int = 5,
) -> list[dict[str, object]]:
    base = start or datetime(2026, 1, 5, 9, 15)
    return [
        {
            "timestamp": base + timedelta(minutes=step_minutes * index),
            "symbol": "TEST",
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1000.0,
            "price": price,
        }
        for index, price in enumerate(prices)
    ]


if __name__ == "__main__":
    unittest.main()
