from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import DEFAULT_DB_PATH, connect
from .infrastructure import initialize_database


@dataclass
class EmaPoint:
    timestamp: datetime
    spot: float
    fast_ema: float
    slow_ema: float


@dataclass
class DemoResult:
    run_id: str
    dataset_id: str
    strategy_name: str
    candles: int
    signals: int
    risk_decisions: int
    orders: int
    trades: int
    net_pnl: float
    max_drawdown: float
    return_pct: float
    report_path: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def fetch_latest_dataset_id(con) -> str:
    row = con.execute(
        """
        SELECT dataset_id
        FROM data_catalog
        WHERE storage_table = 'options_ohlcv'
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("No options dataset found. Run ingest_options first.")
    return row[0]


def load_spot_series(con, dataset_id: str) -> list[tuple[datetime, float]]:
    catalog = con.execute(
        """
        SELECT symbol, exchange, interval
        FROM data_catalog
        WHERE dataset_id = ?
        """,
        [dataset_id],
    ).fetchone()
    if not catalog:
        raise RuntimeError(f"Dataset {dataset_id!r} not found in data_catalog.")

    symbol, exchange, interval = catalog
    rows = con.execute(
        """
        SELECT timestamp, median(spot) AS spot
        FROM options_ohlcv
        WHERE underlying = ? AND exchange = ? AND interval = ?
        GROUP BY timestamp
        ORDER BY timestamp
        """,
        [symbol, exchange, interval],
    ).fetchall()
    if not rows:
        raise RuntimeError(f"Dataset {dataset_id!r} has no spot candles.")
    return [(row[0], float(row[1])) for row in rows]


def calculate_ema_points(
    prices: list[tuple[datetime, float]],
    fast_period: int,
    slow_period: int,
) -> list[EmaPoint]:
    if fast_period <= 0 or slow_period <= 0:
        raise ValueError("EMA periods must be positive.")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be smaller than slow_period.")

    fast_alpha = 2 / (fast_period + 1)
    slow_alpha = 2 / (slow_period + 1)
    fast_ema = prices[0][1]
    slow_ema = prices[0][1]
    points: list[EmaPoint] = []

    for ts, spot in prices:
        fast_ema = (spot * fast_alpha) + (fast_ema * (1 - fast_alpha))
        slow_ema = (spot * slow_alpha) + (slow_ema * (1 - slow_alpha))
        points.append(EmaPoint(ts, spot, fast_ema, slow_ema))
    return points


def calculate_max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return abs(max_drawdown)


def approve_entry_quantity(
    price: float,
    lot_size: int,
    requested_lots: int,
    stop_loss_pct: float,
    max_loss_per_trade: float,
    max_notional: float,
) -> tuple[bool, int, float, float, str]:
    requested_qty = lot_size * requested_lots
    stop_loss_price = price * (1 - stop_loss_pct)
    risk_per_lot = (price - stop_loss_price) * lot_size
    notional = price * requested_qty

    if requested_qty <= 0:
        return False, 0, stop_loss_price, 0.0, "requested quantity is zero"
    if risk_per_lot <= 0:
        return False, 0, stop_loss_price, 0.0, "stop loss risk is invalid"
    if notional > max_notional:
        max_qty_by_notional = int(max_notional // price // lot_size) * lot_size
        requested_qty = min(requested_qty, max_qty_by_notional)
    max_qty_by_risk = int(max_loss_per_trade // risk_per_lot) * lot_size
    approved_qty = min(requested_qty, max_qty_by_risk)

    if approved_qty < lot_size:
        return (
            False,
            0,
            stop_loss_price,
            risk_per_lot,
            "blocked because minimum lot exceeds risk/notional limits",
        )
    return True, approved_qty, stop_loss_price, risk_per_lot, "entry approved"


def run_ema_crossover_demo(
    db_path: Path = DEFAULT_DB_PATH,
    dataset_id: str | None = None,
    artifacts_dir: Path = Path("artifacts/backtests"),
    fast_period: int = 9,
    slow_period: int = 21,
    lot_size: int = 50,
    requested_lots: int = 1,
    stop_loss_pct: float = 0.0075,
    max_loss_per_trade: float = 12500.0,
    max_notional: float = 2_000_000.0,
    starting_equity: float = 1_000_000.0,
    fee_bps: float = 1.0,
) -> DemoResult:
    initialize_database(db_path)
    con = connect(db_path)
    run_id = f"ema_demo_{utc_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    strategy_name = "EMA_CROSSOVER_SPOT_DEMO"
    dataset_id = dataset_id or fetch_latest_dataset_id(con)
    points = calculate_ema_points(load_spot_series(con, dataset_id), fast_period, slow_period)
    parameters = {
        "fast_period": fast_period,
        "slow_period": slow_period,
        "lot_size": lot_size,
        "requested_lots": requested_lots,
        "stop_loss_pct": stop_loss_pct,
        "max_loss_per_trade": max_loss_per_trade,
        "max_notional": max_notional,
        "starting_equity": starting_equity,
        "fee_bps": fee_bps,
    }

    signals: list[tuple] = []
    risk_decisions: list[tuple] = []
    orders: list[tuple] = []
    trades: list[tuple] = []
    equity_curve = [starting_equity]
    trade_pnls: list[float] = []

    position_qty = 0
    entry_price = 0.0
    stop_loss_price: float | None = None
    cumulative_pnl = 0.0
    created_at = utc_now()

    con.execute(
        """
        INSERT OR IGNORE INTO strategy_definitions VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            strategy_name,
            strategy_name,
            "1.0.0",
            "Reference EMA crossover strategy used to validate the generic workflow.",
            json.dumps(
                {
                    "fast_period": {"type": "integer", "minimum": 1},
                    "slow_period": {"type": "integer", "minimum": 2},
                },
                sort_keys=True,
            ),
            True,
            created_at,
        ],
    )
    con.execute(
        """
        INSERT INTO strategy_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            strategy_name,
            dataset_id,
            "running",
            "research",
            json.dumps(parameters, sort_keys=True),
            created_at,
            None,
            None,
        ],
    )

    def store_workflow_event(
        point: EmaPoint,
        signal_type: str,
        reason: str,
        side: str,
        qty: int,
        approved: bool,
        risk_status: str,
        risk_reason: str,
        stop_price: float | None,
        max_loss: float,
    ) -> str | None:
        nonlocal cumulative_pnl, position_qty, entry_price

        signal_id = new_id("sig")
        decision_id = new_id("risk")
        now = utc_now()
        signals.append(
            (
                signal_id,
                run_id,
                point.timestamp,
                "NIFTY",
                signal_type,
                "long" if signal_type == "ENTRY_LONG" else "flat",
                None,
                reason,
                json.dumps(
                    {
                        "spot": point.spot,
                        "fast_ema": point.fast_ema,
                        "slow_ema": point.slow_ema,
                    },
                    sort_keys=True,
                ),
                now,
            )
        )
        risk_decisions.append(
            (
                decision_id,
                run_id,
                signal_id,
                approved,
                lot_size * requested_lots if side == "BUY" else qty,
                qty if approved else 0,
                risk_reason,
                json.dumps(
                    {
                        "risk_status": risk_status.lower(),
                        "estimated_entry_price": point.spot,
                        "stop_loss_price": stop_price,
                        "max_loss": max_loss,
                    },
                    sort_keys=True,
                ),
                now,
                "reference_policy_v1",
            )
        )
        if not approved:
            return None

        order_id = new_id("ord")
        orders.append(
            (
                order_id,
                run_id,
                decision_id,
                "NIFTY",
                side,
                "MARKET",
                qty,
                "filled",
                json.dumps(
                    {
                        "signal_id": signal_id,
                        "timestamp": point.timestamp,
                        "product": "MIS",
                        "fill_price": point.spot,
                    },
                    sort_keys=True,
                    default=str,
                ),
                None,
                now,
                now,
            )
        )

        fee = point.spot * qty * (fee_bps / 10000)
        realized_pnl = -fee
        if side == "BUY":
            position_qty += qty
            entry_price = point.spot
        else:
            gross_pnl = (point.spot - entry_price) * qty
            realized_pnl = gross_pnl - fee
            position_qty -= qty
            trade_pnls.append(realized_pnl)

        cumulative_pnl += realized_pnl
        trades.append(
            (
                new_id("trd"),
                order_id,
                run_id,
                "NIFTY",
                side,
                qty,
                point.spot,
                fee,
                realized_pnl,
                point.timestamp,
            )
        )
        equity_curve.append(starting_equity + cumulative_pnl)
        return order_id

    for index in range(1, len(points)):
        point = points[index]
        previous = points[index - 1]
        if index < slow_period:
            continue

        bullish_cross = previous.fast_ema <= previous.slow_ema and point.fast_ema > point.slow_ema
        bearish_cross = previous.fast_ema >= previous.slow_ema and point.fast_ema < point.slow_ema
        stop_hit = (
            position_qty > 0
            and stop_loss_price is not None
            and point.spot <= stop_loss_price
        )

        if position_qty == 0 and bullish_cross:
            approved, qty, stop_price, risk_per_lot, risk_reason = approve_entry_quantity(
                point.spot,
                lot_size,
                requested_lots,
                stop_loss_pct,
                max_loss_per_trade,
                max_notional,
            )
            store_workflow_event(
                point,
                "ENTRY_LONG",
                "fast EMA crossed above slow EMA",
                "BUY",
                qty,
                approved,
                "APPROVED" if approved else "REJECTED",
                risk_reason,
                stop_price,
                risk_per_lot,
            )
            if approved:
                stop_loss_price = stop_price
        elif position_qty > 0 and (bearish_cross or stop_hit):
            exit_reason = (
                "stop loss touched"
                if stop_hit
                else "fast EMA crossed below slow EMA"
            )
            store_workflow_event(
                point,
                "EXIT_LONG",
                exit_reason,
                "SELL",
                position_qty,
                True,
                "APPROVED",
                "exit approved for open position",
                None,
                0.0,
            )
            stop_loss_price = None

    if position_qty > 0:
        last_point = points[-1]
        store_workflow_event(
            last_point,
            "EXIT_LONG",
            "forced square-off at end of dataset",
            "SELL",
            position_qty,
            True,
            "APPROVED",
            "end-of-backtest square-off",
            None,
            0.0,
        )

    total_fees = sum(row[7] for row in trades)
    gross_profit = sum(pnl for pnl in trade_pnls if pnl > 0)
    gross_loss = sum(pnl for pnl in trade_pnls if pnl < 0)
    net_pnl = cumulative_pnl
    end_equity = starting_equity + net_pnl
    max_drawdown = calculate_max_drawdown(equity_curve)
    return_pct = ((end_equity - starting_equity) / starting_equity) * 100
    trade_timestamps = [row[9] for row in trades]
    summary = {
        "strategy_name": strategy_name,
        "dataset_id": dataset_id,
        "candles": len(points),
        "fills": len(trades),
        "closed_trades": len(trade_pnls),
        "entry_exit_model": "long only; enter on bullish EMA cross; exit on bearish cross, stop loss, or final square-off",
        "storage_tables": [
            "strategy_runs",
            "strategy_signals",
            "risk_decisions",
            "order_events",
            "trade_fills",
            "performance_summaries",
        ],
        "parameters": parameters,
    }

    performance_row = (
        run_id,
        len(trade_pnls),
        net_pnl,
        max_drawdown,
        return_pct,
        json.dumps(
            {
                **summary,
                "winning_trades": sum(1 for pnl in trade_pnls if pnl > 0),
                "losing_trades": sum(1 for pnl in trade_pnls if pnl < 0),
                "gross_profit": gross_profit,
                "gross_loss": gross_loss,
                "total_fees": total_fees,
                "start_equity": starting_equity,
                "end_equity": end_equity,
                "first_trade_at": (
                    min(trade_timestamps) if trade_timestamps else None
                ),
                "last_trade_at": (
                    max(trade_timestamps) if trade_timestamps else None
                ),
            },
            sort_keys=True,
            default=str,
        ),
        utc_now(),
    )

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / f"{run_id}.json"

    try:
        con.execute("BEGIN TRANSACTION")
        if signals:
            con.executemany(
                """
                INSERT INTO strategy_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                signals,
            )
        if risk_decisions:
            con.executemany(
                """
                INSERT INTO risk_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                risk_decisions,
            )
        if orders:
            con.executemany(
                """
                INSERT INTO order_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                orders,
            )
        if trades:
            con.executemany(
                """
                INSERT INTO trade_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                trades,
            )
        con.execute(
            """
            INSERT INTO performance_summaries VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            performance_row,
        )
        con.execute(
            """
            UPDATE strategy_runs
            SET status = ?, finished_at = ?
            WHERE run_id = ?
            """,
            ["completed", utc_now(), run_id],
        )
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")
        con.execute(
            """
            UPDATE strategy_runs
            SET status = ?, finished_at = ?, error_message = ?
            WHERE run_id = ?
            """,
            ["failed", utc_now(), str(exc), run_id],
        )
        raise
    finally:
        con.close()

    result = DemoResult(
        run_id=run_id,
        dataset_id=dataset_id,
        strategy_name=strategy_name,
        candles=len(points),
        signals=len(signals),
        risk_decisions=len(risk_decisions),
        orders=len(orders),
        trades=len(trades),
        net_pnl=round(net_pnl, 2),
        max_drawdown=round(max_drawdown, 2),
        return_pct=round(return_pct, 4),
        report_path=str(report_path),
    )
    report_path.write_text(
        json.dumps({**asdict(result), "summary": summary}, indent=2, default=str),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an EMA crossover demo backtest.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, type=Path, help="DuckDB database path.")
    parser.add_argument("--dataset-id", default=None, help="Data catalog dataset id.")
    parser.add_argument("--fast", default=9, type=int, help="Fast EMA period.")
    parser.add_argument("--slow", default=21, type=int, help="Slow EMA period.")
    parser.add_argument("--lot-size", default=50, type=int, help="Trade lot size.")
    parser.add_argument("--lots", default=1, type=int, help="Requested lots per entry.")
    parser.add_argument(
        "--artifacts",
        default=Path("artifacts/backtests"),
        type=Path,
        help="Directory for backtest report JSON files.",
    )
    args = parser.parse_args()

    result = run_ema_crossover_demo(
        db_path=args.db,
        dataset_id=args.dataset_id,
        artifacts_dir=args.artifacts,
        fast_period=args.fast,
        slow_period=args.slow,
        lot_size=args.lot_size,
        requested_lots=args.lots,
    )
    print(json.dumps(asdict(result), indent=2, default=str))


if __name__ == "__main__":
    main()
