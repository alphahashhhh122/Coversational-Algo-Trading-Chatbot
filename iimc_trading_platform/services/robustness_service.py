from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..domain import ExecutionMode
from ..infrastructure import initialize_database
from ..strategies import build_strategy_registry
from .backtest_service import BacktestService
from .simulation_service import (
    ResearchLedger,
    candle_dates as _candle_dates,
    screen_signals,
)

ROBUSTNESS_POLICY_VERSION = "1.1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RobustnessService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.backtests = BacktestService(db_path)
        self.strategies = build_strategy_registry()

    def run(
        self,
        *,
        strategy_name: str,
        dataset_id: str,
        parameter_grid: list[dict[str, Any]],
        split_ratio: float = 0.7,
        requested_quantity: int = 1,
        starting_equity: float = 1_000_000.0,
        fee_bps: float = 1.0,
        slippage_bps: float = 0.0,
        requested_by: str = "researcher",
        persist_selected_runs: bool = True,
    ) -> dict[str, Any]:
        initialize_database(self.db_path)
        if not 0.5 <= split_ratio <= 0.85:
            raise ValueError("split_ratio must be between 0.5 and 0.85")
        if not 1 <= len(parameter_grid) <= 12:
            raise ValueError("parameter_grid must contain between 1 and 12 candidates")

        strategy = self.strategies.get(strategy_name)
        validated_grid = [
            strategy.validate_parameters(candidate)
            for candidate in parameter_grid
        ]
        _, candles = self.backtests.load_dataset_candles(dataset_id)
        if len(candles) < 100:
            raise ValueError(
                "At least 100 candles are required for robustness evaluation"
            )
        split_index = int(len(candles) * split_ratio)
        if split_index < 50 or len(candles) - split_index < 30:
            raise ValueError("Train/test windows are too small")
        train = candles[:split_index]
        test = candles[split_index:]
        experiment_id = f"robust_{uuid.uuid4().hex[:12]}"
        started_at = utc_now()
        self._start_experiment(
            experiment_id=experiment_id,
            strategy_name=strategy_name,
            dataset_id=dataset_id,
            split_ratio=split_ratio,
            train=train,
            test=test,
            candidate_count=len(validated_grid),
            requested_by=requested_by,
            started_at=started_at,
        )

        try:
            trials = []
            for index, parameters in enumerate(validated_grid):
                train_metrics = screen_signals(
                    strategy.generate(train, parameters),
                    requested_quantity=requested_quantity,
                    starting_equity=starting_equity,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    session_dates=_candle_dates(train),
                )
                test_metrics = screen_signals(
                    strategy.generate(test, parameters),
                    requested_quantity=requested_quantity,
                    starting_equity=starting_equity,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    session_dates=_candle_dates(test),
                )
                trial = {
                    "candidate_index": index,
                    "parameters": parameters,
                    "train_metrics": train_metrics,
                    "test_metrics": test_metrics,
                    "train_score": _selection_score(
                        train_metrics,
                        starting_equity,
                    ),
                }
                trials.append(trial)

            selected = max(
                trials,
                key=lambda item: (
                    item["train_score"],
                    item["train_metrics"]["return_pct"],
                    -item["train_metrics"]["max_drawdown"],
                ),
            )
            for trial in trials:
                trial["selected"] = (
                    trial["candidate_index"]
                    == selected["candidate_index"]
                )
                self._store_trial(experiment_id, trial)

            benchmark = _buy_and_hold_benchmark(
                test,
                quantity=requested_quantity,
                starting_equity=starting_equity,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            verdict, checks = _robustness_verdict(
                selected,
                trials,
                benchmark,
            )
            train_run_id = None
            test_run_id = None
            if persist_selected_runs:
                train_run = self.backtests.run(
                    strategy_name=strategy_name,
                    dataset_id=dataset_id,
                    parameters=selected["parameters"],
                    execution_mode=ExecutionMode.RESEARCH,
                    requested_quantity=requested_quantity,
                    starting_equity=starting_equity,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    window_start=train[0]["timestamp"],
                    window_end=train[-1]["timestamp"],
                )
                test_run = self.backtests.run(
                    strategy_name=strategy_name,
                    dataset_id=dataset_id,
                    parameters=selected["parameters"],
                    execution_mode=ExecutionMode.RESEARCH,
                    requested_quantity=requested_quantity,
                    starting_equity=starting_equity,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    window_start=test[0]["timestamp"],
                    window_end=test[-1]["timestamp"],
                )
                train_run_id = train_run["run_id"]
                test_run_id = test_run["run_id"]

            summary = {
                "selected_candidate_index": selected["candidate_index"],
                "selected_train_metrics": selected["train_metrics"],
                "selected_test_metrics": selected["test_metrics"],
                "checks": checks,
                "profitable_test_candidate_ratio": round(
                    sum(
                        1
                        for trial in trials
                        if trial["test_metrics"]["net_pnl"] > 0
                    )
                    / len(trials),
                    4,
                ),
            }
            self._finish_experiment(
                experiment_id=experiment_id,
                selected_parameters=selected["parameters"],
                selected_train_run_id=train_run_id,
                selected_test_run_id=test_run_id,
                benchmark=benchmark,
                verdict=verdict,
                summary=summary,
            )
        except Exception as exc:
            self._fail_experiment(experiment_id, str(exc))
            raise
        return self.get(experiment_id)

    def get(self, experiment_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT experiment_id, strategy_name, dataset_id, status,
                       split_ratio, train_start, train_end, test_start,
                       test_end, candidate_count, selected_parameters_json,
                       selected_train_run_id, selected_test_run_id,
                       benchmark_json, verdict, evaluation_policy_version,
                       summary_json, requested_by,
                       started_at, finished_at, error_message
                FROM robustness_experiments
                WHERE experiment_id = ?
                """,
                [experiment_id],
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"Robustness experiment not found: {experiment_id}"
                )
            trial_rows = con.execute(
                """
                SELECT trial_id, candidate_index, parameters_json,
                       train_metrics_json, test_metrics_json, train_score,
                       selected, created_at
                FROM robustness_trials
                WHERE experiment_id = ?
                ORDER BY candidate_index
                """,
                [experiment_id],
            ).fetchall()
        finally:
            con.close()
        return {
            "experiment_id": row[0],
            "strategy_name": row[1],
            "dataset_id": row[2],
            "status": row[3],
            "split_ratio": row[4],
            "windows": {
                "train_start": row[5],
                "train_end": row[6],
                "test_start": row[7],
                "test_end": row[8],
            },
            "candidate_count": row[9],
            "selected_parameters": json.loads(row[10]) if row[10] else None,
            "selected_train_run_id": row[11],
            "selected_test_run_id": row[12],
            "benchmark": json.loads(row[13]) if row[13] else None,
            "verdict": row[14],
            "evaluation_policy_version": row[15],
            "summary": json.loads(row[16]) if row[16] else None,
            "requested_by": row[17],
            "started_at": row[18],
            "finished_at": row[19],
            "error_message": row[20],
            "trials": [
                {
                    "trial_id": trial[0],
                    "candidate_index": trial[1],
                    "parameters": json.loads(trial[2]),
                    "train_metrics": json.loads(trial[3]),
                    "test_metrics": json.loads(trial[4]),
                    "train_score": trial[5],
                    "selected": trial[6],
                    "created_at": trial[7],
                }
                for trial in trial_rows
            ],
        }

    def reevaluate(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.get(experiment_id)
        if experiment["status"] != "completed":
            raise ValueError("Only completed experiments can be reevaluated")
        selected = next(
            trial for trial in experiment["trials"]
            if trial["selected"]
        )
        verdict, checks = _robustness_verdict(
            selected,
            experiment["trials"],
            experiment["benchmark"],
        )
        summary = {
            **(experiment["summary"] or {}),
            "checks": checks,
            "profitable_test_candidate_ratio": round(
                sum(
                    1
                    for trial in experiment["trials"]
                    if trial["test_metrics"]["net_pnl"] > 0
                )
                / len(experiment["trials"]),
                4,
            ),
        }
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE robustness_experiments
                SET verdict = ?, evaluation_policy_version = ?,
                    summary_json = ?
                WHERE experiment_id = ?
                """,
                [
                    verdict,
                    ROBUSTNESS_POLICY_VERSION,
                    json.dumps(summary, sort_keys=True),
                    experiment_id,
                ],
            )
        finally:
            con.close()
        return self.get(experiment_id)

    def list(self, limit: int = 50) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT experiment_id, strategy_name, dataset_id, status,
                       candidate_count, verdict, selected_train_run_id,
                       selected_test_run_id, started_at, finished_at
                FROM robustness_experiments
                ORDER BY started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "experiments": [
                {
                    "experiment_id": row[0],
                    "strategy_name": row[1],
                    "dataset_id": row[2],
                    "status": row[3],
                    "candidate_count": row[4],
                    "verdict": row[5],
                    "selected_train_run_id": row[6],
                    "selected_test_run_id": row[7],
                    "started_at": row[8],
                    "finished_at": row[9],
                }
                for row in rows
            ]
        }

    def _start_experiment(
        self,
        *,
        experiment_id: str,
        strategy_name: str,
        dataset_id: str,
        split_ratio: float,
        train: list[dict[str, Any]],
        test: list[dict[str, Any]],
        candidate_count: int,
        requested_by: str,
        started_at: datetime,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO robustness_experiments (
                    experiment_id, strategy_name, dataset_id, status,
                    split_ratio, train_start, train_end, test_start, test_end,
                    candidate_count, selected_parameters_json,
                    selected_train_run_id, selected_test_run_id,
                    benchmark_json, verdict, summary_json, requested_by,
                    started_at, finished_at, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    experiment_id,
                    strategy_name,
                    dataset_id,
                    "running",
                    split_ratio,
                    train[0]["timestamp"],
                    train[-1]["timestamp"],
                    test[0]["timestamp"],
                    test[-1]["timestamp"],
                    candidate_count,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    requested_by,
                    started_at,
                    None,
                    None,
                ],
            )
        finally:
            con.close()

    def _store_trial(
        self,
        experiment_id: str,
        trial: dict[str, Any],
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO robustness_trials VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    f"trial_{uuid.uuid4().hex[:12]}",
                    experiment_id,
                    trial["candidate_index"],
                    json.dumps(trial["parameters"], sort_keys=True),
                    json.dumps(trial["train_metrics"], sort_keys=True),
                    json.dumps(trial["test_metrics"], sort_keys=True),
                    trial["train_score"],
                    trial["selected"],
                    utc_now(),
                ],
            )
        finally:
            con.close()

    def _finish_experiment(
        self,
        *,
        experiment_id: str,
        selected_parameters: dict[str, Any],
        selected_train_run_id: str | None,
        selected_test_run_id: str | None,
        benchmark: dict[str, Any],
        verdict: str,
        summary: dict[str, Any],
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE robustness_experiments
                SET status = 'completed',
                    selected_parameters_json = ?,
                    selected_train_run_id = ?,
                    selected_test_run_id = ?,
                    benchmark_json = ?,
                    verdict = ?,
                    evaluation_policy_version = ?,
                    summary_json = ?,
                    finished_at = ?
                WHERE experiment_id = ?
                """,
                [
                    json.dumps(selected_parameters, sort_keys=True),
                    selected_train_run_id,
                    selected_test_run_id,
                    json.dumps(benchmark, sort_keys=True, default=str),
                    verdict,
                    ROBUSTNESS_POLICY_VERSION,
                    json.dumps(summary, sort_keys=True, default=str),
                    utc_now(),
                    experiment_id,
                ],
            )
        finally:
            con.close()

    def _fail_experiment(
        self,
        experiment_id: str,
        error_message: str,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE robustness_experiments
                SET status = 'failed', error_message = ?, finished_at = ?
                WHERE experiment_id = ?
                """,
                [error_message, utc_now(), experiment_id],
            )
        finally:
            con.close()


