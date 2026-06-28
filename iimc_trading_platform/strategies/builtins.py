from __future__ import annotations

from typing import Any

from ..domain import SignalDirection
from .base import RawSignal, StrategyPlugin


class EMACrossoverStrategy(StrategyPlugin):
    name = "ema_crossover"
    version = "1.0.0"
    description = "Long-only EMA crossover with percentage stop loss."
    parameter_schema = {
        "fast_period": {
            "type": "integer",
            "default": 9,
            "minimum": 2,
            "maximum": 100,
        },
        "slow_period": {
            "type": "integer",
            "default": 21,
            "minimum": 3,
            "maximum": 300,
        },
        "stop_loss_pct": {
            "type": "number",
            "default": 0.02,
            "minimum": 0.001,
            "maximum": 0.25,
        },
    }

    def validate_cross_parameters(self, parameters: dict[str, Any]) -> None:
        if parameters["fast_period"] >= parameters["slow_period"]:
            raise ValueError("fast_period must be smaller than slow_period")

    def generate(
        self,
        candles: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> list[RawSignal]:
        fast_period = parameters["fast_period"]
        slow_period = parameters["slow_period"]
        stop_loss_pct = parameters["stop_loss_pct"]
        _require_candles(candles, slow_period + 1)

        prices = [float(candle["price"]) for candle in candles]
        fast_values = _ema(prices, fast_period)
        slow_values = _ema(prices, slow_period)
        signals: list[RawSignal] = []
        in_position = False
        entry_price = 0.0

        for index in range(slow_period, len(candles)):
            previous = index - 1
            crossed_up = (
                fast_values[previous] <= slow_values[previous]
                and fast_values[index] > slow_values[index]
            )
            crossed_down = (
                fast_values[previous] >= slow_values[previous]
                and fast_values[index] < slow_values[index]
            )
            stop_hit = (
                in_position
                and prices[index] <= entry_price * (1 - stop_loss_pct)
            )

            if crossed_up and not in_position:
                in_position = True
                entry_price = prices[index]
                signals.append(
                    _signal(
                        candles[index],
                        signal_type="entry",
                        direction=SignalDirection.LONG,
                        confidence=_spread_confidence(
                            fast_values[index],
                            slow_values[index],
                        ),
                        reason=(
                            f"EMA{fast_period} crossed above EMA{slow_period}"
                        ),
                        features={
                            "fast_ema": fast_values[index],
                            "slow_ema": slow_values[index],
                        },
                    )
                )
            elif in_position and (crossed_down or stop_hit):
                in_position = False
                signals.append(
                    _signal(
                        candles[index],
                        signal_type="exit",
                        direction=SignalDirection.EXIT,
                        confidence=1.0,
                        reason=(
                            "stop loss reached"
                            if stop_hit
                            else (
                                f"EMA{fast_period} crossed below "
                                f"EMA{slow_period}"
                            )
                        ),
                        features={
                            "fast_ema": fast_values[index],
                            "slow_ema": slow_values[index],
                        },
                    )
                )

        _force_exit(candles, signals, in_position)
        return signals


class SMACrossoverStrategy(StrategyPlugin):
    name = "sma_crossover"
    version = "1.0.0"
    description = "Long-only simple moving-average crossover."
    parameter_schema = {
        "fast_period": {
            "type": "integer",
            "default": 10,
            "minimum": 2,
            "maximum": 100,
        },
        "slow_period": {
            "type": "integer",
            "default": 30,
            "minimum": 3,
            "maximum": 300,
        },
        "stop_loss_pct": {
            "type": "number",
            "default": 0.02,
            "minimum": 0.001,
            "maximum": 0.25,
        },
    }

    def validate_cross_parameters(self, parameters: dict[str, Any]) -> None:
        if parameters["fast_period"] >= parameters["slow_period"]:
            raise ValueError("fast_period must be smaller than slow_period")

    def generate(
        self,
        candles: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> list[RawSignal]:
        fast_period = parameters["fast_period"]
        slow_period = parameters["slow_period"]
        stop_loss_pct = parameters["stop_loss_pct"]
        _require_candles(candles, slow_period + 1)

        prices = [float(candle["price"]) for candle in candles]
        fast_values = _sma(prices, fast_period)
        slow_values = _sma(prices, slow_period)
        signals: list[RawSignal] = []
        in_position = False
        entry_price = 0.0

        for index in range(slow_period, len(candles)):
            previous = index - 1
            crossed_up = (
                fast_values[previous] <= slow_values[previous]
                and fast_values[index] > slow_values[index]
            )
            crossed_down = (
                fast_values[previous] >= slow_values[previous]
                and fast_values[index] < slow_values[index]
            )
            stop_hit = (
                in_position
                and prices[index] <= entry_price * (1 - stop_loss_pct)
            )

            if crossed_up and not in_position:
                in_position = True
                entry_price = prices[index]
                signals.append(
                    _signal(
                        candles[index],
                        "entry",
                        SignalDirection.LONG,
                        _spread_confidence(
                            fast_values[index],
                            slow_values[index],
                        ),
                        f"SMA{fast_period} crossed above SMA{slow_period}",
                        {
                            "fast_sma": fast_values[index],
                            "slow_sma": slow_values[index],
                        },
                    )
                )
            elif in_position and (crossed_down or stop_hit):
                in_position = False
                signals.append(
                    _signal(
                        candles[index],
                        "exit",
                        SignalDirection.EXIT,
                        1.0,
                        (
                            "stop loss reached"
                            if stop_hit
                            else (
                                f"SMA{fast_period} crossed below "
                                f"SMA{slow_period}"
                            )
                        ),
                        {
                            "fast_sma": fast_values[index],
                            "slow_sma": slow_values[index],
                        },
                    )
                )

        _force_exit(candles, signals, in_position)
        return signals


class RSIMeanReversionStrategy(StrategyPlugin):
    name = "rsi_mean_reversion"
    version = "1.0.0"
    description = "Long-only RSI oversold/overbought mean reversion."
    parameter_schema = {
        "period": {
            "type": "integer",
            "default": 14,
            "minimum": 2,
            "maximum": 100,
        },
        "oversold": {
            "type": "number",
            "default": 30.0,
            "minimum": 1.0,
            "maximum": 49.0,
        },
        "overbought": {
            "type": "number",
            "default": 70.0,
            "minimum": 51.0,
            "maximum": 99.0,
        },
    }

    def validate_cross_parameters(self, parameters: dict[str, Any]) -> None:
        if parameters["oversold"] >= parameters["overbought"]:
            raise ValueError("oversold must be smaller than overbought")

    def generate(
        self,
        candles: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> list[RawSignal]:
        period = parameters["period"]
        oversold = parameters["oversold"]
        overbought = parameters["overbought"]
        _require_candles(candles, period + 2)
        prices = [float(candle["price"]) for candle in candles]
        values = _rsi(prices, period)
        signals: list[RawSignal] = []
        in_position = False

        for index in range(period, len(candles)):
            if values[index] <= oversold and not in_position:
                in_position = True
                signals.append(
                    _signal(
                        candles[index],
                        "entry",
                        SignalDirection.LONG,
                        min(1.0, (oversold - values[index]) / oversold),
                        f"RSI {values[index]:.2f} is oversold",
                        {"rsi": values[index]},
                    )
                )
            elif values[index] >= overbought and in_position:
                in_position = False
                signals.append(
                    _signal(
                        candles[index],
                        "exit",
                        SignalDirection.EXIT,
                        1.0,
                        f"RSI {values[index]:.2f} is overbought",
                        {"rsi": values[index]},
                    )
                )

        _force_exit(candles, signals, in_position)
        return signals


class MomentumStrategy(StrategyPlugin):
    name = "momentum_roc"
    version = "1.0.0"
    description = "Long-only rate-of-change momentum strategy."
    parameter_schema = {
        "period": {
            "type": "integer",
            "default": 10,
            "minimum": 2,
            "maximum": 200,
        },
        "entry_threshold": {
            "type": "number",
            "default": 0.01,
            "minimum": 0.0001,
            "maximum": 1.0,
        },
        "exit_threshold": {
            "type": "number",
            "default": 0.0,
            "minimum": -1.0,
            "maximum": 1.0,
        },
    }

    def generate(
        self,
        candles: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> list[RawSignal]:
        period = parameters["period"]
        entry_threshold = parameters["entry_threshold"]
        exit_threshold = parameters["exit_threshold"]
        _require_candles(candles, period + 1)
        prices = [float(candle["price"]) for candle in candles]
        signals: list[RawSignal] = []
        in_position = False

        for index in range(period, len(candles)):
            base_price = prices[index - period]
            rate = (
                (prices[index] - base_price) / base_price
                if base_price
                else 0.0
            )
            if rate >= entry_threshold and not in_position:
                in_position = True
                signals.append(
                    _signal(
                        candles[index],
                        "entry",
                        SignalDirection.LONG,
                        min(1.0, rate / max(entry_threshold * 5, 1e-9)),
                        f"Rate of change {rate:.4f} exceeded entry threshold",
                        {"rate_of_change": rate, "period": period},
                    )
                )
            elif rate <= exit_threshold and in_position:
                in_position = False
                signals.append(
                    _signal(
                        candles[index],
                        "exit",
                        SignalDirection.EXIT,
                        1.0,
                        f"Rate of change {rate:.4f} crossed exit threshold",
                        {"rate_of_change": rate, "period": period},
                    )
                )

        _force_exit(candles, signals, in_position)
        return signals


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


def _spread_confidence(fast_value: float, slow_value: float) -> float:
    return min(
        1.0,
        abs(fast_value - slow_value) / max(abs(slow_value), 1e-9),
    )


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

