from __future__ import annotations

import json
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENGINE_VERSION = "research_runtime_1.1.0"

from ..db import connect
from ..domain import ExecutionMode, RunStatus
from ..infrastructure import initialize_database
from ..strategies import StrategyRegistry, build_strategy_registry
from .freshness_service import FreshnessService
from .order_service import OrderService
from .risk_service import RiskService
from .simulation_service import (
    ResearchLedger,
    max_drawdown,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BacktestService:
    def __init__(
        self,
        db_path: Path,
        strategy_registry: StrategyRegistry | None = None,
        allow_live_trading: bool = False,
    ) -> None:
        self.db_path = db_path
        self.strategy_registry = strategy_registry or build_strategy_registry()
        self.allow_live_trading = allow_live_trading

    def list_strategies(self) -> list[dict[str, Any]]:
        return self.strategy_registry.list()

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT r.run_id, r.strategy_id, r.dataset_id, r.status,
                       r.execution_mode, r.started_at, r.finished_at,
                       p.total_trades, p.net_pnl, p.max_drawdown, p.return_pct
                FROM strategy_runs AS r
                LEFT JOIN performance_summaries AS p ON p.run_id = r.run_id
                ORDER BY r.started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return [
            {
                "run_id": row[0],
                "strategy": row[1],
                "dataset_id": row[2],
                "status": row[3],
                "execution_mode": row[4],
                "started_at": row[5],
                "finished_at": row[6],
                "total_trades": row[7],
                "net_pnl": row[8],
                "max_drawdown": row[9],
                "return_pct": row[10],
            }
            for row in rows
        ]

    def run(
        self,
        *,
        strategy_name: str,
        dataset_id: str,
        parameters: dict[str, Any] | None = None,
        execution_mode: ExecutionMode = ExecutionMode.RESEARCH,
        requested_quantity: int = 1,
        starting_equity: float = 1_000_000.0,
        fee_bps: float = 1.0,
        slippage_bps: float = 0.0,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> dict[str, Any]:
        initialize_database(self.db_path)
        freshness = FreshnessService(self.db_path).assess(
            dataset_id,
            "historical_research",
        )
        if freshness["status"] == "rejected":
            raise ValueError(
                f"Dataset is not fit for historical research: "
                f"{freshness['reason']}"
            )
        strategy = self.strategy_registry.get(strategy_name)
        validated_parameters = strategy.validate_parameters(parameters or {})
        dataset, candles = self.load_dataset_candles(
            dataset_id,
            window_start=window_start,
            window_end=window_end,
        )
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        started_at = utc_now()

        self._start_run(
            run_id,
            strategy,
            dataset_id,
            execution_mode,
            {
                **validated_parameters,
                "requested_quantity": requested_quantity,
                "starting_equity": starting_equity,
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
                "window_start": (
                    window_start.isoformat() if window_start else None
                ),
                "window_end": (
                    window_end.isoformat() if window_end else None
                ),
            },
            started_at,
        )
        manifest = self._store_experiment_manifest(
            run_id=run_id,
            strategy_name=strategy.name,
            strategy_version=strategy.version,
            dataset=dataset,
            parameters={
                **validated_parameters,
                "requested_quantity": requested_quantity,
                "starting_equity": starting_equity,
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
                "window_start": (
                    window_start.isoformat() if window_start else None
                ),
                "window_end": (
                    window_end.isoformat() if window_end else None
                ),
            },
        )

        try:
            raw_signals = strategy.generate(candles, validated_parameters)
            result = self._execute_research_workflow(
                run_id=run_id,
                symbol=dataset["symbol"],
                raw_signals=raw_signals,
                requested_quantity=requested_quantity,
                execution_mode=execution_mode,
                starting_equity=starting_equity,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                strategy_name=strategy_name,
                strategy_version=strategy.version,
                dataset_id=dataset_id,
                parameters=validated_parameters,
                candle_count=len(candles),
                freshness_assessment_id=freshness["assessment_id"],
                manifest_id=manifest["manifest_id"],
                manifest_sha256=manifest["manifest_sha256"],
            )
        except Exception as exc:
            self._finish_run(run_id, RunStatus.FAILED, str(exc))
            raise

        self._finish_run(run_id, RunStatus.COMPLETED, None)
        return result

    def get_result(self, run_id: str) -> dict[str, Any] | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT r.run_id, r.strategy_id, r.dataset_id, r.status,
                       r.execution_mode, r.parameters_json, r.started_at,
                       r.finished_at, r.error_message,
                       p.total_trades, p.net_pnl, p.max_drawdown,
                       p.return_pct, p.metrics_json
                FROM strategy_runs AS r
                LEFT JOIN performance_summaries AS p ON p.run_id = r.run_id
                WHERE r.run_id = ?
                """,
                [run_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "strategy": row[1],
            "dataset_id": row[2],
            "status": row[3],
            "execution_mode": row[4],
            "parameters": json.loads(row[5]),
            "started_at": row[6],
            "finished_at": row[7],
            "error_message": row[8],
            "total_trades": row[9],
            "net_pnl": row[10],
            "max_drawdown": row[11],
            "return_pct": row[12],
            "metrics": json.loads(row[13]) if row[13] else {},
        }

    def get_performance(self, run_id: str) -> dict[str, Any]:
        run = self.get_result(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        starting_equity = float(
            run["metrics"].get(
                "starting_equity",
                run["parameters"].get("starting_equity", 1_000_000.0),
            )
        )
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT filled_at, realized_pnl, fees
                FROM trade_fills
                WHERE run_id = ?
                ORDER BY filled_at, trade_id
                """,
                [run_id],
            ).fetchall()
        finally:
            con.close()

        equity = starting_equity
        peak = starting_equity
        curve = []
        for filled_at, realized_pnl, fees in rows:
            equity += float(realized_pnl)
            peak = max(peak, equity)
            curve.append(
                {
                    "timestamp": filled_at,
                    "equity": round(equity, 2),
                    "drawdown": round(peak - equity, 2),
                    "realized_pnl": round(float(realized_pnl), 2),
                    "fees": round(float(fees), 2),
                }
            )
        return {
            "run_id": run_id,
            "summary": {
                "total_trades": run["total_trades"],
                "net_pnl": run["net_pnl"],
                "max_drawdown": run["max_drawdown"],
                "return_pct": run["return_pct"],
                **run["metrics"],
            },
            "equity_curve": curve,
        }

    def load_dataset_candles(
        self,
        dataset_id: str,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if (
            window_start is not None
            and window_end is not None
            and window_start > window_end
        ):
            raise ValueError("window_start must not be after window_end")
        con = connect(self.db_path)
        try:
            dataset_row = con.execute(
                """
                SELECT c.symbol, c.exchange, c.interval, c.storage_table,
                       c.source_id, c.quality_status, r.sha256
                FROM data_catalog AS c
                JOIN raw_file_registry AS r ON r.source_id = c.source_id
                WHERE dataset_id = ?
                """,
                [dataset_id],
            ).fetchone()
            if dataset_row is None:
                raise ValueError(f"Dataset not found: {dataset_id}")
            if dataset_row[3] == "options_ohlcv":
                candle_rows = con.execute(
                    """
                    SELECT timestamp,
                           median(spot) AS price,
                           median(open) AS open,
                           median(high) AS high,
                           median(low) AS low,
                           median(close) AS close,
                           median(volume) AS volume
                    FROM options_ohlcv
                    WHERE source_id = ?
                      AND (? IS NULL OR timestamp >= ?)
                      AND (? IS NULL OR timestamp <= ?)
                    GROUP BY timestamp
                    ORDER BY timestamp
                    """,
                    [
                        dataset_row[4],
                        window_start,
                        window_start,
                        window_end,
                        window_end,
                    ],
                ).fetchall()
            elif dataset_row[3] == "market_ohlcv":
                candle_rows = con.execute(
                    """
                    SELECT timestamp, close AS price, open, high, low, close,
                           volume
                    FROM market_ohlcv
                    WHERE source_id = ?
                      AND (? IS NULL OR timestamp >= ?)
                      AND (? IS NULL OR timestamp <= ?)
                    ORDER BY timestamp
                    """,
                    [
                        dataset_row[4],
                        window_start,
                        window_start,
                        window_end,
                        window_end,
                    ],
                ).fetchall()
            else:
                raise ValueError(
                    f"Unsupported storage table for backtesting: {dataset_row[3]}"
                )
        finally:
            con.close()

        candles = [
            {
                "timestamp": row[0],
                "price": float(row[1]),
                "open": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "close": float(row[5]),
                "volume": float(row[6]),
                "symbol": dataset_row[0],
            }
            for row in candle_rows
            if row[1] is not None
        ]
        if not candles:
            raise ValueError(f"Dataset {dataset_id!r} contains no usable prices")
        return (
            {
                "dataset_id": dataset_id,
                "symbol": dataset_row[0],
                "exchange": dataset_row[1],
                "interval": dataset_row[2],
                "quality_status": dataset_row[5],
                "source_id": dataset_row[4],
                "source_sha256": dataset_row[6],
                "window_start": window_start,
                "window_end": window_end,
            },
            candles,
        )

    def _start_run(
        self,
        run_id: str,
        strategy,
        dataset_id: str,
        execution_mode: ExecutionMode,
        parameters: dict[str, Any],
        started_at: datetime,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO strategy_definitions (
                    strategy_id, name, version, description,
                    parameter_schema_json, enabled, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    strategy.name,
                    strategy.name,
                    strategy.version,
                    strategy.description,
                    json.dumps(strategy.parameter_schema, sort_keys=True),
                    True,
                    started_at,
                ],
            )
            con.execute(
                """
                INSERT INTO strategy_runs (
                    run_id, strategy_id, dataset_id, status, execution_mode,
                    parameters_json, started_at, finished_at, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    strategy.name,
                    dataset_id,
                    RunStatus.RUNNING.value,
                    execution_mode.value,
                    json.dumps(parameters, sort_keys=True),
                    started_at,
                    None,
                    None,
                ],
            )
        finally:
            con.close()

    def _finish_run(
        self,
        run_id: str,
        status: RunStatus,
        error_message: str | None,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE strategy_runs
                SET status = ?, finished_at = ?, error_message = ?
                WHERE run_id = ?
                """,
                [status.value, utc_now(), error_message, run_id],
            )
        finally:
            con.close()

    def _execute_research_workflow(
        self,
        *,
        run_id: str,
        symbol: str,
        raw_signals,
        requested_quantity: int,
        execution_mode: ExecutionMode,
        starting_equity: float,
        fee_bps: float,
        slippage_bps: float,
        strategy_name: str,
        strategy_version: str,
        dataset_id: str,
        parameters: dict[str, Any],
        candle_count: int,
        freshness_assessment_id: str,
        manifest_id: str,
        manifest_sha256: str,
    ) -> dict[str, Any]:
        risk_service = RiskService(
            self.db_path,
            allow_live_trading=self.allow_live_trading,
        )
        order_service = OrderService(self.db_path)
        ledger = ResearchLedger(starting_equity)
        approved_count = 0
        rejected_count = 0

        for raw_signal in raw_signals:
            signal_id = f"sig_{uuid.uuid4().hex[:12]}"
            self._store_signal(run_id, signal_id, raw_signal)
            quantity = (
                ledger.position_quantity
                if raw_signal.signal_type == "exit"
                else requested_quantity
            )
            risk = risk_service.evaluate(
                run_id=run_id,
                signal_id=signal_id,
                signal_type=raw_signal.signal_type,
                symbol=symbol,
                price=raw_signal.price,
                requested_quantity=quantity,
                confidence=raw_signal.confidence,
                execution_mode=execution_mode,
            )
            if not risk.approved:
                rejected_count += 1
                continue
            approved_count += 1
            if (
                raw_signal.signal_type == "exit"
                and ledger.position_quantity <= 0
            ):
                continue

            order = order_service.create_order(
                run_id=run_id,
                decision_id=risk.decision_id,
                symbol=symbol,
                side="BUY" if raw_signal.signal_type == "entry" else "SELL",
                order_type="MARKET",
                quantity=risk.approved_quantity,
                execution_mode=execution_mode,
                price=raw_signal.price,
            )
            if execution_mode in {ExecutionMode.SEMI_AUTO, ExecutionMode.LIVE}:
                continue

            fill = ledger.process(
                signal_type=raw_signal.signal_type,
                market_price=raw_signal.price,
                quantity=order.quantity,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                timestamp=raw_signal.timestamp,
            )
            if fill is None:
                continue

            order_service.record_simulated_fill(
                order,
                fill_price=fill.fill_price,
                fees=fill.fees,
                realized_pnl=fill.realized_pnl,
                filled_at=raw_signal.timestamp,
            )

        maximum_drawdown = max_drawdown(ledger.equity_curve)
        return_pct = (
            (ledger.cumulative_pnl / starting_equity) * 100
            if starting_equity
            else 0.0
        )
        total_fees = ledger.total_fees
        trade_metrics = ledger.metrics()
        metrics = {
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
            "dataset_id": dataset_id,
            "parameters": parameters,
            "starting_equity": starting_equity,
            "ending_equity": starting_equity + ledger.cumulative_pnl,
            "candle_count": candle_count,
            "signal_count": len(raw_signals),
            "risk_approved": approved_count,
            "risk_rejected": rejected_count,
            "winning_trades": sum(
                1 for pnl in ledger.closed_trade_pnls if pnl > 0
            ),
            "losing_trades": sum(
                1 for pnl in ledger.closed_trade_pnls if pnl < 0
            ),
            "gross_profit": sum(
                pnl for pnl in ledger.closed_trade_pnls if pnl > 0
            ),
            "gross_loss": sum(
                pnl for pnl in ledger.closed_trade_pnls if pnl < 0
            ),
            "total_fees": total_fees,
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "manifest_id": manifest_id,
            "manifest_sha256": manifest_sha256,
            "engine_version": ENGINE_VERSION,
            **trade_metrics,
        }
        self._store_performance(
            run_id,
            len(ledger.closed_trade_pnls),
            ledger.cumulative_pnl,
            maximum_drawdown,
            return_pct,
            metrics,
        )
        return {
            "run_id": run_id,
            "strategy": strategy_name,
            "strategy_version": strategy_version,
            "dataset_id": dataset_id,
            "execution_mode": execution_mode.value,
            "status": RunStatus.COMPLETED.value,
            "total_trades": len(ledger.closed_trade_pnls),
            "net_pnl": round(ledger.cumulative_pnl, 2),
            "max_drawdown": round(maximum_drawdown, 2),
            "return_pct": round(return_pct, 4),
            "metrics": metrics,
            "freshness_assessment_id": freshness_assessment_id,
            "manifest_id": manifest_id,
            "manifest_sha256": manifest_sha256,
        }

    def _store_experiment_manifest(
        self,
        *,
        run_id: str,
        strategy_name: str,
        strategy_version: str,
        dataset: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, str]:
        payload = {
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
            "dataset_id": dataset["dataset_id"],
            "source_id": dataset["source_id"],
            "source_sha256": dataset["source_sha256"],
            "parameters": parameters,
            "engine_version": ENGINE_VERSION,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        manifest_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        manifest_id = f"manifest_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO experiment_manifests VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    manifest_id,
                    run_id,
                    strategy_name,
                    strategy_version,
                    dataset["dataset_id"],
                    dataset["source_id"],
                    dataset["source_sha256"],
                    json.dumps(parameters, sort_keys=True),
                    ENGINE_VERSION,
                    manifest_sha256,
                    utc_now(),
                ],
            )
        finally:
            con.close()
        return {
            "manifest_id": manifest_id,
            "manifest_sha256": manifest_sha256,
        }

    def _store_signal(self, run_id: str, signal_id: str, signal) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO strategy_signals (
                    signal_id, run_id, timestamp, symbol, signal_type,
                    direction, confidence, reason, features_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    signal_id,
                    run_id,
                    signal.timestamp,
                    signal.symbol,
                    signal.signal_type,
                    signal.direction.value,
                    signal.confidence,
                    signal.reason,
                    json.dumps(signal.features, sort_keys=True),
                    utc_now(),
                ],
            )
        finally:
            con.close()

    def _sum_fees(self, run_id: str) -> float:
        con = connect(self.db_path)
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(fees), 0) FROM trade_fills WHERE run_id = ?",
                [run_id],
            ).fetchone()
        finally:
            con.close()
        return float(row[0]) if row else 0.0

    def _store_performance(
        self,
        run_id: str,
        total_trades: int,
        net_pnl: float,
        max_drawdown: float,
        return_pct: float,
        metrics: dict[str, Any],
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO performance_summaries (
                    run_id, total_trades, net_pnl, max_drawdown,
                    return_pct, metrics_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    total_trades,
                    net_pnl,
                    max_drawdown,
                    return_pct,
                    json.dumps(metrics, sort_keys=True, default=str),
                    utc_now(),
                ],
            )
        finally:
            con.close()
