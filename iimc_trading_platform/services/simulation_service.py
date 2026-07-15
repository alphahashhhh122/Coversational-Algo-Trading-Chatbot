from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SimulatedFill:
    fill_price: float
    fees: float
    realized_pnl: float
    quantity: int


@dataclass
class ResearchLedger:
    starting_equity: float
    position_quantity: int = 0
    position_side: str = "long"
    entry_price: float = 0.0
    entry_fee: float = 0.0
    cumulative_pnl: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    closed_trade_pnls: list[float] = field(default_factory=list)
    closed_trade_records: list[tuple[Any, float]] = field(default_factory=list)
    total_fees: float = 0.0

    def __post_init__(self) -> None:
        self.equity_curve = [self.starting_equity]

    def process(
        self,
        *,
        signal_type: str,
        market_price: float,
        quantity: int,
        fee_bps: float,
        slippage_bps: float,
        timestamp: Any = None,
        direction: Any = "long",
    ) -> SimulatedFill | None:
        if signal_type == "entry" and self.position_quantity > 0:
            return None
        if signal_type == "exit" and self.position_quantity <= 0:
            return None

        side = _direction_value(direction)
        if signal_type == "entry" and side not in {"long", "short"}:
            raise ValueError("Entry direction must be long or short")
        active_side = side if signal_type == "entry" else self.position_side
        slippage_rate = slippage_bps / 10_000
        fill_price = (
            market_price * (1 - slippage_rate)
            if (signal_type == "entry" and active_side == "short")
            or (signal_type == "exit" and active_side == "long")
            else market_price * (1 + slippage_rate)
        )
        fee = fill_price * quantity * (fee_bps / 10_000)
        if signal_type == "entry":
            self.entry_price = fill_price
            self.position_quantity = quantity
            self.position_side = active_side
            self.entry_fee = fee
            realized_pnl = -fee
        else:
            multiplier = 1 if self.position_side == "long" else -1
            gross_pnl = (
                (fill_price - self.entry_price)
                * self.position_quantity
                * multiplier
            )
            realized_pnl = gross_pnl - fee
            closed_pnl = gross_pnl - self.entry_fee - fee
            self.closed_trade_pnls.append(closed_pnl)
            self.closed_trade_records.append((timestamp, closed_pnl))
            self.position_quantity = 0
            self.position_side = "long"
            self.entry_price = 0.0
            self.entry_fee = 0.0

        self.total_fees += fee
        self.cumulative_pnl += realized_pnl
        self.equity_curve.append(
            self.starting_equity + self.cumulative_pnl
        )
        return SimulatedFill(
            fill_price=fill_price,
            fees=fee,
            realized_pnl=realized_pnl,
            quantity=quantity,
        )

    def metrics(self) -> dict[str, Any]:
        return {
            **trade_statistics(
                self.closed_trade_pnls,
                starting_equity=self.starting_equity,
                max_drawdown=max_drawdown(self.equity_curve),
            ),
            **daily_risk_statistics(
                self.closed_trade_records,
                starting_equity=self.starting_equity,
            ),
        }


def screen_signals(
    signals,
    *,
    requested_quantity: int,
    starting_equity: float,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    ledger = ResearchLedger(starting_equity)
    for signal in signals:
        quantity = (
            ledger.position_quantity
            if signal.signal_type == "exit"
            else requested_quantity
        )
        ledger.process(
            signal_type=signal.signal_type,
            market_price=signal.price,
            quantity=quantity,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            timestamp=signal.timestamp,
            direction=getattr(signal.direction, "value", signal.direction),
        )
    drawdown = max_drawdown(ledger.equity_curve)
    return {
        "total_trades": len(ledger.closed_trade_pnls),
        "net_pnl": round(ledger.cumulative_pnl, 6),
        "max_drawdown": round(drawdown, 6),
        "return_pct": round(
            (
                ledger.cumulative_pnl / starting_equity
            ) * 100 if starting_equity else 0.0,
            6,
        ),
        "total_fees": round(ledger.total_fees, 6),
        **ledger.metrics(),
    }


def _direction_value(direction: Any) -> str:
    return str(getattr(direction, "value", direction)).lower()


def max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    maximum = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def trade_statistics(
    trade_pnls: list[float],
    *,
    starting_equity: float,
    max_drawdown: float,
) -> dict[str, float]:
    if not trade_pnls:
        return {
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "expectancy": 0.0,
            "recovery_factor": 0.0,
            "net_pnl_to_starting_equity": 0.0,
        }
    wins = [pnl for pnl in trade_pnls if pnl > 0]
    losses = [pnl for pnl in trade_pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss_abs = abs(sum(losses))
    mean = sum(trade_pnls) / len(trade_pnls)
    net_pnl = sum(trade_pnls)
    return {
        "win_rate_pct": round((len(wins) / len(trade_pnls)) * 100, 4),
        "profit_factor": round(
            gross_profit / gross_loss_abs if gross_loss_abs else 0.0,
            6,
        ),
        "average_win": round(
            gross_profit / len(wins) if wins else 0.0,
            6,
        ),
        "average_loss": round(
            sum(losses) / len(losses) if losses else 0.0,
            6,
        ),
        "expectancy": round(mean, 6),
        "recovery_factor": round(
            net_pnl / max_drawdown if max_drawdown else 0.0,
            6,
        ),
        "net_pnl_to_starting_equity": round(
            net_pnl / starting_equity if starting_equity else 0.0,
            8,
        ),
    }


def daily_risk_statistics(
    trade_records: list[tuple[Any, float]],
    *,
    starting_equity: float,
) -> dict[str, Any]:
    daily_pnl: dict[str, float] = defaultdict(float)
    for timestamp, pnl in trade_records:
        if timestamp is None:
            continue
        date_key = (
            timestamp.date().isoformat()
            if hasattr(timestamp, "date")
            else str(timestamp)[:10]
        )
        daily_pnl[date_key] += pnl
    returns = [
        pnl / starting_equity
        for _, pnl in sorted(daily_pnl.items())
        if starting_equity
    ]
    if not returns:
        return {
            "daily_observations": 0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "risk_metric_basis": "daily_realized_returns",
        }
    mean = sum(returns) / len(returns)
    variance = sum(
        (value - mean) ** 2 for value in returns
    ) / len(returns)
    standard_deviation = math.sqrt(variance)
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = math.sqrt(
        sum(value**2 for value in downside) / len(downside)
    )
    annualizer = math.sqrt(252)
    return {
        "daily_observations": len(returns),
        "sharpe_ratio": round(
            (mean / standard_deviation) * annualizer
            if standard_deviation
            else 0.0,
            6,
        ),
        "sortino_ratio": round(
            (mean / downside_deviation) * annualizer
            if downside_deviation
            else 0.0,
            6,
        ),
        "risk_metric_basis": "daily_realized_returns",
    }