def _selection_score(
    metrics: dict[str, Any],
    starting_equity: float,
) -> float:
    if metrics["total_trades"] < 2:
        return -1_000_000.0
    drawdown_pct = (
        metrics["max_drawdown"] / starting_equity
    ) * 100 if starting_equity else 0.0
    return round(metrics["return_pct"] - drawdown_pct, 8)


def _buy_and_hold_benchmark(
    candles: list[dict[str, Any]],
    *,
    quantity: int,
    starting_equity: float,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    ledger = ResearchLedger(starting_equity)
    ledger.process(
        signal_type="entry",
        market_price=float(candles[0]["price"]),
        quantity=quantity,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        timestamp=candles[0]["timestamp"],
    )
    ledger.process(
        signal_type="exit",
        market_price=float(candles[-1]["price"]),
        quantity=quantity,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        timestamp=candles[-1]["timestamp"],
    )
    return {
        "name": "buy_and_hold",
        "start_timestamp": candles[0]["timestamp"],
        "end_timestamp": candles[-1]["timestamp"],
        "start_price": candles[0]["price"],
        "end_price": candles[-1]["price"],
        "net_pnl": round(ledger.cumulative_pnl, 6),
        "return_pct": round(
            ledger.cumulative_pnl / starting_equity * 100,
            6,
        ) if starting_equity else 0.0,
    }


def _robustness_verdict(
    selected: dict[str, Any],
    trials: list[dict[str, Any]],
    benchmark: dict[str, Any],
) -> tuple[str, dict[str, bool]]:
    test = selected["test_metrics"]
    profitable_ratio = (
        sum(
            1 for trial in trials
            if trial["test_metrics"]["net_pnl"] > 0
        )
        / len(trials)
    )
    checks = {
        "out_of_sample_positive": test["net_pnl"] > 0,
        "out_of_sample_profit_factor": test["profit_factor"] >= 1.0,
        "minimum_out_of_sample_trades": test["total_trades"] >= 20,
        "parameter_neighborhood_stable": profitable_ratio >= 0.5,
        "beats_test_benchmark": (
            test["return_pct"] >= benchmark["return_pct"]
        ),
    }
    passed = sum(checks.values())
    if not checks["minimum_out_of_sample_trades"]:
        verdict = "insufficient_sample"
    elif passed == len(checks):
        verdict = "robust"
    elif passed >= 3:
        verdict = "mixed"
    else:
        verdict = "fragile"
    return verdict, checks
