from __future__ import annotations

from typing import Any

from ..domain import SignalDirection
from .base import RawSignal, StrategyPlugin


SUPPORTED_INDICATORS = {"EMA", "SMA", "RSI", "ROC"}
SUPPORTED_OPERATORS = {
    ">",
    "<",
    ">=",
    "<=",
    "==",
    "crosses_above",
    "crosses_below",
}
SUPPORTED_DATA_FIELDS = {"open", "high", "low", "close", "volume", "price"}


class RuleSpecStrategy(StrategyPlugin):
    name = "rule_spec"
    version = "1.0.0"
    description = (
        "Governed no-code rule-spec strategy using supported indicators, "
        "boolean entry/exit rules, and optional stop/take-profit controls."
    )
    parameter_schema: dict[str, dict[str, Any]] = {}

    def validate_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        spec = parameters.get("spec")
        if not isinstance(spec, dict):
            raise ValueError("rule_spec requires a spec object")
        validation = validate_rule_spec(spec)
        if validation["missing_capabilities"]:
            raise ValueError(
                "rule_spec cannot execute unsupported capabilities: "
                f"{validation['missing_capabilities']}"
            )
        return {"spec": spec, "validation": validation}

    def generate(
        self,
        candles: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> list[RawSignal]:
        spec = parameters["spec"]
        validation = parameters["validation"]
        warmup = validation["warmup_bars"]
        _require_candles(candles, warmup + 2)

        refs = _build_reference_series(candles, spec)
        entry_rules = spec["entry_rules"]
        exit_rules = spec["exit_rules"]
        risk = spec.get("risk") or {}
        stop_loss_pct = risk.get("stop_loss_pct")
        take_profit_pct = risk.get("take_profit_pct")

        signals: list[RawSignal] = []
        in_position = False
        entry_price = 0.0

        for index in range(max(1, warmup), len(candles)):
            price = float(candles[index]["price"])
            stop_hit = (
                in_position
                and stop_loss_pct is not None
                and price <= entry_price * (1 - float(stop_loss_pct))
            )
            target_hit = (
                in_position
                and take_profit_pct is not None
                and price >= entry_price * (1 + float(take_profit_pct))
            )

            if (
                not in_position
                and _rules_pass(entry_rules, refs, index)
            ):
                in_position = True
                entry_price = price
                signals.append(
                    _signal(
                        candles[index],
                        "entry",
                        SignalDirection.LONG,
                        _rule_confidence(entry_rules, refs, index),
                        "custom rule-spec entry conditions passed",
                        _rule_features(entry_rules, refs, index),
                    )
                )
            elif in_position and (
                stop_hit
                or target_hit
                or _rules_pass(exit_rules, refs, index)
            ):
                in_position = False
                signals.append(
                    _signal(
                        candles[index],
                        "exit",
                        SignalDirection.EXIT,
                        1.0,
                        (
                            "custom rule-spec stop loss reached"
                            if stop_hit
                            else (
                                "custom rule-spec take profit reached"
                                if target_hit
                                else "custom rule-spec exit conditions passed"
                            )
                        ),
                        _rule_features(exit_rules, refs, index),
                    )
                )

        _force_exit(candles, signals, in_position)
        return signals


def validate_rule_spec(spec: dict[str, Any]) -> dict[str, Any]:
    missing: list[dict[str, str]] = []
    indicators = list(spec.get("indicators") or [])
    entry_rules = list(spec.get("entry_rules") or [])
    exit_rules = list(spec.get("exit_rules") or [])
    refs = set(SUPPORTED_DATA_FIELDS)
    max_period = 1

    if not indicators:
        missing.append(
            {
                "kind": "indicator",
                "value": "none",
                "reason": "At least one indicator is required.",
            }
        )
    if not entry_rules:
        missing.append(
            {
                "kind": "entry_rule",
                "value": "none",
                "reason": "At least one entry rule is required.",
            }
        )
    if not exit_rules:
        missing.append(
            {
                "kind": "exit_rule",
                "value": "none",
                "reason": "At least one exit rule is required.",
            }
        )

    for indicator in indicators:
        indicator_type = str(indicator.get("type", "")).upper()
        source = str(indicator.get("source", "close")).lower()
        period = int(indicator.get("period") or 1)
        if indicator_type not in SUPPORTED_INDICATORS:
            missing.append(
                {
                    "kind": "indicator",
                    "value": indicator_type,
                    "reason": "Indicator is not supported by the rule-spec runtime.",
                }
            )
        if source not in SUPPORTED_DATA_FIELDS:
            missing.append(
                {
                    "kind": "data_field",
                    "value": source,
                    "reason": "Required source field is not in supported OHLCV fields.",
                }
            )
        if indicator_type in SUPPORTED_INDICATORS and period < 1:
            missing.append(
                {
                    "kind": "indicator_period",
                    "value": str(period),
                    "reason": "Indicator period must be positive.",
                }
            )
        max_period = max(max_period, period)
        refs.add(_indicator_ref(indicator))

    for rule in [*entry_rules, *exit_rules]:
        operator = str(rule.get("operator", "")).lower()
        if operator not in SUPPORTED_OPERATORS:
            missing.append(
                {
                    "kind": "operator",
                    "value": operator,
                    "reason": "Rule operator is not supported by the rule-spec runtime.",
                }
            )
        for side in ("left", "right"):
            value = rule.get(side)
            if isinstance(value, str) and not _is_numeric(value):
                ref = value.lower() if value.lower() in SUPPORTED_DATA_FIELDS else value
                if ref not in refs:
                    missing.append(
                        {
                            "kind": "rule_reference",
                            "value": value,
                            "reason": "Rule references an unknown indicator or data field.",
                        }
                    )

    return {
        "well_formed": not missing,
        "supported_indicators": sorted(SUPPORTED_INDICATORS),
        "supported_operators": sorted(SUPPORTED_OPERATORS),
        "supported_data_fields": sorted(SUPPORTED_DATA_FIELDS),
        "indicator_refs": sorted(ref for ref in refs if ref not in SUPPORTED_DATA_FIELDS),
        "missing_capabilities": missing,
        "requires_human_review": bool(missing),
        "can_execute_without_new_code": not missing,
        "warmup_bars": max_period,
    }


def _build_reference_series(
    candles: list[dict[str, Any]],
    spec: dict[str, Any],
) -> dict[str, list[float]]:
    refs: dict[str, list[float]] = {
        field: [float(candle.get(field, candle["price"])) for candle in candles]
        for field in SUPPORTED_DATA_FIELDS
    }
    refs["close"] = [float(candle.get("close", candle["price"])) for candle in candles]
    refs["price"] = [float(candle["price"]) for candle in candles]

    for indicator in spec.get("indicators") or []:
        indicator_type = str(indicator["type"]).upper()
        source = str(indicator.get("source", "close")).lower()
        period = int(indicator.get("period") or 1)
        source_values = refs[source]
        if indicator_type == "EMA":
            values = _ema(source_values, period)
        elif indicator_type == "SMA":
            values = _sma(source_values, period)
        elif indicator_type == "RSI":
            values = _rsi(source_values, period)
        elif indicator_type == "ROC":
            values = _roc(source_values, period)
        else:
            raise ValueError(f"Unsupported indicator: {indicator_type}")
        refs[_indicator_ref(indicator)] = values
    return refs


def _rules_pass(
    rules: list[dict[str, Any]],
    refs: dict[str, list[float]],
    index: int,
) -> bool:
    result: bool | None = None
    for rule in rules:
        passed = _eval_rule(rule, refs, index)
        joiner = str(rule.get("joiner", "AND")).upper()
        if result is None:
            result = passed
        elif joiner == "OR":
            result = result or passed
        else:
            result = result and passed
    return bool(result)


def _eval_rule(
    rule: dict[str, Any],
    refs: dict[str, list[float]],
    index: int,
) -> bool:
    left = _value(rule["left"], refs, index)
    right = _value(rule["right"], refs, index)
    operator = str(rule["operator"]).lower()
    if operator == ">":
        return left > right
    if operator == "<":
        return left < right
    if operator == ">=":
        return left >= right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return abs(left - right) <= 1e-9
    if operator == "crosses_above":
        return _value(rule["left"], refs, index - 1) <= _value(
            rule["right"], refs, index - 1
        ) and left > right
    if operator == "crosses_below":
        return _value(rule["left"], refs, index - 1) >= _value(
            rule["right"], refs, index - 1
        ) and left < right
    raise ValueError(f"Unsupported operator: {operator}")


def _value(value: Any, refs: dict[str, list[float]], index: int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _is_numeric(value):
        return float(value)
    key = str(value)
    key = key.lower() if key.lower() in SUPPORTED_DATA_FIELDS else key
    return float(refs[key][index])


def _rule_confidence(
    rules: list[dict[str, Any]],
    refs: dict[str, list[float]],
    index: int,
) -> float:
    if not rules:
        return 0.0
    passed = sum(1 for rule in rules if _eval_rule(rule, refs, index))
    return passed / len(rules)


def _rule_features(
    rules: list[dict[str, Any]],
    refs: dict[str, list[float]],
    index: int,
) -> dict[str, float]:
    features: dict[str, float] = {}
    for rule in rules:
        for side in ("left", "right"):
            value = rule.get(side)
            if isinstance(value, str) and not _is_numeric(value):
                key = value.lower() if value.lower() in SUPPORTED_DATA_FIELDS else value
                if key in refs:
                    features[key] = refs[key][index]
    return features


def _indicator_ref(indicator: dict[str, Any]) -> str:
    if indicator.get("name"):
        return str(indicator["name"])
    indicator_type = str(indicator.get("type", "")).upper()
    period = indicator.get("period")
    return f"{indicator_type}_{period}" if period is not None else indicator_type


def _signal(
    candle: dict[str, Any],
    signal_type: str,
    direction: SignalDirection,
    confidence: float,
    reason: str,
    features: dict[str, Any],
) -> RawSignal:
    return RawSignal(
        timestamp=candle["timestamp"],
        symbol=str(candle["symbol"]),
        signal_type=signal_type,
        direction=direction,
        price=float(candle["price"]),
        confidence=max(0.0, min(1.0, float(confidence))),
        reason=reason,
        features={key: round(float(value), 6) for key, value in features.items()},
    )


def _force_exit(
    candles: list[dict[str, Any]],
    signals: list[RawSignal],
    in_position: bool,
) -> None:
    if in_position:
        signals.append(
            _signal(
                candles[-1],
                "exit",
                SignalDirection.EXIT,
                1.0,
                "forced square-off at end of dataset",
                {},
            )
        )


def _require_candles(candles: list[dict[str, Any]], minimum: int) -> None:
    if len(candles) < minimum:
        raise ValueError(f"Need at least {minimum} candles, got {len(candles)}")


def _is_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _ema(prices: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1)
    values = [prices[0]]
    for price in prices[1:]:
        values.append(price * alpha + values[-1] * (1 - alpha))
    return values


def _sma(prices: list[float], period: int) -> list[float]:
    values = [prices[0]] * len(prices)
    running_sum = 0.0
    for index, price in enumerate(prices):
        running_sum += price
        if index >= period:
            running_sum -= prices[index - period]
        window = min(index + 1, period)
        values[index] = running_sum / window
    return values


def _rsi(prices: list[float], period: int) -> list[float]:
    if len(prices) <= period:
        return [50.0] * len(prices)
    values = [50.0] * len(prices)
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = prices[index] - prices[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    for index in range(period, len(prices)):
        if index > period:
            change = prices[index] - prices[index - 1]
            average_gain = (
                average_gain * (period - 1) + max(change, 0.0)
            ) / period
            average_loss = (
                average_loss * (period - 1) + max(-change, 0.0)
            ) / period
        relative_strength = (
            average_gain / average_loss
            if average_loss > 1e-12
            else float("inf")
        )
        values[index] = (
            100.0
            if relative_strength == float("inf")
            else 100 - (100 / (1 + relative_strength))
        )
    return values


def _roc(prices: list[float], period: int) -> list[float]:
    values = [0.0] * len(prices)
    for index in range(period, len(prices)):
        base = prices[index - period]
        values[index] = (prices[index] - base) / base if base else 0.0
    return values
