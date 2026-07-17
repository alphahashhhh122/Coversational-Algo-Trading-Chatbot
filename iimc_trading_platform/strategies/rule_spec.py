from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from ..domain import SignalDirection
from .base import RawSignal, StrategyPlugin


SUPPORTED_INDICATORS = {
    "ATR",
    "BB_LOWER",
    "BB_MIDDLE",
    "BB_UPPER",
    "EMA",
    "MACD",
    "MACD_SIGNAL",
    "ROC",
    "RSI",
    "SMA",
    "VWAP",
}
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
_FEATURE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")


def rule_spec_capabilities() -> dict[str, Any]:
    return {
        "supported_indicators": sorted(SUPPORTED_INDICATORS),
        "supported_operators": sorted(SUPPORTED_OPERATORS),
        "supported_data_fields": sorted(SUPPORTED_DATA_FIELDS),
        "supported_position_sides": ["long", "short"],
        "supported_risk_controls": [
            "max_position_size",
            "stop_loss_pct",
            "take_profit_pct",
            "trailing_stop_pct",
        ],
        "supported_session_filter": {
            "supported": True,
            "contract": {
                "start": "HH:MM entry window open (exchange local time)",
                "end": "HH:MM entry window close; exits are never blocked",
            },
        },
        "external_feature_inputs": {
            "supported": True,
            "contract": {
                "name": "strategy reference name",
                "dataset_id": "governed feature-series dataset",
                "feature_name": "stored numeric feature name",
                "alignment": "asof",
                "max_age_hours": "positive point-in-time freshness limit",
            },
            "examples": ["iv_skew", "open_interest", "earnings_surprise", "news_sentiment"],
        },
        "execution_policy": (
            "Native deterministic rule-spec execution only; unsupported "
            "primitives are preserved for review and never executed as code."
        ),
    }


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
        session = spec.get("session") or None
        position_side = str(spec.get("position_side", "long")).lower()
        stop_loss_pct = risk.get("stop_loss_pct")
        take_profit_pct = risk.get("take_profit_pct")
        trailing_stop_pct = risk.get("trailing_stop_pct")

        signals: list[RawSignal] = []
        in_position = False
        entry_price = 0.0
        extreme_price = 0.0

        for index in range(max(1, warmup), len(candles)):
            price = float(candles[index]["price"])
            if in_position:
                extreme_price = (
                    max(extreme_price, price)
                    if position_side == "long"
                    else min(extreme_price, price)
                )
            stop_hit = (
                in_position
                and stop_loss_pct is not None
                and (
                    price <= entry_price * (1 - float(stop_loss_pct))
                    if position_side == "long"
                    else price >= entry_price * (1 + float(stop_loss_pct))
                )
            )
            target_hit = (
                in_position
                and take_profit_pct is not None
                and (
                    price >= entry_price * (1 + float(take_profit_pct))
                    if position_side == "long"
                    else price <= entry_price * (1 - float(take_profit_pct))
                )
            )
            trail_hit = (
                in_position
                and trailing_stop_pct is not None
                and (
                    price <= extreme_price * (1 - float(trailing_stop_pct))
                    if position_side == "long"
                    else price >= extreme_price * (1 + float(trailing_stop_pct))
                )
            )

            if (
                not in_position
                and _in_session(candles[index]["timestamp"], session)
                and _rules_pass(entry_rules, refs, index)
            ):
                in_position = True
                entry_price = price
                extreme_price = price
                signals.append(
                    _signal(
                        candles[index],
                        "entry",
                        (
                            SignalDirection.SHORT
                            if position_side == "short"
                            else SignalDirection.LONG
                        ),
                        _rule_confidence(entry_rules, refs, index),
                        "custom rule-spec entry conditions passed",
                        _rule_features(entry_rules, refs, index),
                    )
                )
            elif in_position and (
                stop_hit
                or target_hit
                or trail_hit
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
                                else (
                                    "custom rule-spec trailing stop reached"
                                    if trail_hit
                                    else "custom rule-spec exit conditions passed"
                                )
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
    seen_refs: set[str] = set()
    feature_inputs = _feature_inputs(spec, missing)
    for feature in feature_inputs:
        reference = feature.get("name")
        if not isinstance(reference, str):
            continue
        if reference in seen_refs or reference in SUPPORTED_DATA_FIELDS:
            missing.append(
                {
                    "kind": "feature_reference",
                    "value": reference,
                    "reason": "Feature input names must be unique and not shadow OHLCV fields.",
                }
            )
            continue
        seen_refs.add(reference)
        refs.add(reference)

    position_side = str(spec.get("position_side", "long")).lower()
    if position_side not in {"long", "short"}:
        missing.append(
            {
                "kind": "position_side",
                "value": position_side,
                "reason": "position_side must be either long or short.",
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
        source = _reference_name(indicator.get("source", "close"))
        period = int(indicator.get("period") or 1)
        fast_period = int(indicator.get("fast_period") or 12)
        slow_period = int(indicator.get("slow_period") or 26)
        signal_period = int(indicator.get("signal_period") or 9)
        if indicator_type not in SUPPORTED_INDICATORS:
            missing.append(
                {
                    "kind": "indicator",
                    "value": indicator_type,
                    "reason": "Indicator is not supported by the rule-spec runtime.",
                }
            )
        if source not in refs:
            missing.append(
                {
                    "kind": "data_field",
                    "value": source,
                    "reason": "Required source field is not an OHLCV field or declared feature input.",
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
        if indicator_type in {"MACD", "MACD_SIGNAL"}:
            if fast_period >= slow_period:
                missing.append(
                    {
                        "kind": "indicator_period",
                        "value": f"{fast_period}/{slow_period}",
                        "reason": "MACD fast_period must be smaller than slow_period.",
                    }
                )
            max_period = max(max_period, slow_period + signal_period)
        else:
            max_period = max(max_period, period)
        reference = _indicator_ref(indicator)
        if reference in seen_refs:
            missing.append(
                {
                    "kind": "indicator_reference",
                    "value": reference,
                    "reason": "Indicator references must be unique.",
                }
            )
        seen_refs.add(reference)
        refs.add(reference)

    _validate_risk(spec.get("risk") or {}, missing)
    _validate_session(spec.get("session"), missing)

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
                ref = _reference_name(value)
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
        "external_feature_inputs": feature_inputs,
        "supported_position_sides": ["long", "short"],
        "indicator_refs": sorted(ref for ref in refs if ref not in SUPPORTED_DATA_FIELDS),
        "missing_capabilities": missing,
        "requires_human_review": bool(missing),
        "can_execute_without_new_code": not missing,
        "warmup_bars": max_period,
    }


_SESSION_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_risk(
    risk: Any,
    missing: list[dict[str, str]],
) -> None:
    if not isinstance(risk, dict):
        missing.append(
            {
                "kind": "risk",
                "value": str(risk),
                "reason": "risk must be an object of numeric risk controls.",
            }
        )
        return
    bounds = {
        "stop_loss_pct": (0.0, 1.0),
        "take_profit_pct": (0.0, 10.0),
        "trailing_stop_pct": (0.0, 1.0),
    }
    for field, (low, high) in bounds.items():
        value = risk.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = math.nan
        if not math.isfinite(numeric) or not low < numeric <= high:
            missing.append(
                {
                    "kind": "risk_control",
                    "value": f"{field}={value}",
                    "reason": (
                        f"{field} must be a fraction greater than {low} and "
                        f"at most {high} (for example 0.02 for 2%)."
                    ),
                }
            )
    size = risk.get("max_position_size")
    if size is not None and (
        not isinstance(size, (int, float))
        or int(size) != size
        or int(size) < 1
    ):
        missing.append(
            {
                "kind": "risk_control",
                "value": f"max_position_size={size}",
                "reason": "max_position_size must be a positive whole number.",
            }
        )


def _validate_session(
    session: Any,
    missing: list[dict[str, str]],
) -> None:
    if session is None:
        return
    valid = (
        isinstance(session, dict)
        and isinstance(session.get("start"), str)
        and isinstance(session.get("end"), str)
        and _SESSION_TIME_PATTERN.fullmatch(session["start"]) is not None
        and _SESSION_TIME_PATTERN.fullmatch(session["end"]) is not None
        and session["start"] < session["end"]
    )
    if not valid:
        missing.append(
            {
                "kind": "session",
                "value": str(session),
                "reason": (
                    "session requires start and end times in HH:MM format "
                    "with start earlier than end."
                ),
            }
        )


def _in_session(timestamp: Any, session: dict[str, str] | None) -> bool:
    if not session:
        return True
    clock = _clock_of(timestamp)
    if clock is None:
        return True
    return session["start"] <= clock <= session["end"]


def _clock_of(timestamp: Any) -> str | None:
    if hasattr(timestamp, "strftime"):
        return timestamp.strftime("%H:%M")
    text = str(timestamp)
    match = re.search(r"[T\s](\d{2}:\d{2})", text)
    return match.group(1) if match else None


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
    for feature in spec.get("feature_inputs") or []:
        if not isinstance(feature, dict):
            continue
        name = feature.get("name")
        if not isinstance(name, str):
            continue
        refs[name] = [
            float(candle.get("features", {}).get(name, math.nan))
            for candle in candles
        ]

    for indicator in spec.get("indicators") or []:
        indicator_type = str(indicator["type"]).upper()
        source = _reference_name(indicator.get("source", "close"))
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
        elif indicator_type == "ATR":
            values = _atr(candles, period)
        elif indicator_type == "VWAP":
            values = _vwap(candles)
        elif indicator_type in {"BB_UPPER", "BB_MIDDLE", "BB_LOWER"}:
            middle = _sma(source_values, period)
            deviation = _rolling_stddev(source_values, period)
            multiplier = float(indicator.get("stddev") or 2.0)
            if indicator_type == "BB_UPPER":
                values = [
                    mean + multiplier * spread
                    for mean, spread in zip(middle, deviation)
                ]
            elif indicator_type == "BB_LOWER":
                values = [
                    mean - multiplier * spread
                    for mean, spread in zip(middle, deviation)
                ]
            else:
                values = middle
        elif indicator_type in {"MACD", "MACD_SIGNAL"}:
            fast_period = int(indicator.get("fast_period") or 12)
            slow_period = int(indicator.get("slow_period") or 26)
            signal_period = int(indicator.get("signal_period") or 9)
            line = [
                fast - slow
                for fast, slow in zip(
                    _ema(source_values, fast_period),
                    _ema(source_values, slow_period),
                )
            ]
            values = line if indicator_type == "MACD" else _ema(line, signal_period)
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
    key = _reference_name(value)
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
                key = _reference_name(value)
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


def _reference_name(value: Any) -> str:
    reference = str(value)
    return (
        reference.lower()
        if reference.lower() in SUPPORTED_DATA_FIELDS
        else reference
    )


def _feature_inputs(
    spec: dict[str, Any],
    missing: list[dict[str, str]],
) -> list[dict[str, Any]]:
    raw_inputs = spec.get("feature_inputs") or []
    if not isinstance(raw_inputs, list):
        missing.append(
            {
                "kind": "feature_inputs",
                "value": str(raw_inputs),
                "reason": "feature_inputs must be a list of governed feature mappings.",
            }
        )
        return []
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_inputs):
        if not isinstance(raw, dict):
            missing.append(
                {
                    "kind": "feature_input",
                    "value": str(raw),
                    "reason": "Each feature input must be an object.",
                }
            )
            continue
        name = raw.get("name")
        dataset_id = raw.get("dataset_id")
        feature_name = raw.get("feature_name")
        alignment = str(raw.get("alignment", "asof")).lower()
        max_age_hours = raw.get("max_age_hours")
        if not isinstance(name, str) or not _FEATURE_REFERENCE_PATTERN.fullmatch(name):
            missing.append(
                {
                    "kind": "feature_name",
                    "value": str(name),
                    "reason": "Feature input name must be an identifier using letters, numbers, and underscores.",
                }
            )
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            missing.append(
                {
                    "kind": "feature_dataset",
                    "value": str(dataset_id),
                    "reason": "Feature inputs require a governed dataset_id.",
                }
            )
        if not isinstance(feature_name, str) or not _FEATURE_REFERENCE_PATTERN.fullmatch(feature_name):
            missing.append(
                {
                    "kind": "feature_source",
                    "value": str(feature_name),
                    "reason": "feature_name must identify a stored numeric feature.",
                }
            )
        if alignment != "asof":
            missing.append(
                {
                    "kind": "feature_alignment",
                    "value": alignment,
                    "reason": "Only point-in-time asof feature alignment is supported.",
                }
            )
        try:
            normalized_age = float(max_age_hours)
        except (TypeError, ValueError):
            normalized_age = 0.0
        if not math.isfinite(normalized_age) or normalized_age <= 0:
            missing.append(
                {
                    "kind": "feature_freshness",
                    "value": str(max_age_hours),
                    "reason": "Feature inputs require a positive max_age_hours limit.",
                }
            )
        normalized.append(
            {
                "name": name,
                "dataset_id": dataset_id,
                "feature_name": feature_name,
                "alignment": alignment,
                "max_age_hours": normalized_age,
                "index": index,
            }
        )
    return normalized


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


def _atr(candles: list[dict[str, Any]], period: int) -> list[float]:
    ranges: list[float] = []
    previous_close = float(candles[0].get("close", candles[0]["price"]))
    for candle in candles:
        high = float(candle.get("high", candle["price"]))
        low = float(candle.get("low", candle["price"]))
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = float(candle.get("close", candle["price"]))
    result = [0.0] * len(ranges)
    if len(ranges) < period:
        return result
    result[period - 1] = sum(ranges[:period]) / period
    for index in range(period, len(ranges)):
        result[index] = (result[index - 1] * (period - 1) + ranges[index]) / period
    return result


def _vwap(candles: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    cumulative_value = 0.0
    cumulative_volume = 0.0
    previous_date: date | None = None
    for candle in candles:
        timestamp = candle.get("timestamp")
        if timestamp is not None:
            candle_date = (
                timestamp.date()
                if hasattr(timestamp, "date")
                else None
            )
            if candle_date is not None and candle_date != previous_date:
                cumulative_value = 0.0
                cumulative_volume = 0.0
                previous_date = candle_date
        typical_price = (
            float(candle.get("high", candle["price"]))
            + float(candle.get("low", candle["price"]))
            + float(candle.get("close", candle["price"]))
        ) / 3
        volume = max(0.0, float(candle.get("volume", 0.0)))
        cumulative_value += typical_price * volume
        cumulative_volume += volume
        values.append(
            cumulative_value / cumulative_volume
            if cumulative_volume > 0
            else typical_price
        )
    return values


def _rolling_stddev(values: list[float], period: int) -> list[float]:
    result: list[float] = []
    for index in range(len(values)):
        window = values[max(0, index - period + 1) : index + 1]
        mean = sum(window) / len(window)
        result.append((sum((value - mean) ** 2 for value in window) / len(window)) ** 0.5)
    return result
