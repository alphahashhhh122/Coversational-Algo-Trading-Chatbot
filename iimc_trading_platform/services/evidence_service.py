from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .backtest_service import BacktestService
from .robustness_service import RobustnessService


def _ratio(value: Any) -> str:
    """A risk ratio, or an honest note that the sample could not support one."""
    return f"{value:.4f}" if isinstance(value, (int, float)) else "not computable"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EvidenceService:
    def __init__(self, db_path: Path, artifacts_dir: Path) -> None:
        self.db_path = db_path
        self.artifacts_dir = artifacts_dir
        self.backtests = BacktestService(db_path)
        self.robustness = RobustnessService(db_path)

    def run_timeline(self, run_id: str) -> dict[str, Any]:
        run = self.backtests.get_result(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        con = connect(self.db_path)
        try:
            signals = con.execute(
                """
                SELECT signal_id, timestamp, signal_type, direction,
                       confidence, reason, features_json, created_at
                FROM strategy_signals
                WHERE run_id = ?
                ORDER BY timestamp, signal_id
                """,
                [run_id],
            ).fetchall()
            risks = con.execute(
                """
                SELECT r.decision_id, r.signal_id, r.approved,
                       r.requested_quantity, r.approved_quantity, r.reason,
                       r.checks_json, s.timestamp, r.risk_policy_version,
                       r.created_at
                FROM risk_decisions AS r
                JOIN strategy_signals AS s ON s.signal_id = r.signal_id
                WHERE r.run_id = ?
                ORDER BY s.timestamp, r.decision_id
                """,
                [run_id],
            ).fetchall()
            orders = con.execute(
                """
                SELECT o.order_id, o.decision_id, o.symbol, o.side,
                       o.quantity, o.price, o.status, o.execution_mode,
                       s.timestamp, o.created_at
                FROM order_events AS o
                JOIN risk_decisions AS r ON r.decision_id = o.decision_id
                JOIN strategy_signals AS s ON s.signal_id = r.signal_id
                WHERE o.run_id = ?
                ORDER BY s.timestamp, o.order_id
                """,
                [run_id],
            ).fetchall()
            fills = con.execute(
                """
                SELECT trade_id, order_id, side, quantity, price, fees,
                       realized_pnl, filled_at
                FROM trade_fills
                WHERE run_id = ?
                ORDER BY filled_at, trade_id
                """,
                [run_id],
            ).fetchall()
        finally:
            con.close()

        events: list[dict[str, Any]] = []
        for row in signals:
            events.append(
                {
                    "timestamp": row[1],
                    "recorded_at": row[7],
                    "event_type": "signal",
                    "entity_id": row[0],
                    "details": {
                        "signal_type": row[2],
                        "direction": row[3],
                        "confidence": row[4],
                        "reason": row[5],
                        "features": json.loads(row[6]),
                    },
                }
            )
        for row in risks:
            events.append(
                {
                    "timestamp": row[7],
                    "recorded_at": row[9],
                    "event_type": "risk_decision",
                    "entity_id": row[0],
                    "parent_id": row[1],
                    "details": {
                        "approved": row[2],
                        "requested_quantity": row[3],
                        "approved_quantity": row[4],
                        "reason": row[5],
                        "checks": json.loads(row[6]),
                        "policy_version": row[8],
                    },
                }
            )
        for row in orders:
            events.append(
                {
                    "timestamp": row[8],
                    "recorded_at": row[9],
                    "event_type": "order",
                    "entity_id": row[0],
                    "parent_id": row[1],
                    "details": {
                        "symbol": row[2],
                        "side": row[3],
                        "quantity": row[4],
                        "price": row[5],
                        "status": row[6],
                        "execution_mode": row[7],
                    },
                }
            )
        for row in fills:
            events.append(
                {
                    "timestamp": row[7],
                    "recorded_at": row[7],
                    "event_type": "fill",
                    "entity_id": row[0],
                    "parent_id": row[1],
                    "details": {
                        "side": row[2],
                        "quantity": row[3],
                        "price": row[4],
                        "fees": row[5],
                        "realized_pnl": row[6],
                    },
                }
            )
        event_priority = {
            "signal": 1,
            "risk_decision": 2,
            "order": 3,
            "fill": 4,
        }
        events.sort(
            key=lambda event: (
                event["timestamp"],
                event_priority[event["event_type"]],
                event["entity_id"],
            )
        )
        return {
            "run_id": run_id,
            "run": run,
            "counts": {
                "signals": len(signals),
                "risk_decisions": len(risks),
                "orders": len(orders),
                "fills": len(fills),
            },
            "events": events,
        }

    def compare_runs(self, run_ids: list[str]) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(run_ids))
        if len(unique_ids) < 2 or len(unique_ids) > 10:
            raise ValueError("Comparison requires between 2 and 10 unique runs")

        runs = []
        for run_id in unique_ids:
            result = self.backtests.get_result(run_id)
            if result is None:
                raise ValueError(f"Run not found: {run_id}")
            runs.append(result)

        ranked = sorted(
            runs,
            key=lambda run: (
                float(run["return_pct"] or 0),
                float(run["net_pnl"] or 0),
                -float(run["max_drawdown"] or 0),
            ),
            reverse=True,
        )
        return {
            "run_ids": unique_ids,
            "runs": runs,
            "ranking": [
                {
                    "rank": index,
                    "run_id": run["run_id"],
                    "strategy": run["strategy"],
                    "return_pct": run["return_pct"],
                    "net_pnl": run["net_pnl"],
                    "max_drawdown": run["max_drawdown"],
                }
                for index, run in enumerate(ranked, start=1)
            ],
            "ranking_method": (
                "Return percentage descending, then net P&L descending, "
                "then maximum drawdown ascending. This is comparison evidence, "
                "not an investment recommendation."
            ),
        }

    def create_run_report(
        self,
        run_id: str,
        *,
        created_by: str,
    ) -> dict[str, Any]:
        timeline = self.run_timeline(run_id)
        performance = self.backtests.get_performance(run_id)
        run = timeline["run"]
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        created_at = utc_now()
        report_dir = self.artifacts_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{report_id}.md"

        summary = performance["summary"]
        lines = [
            f"# Strategy Run Report: {run_id}",
            "",
            f"- Strategy: `{run['strategy']}`",
            f"- Dataset: `{run['dataset_id']}`",
            f"- Status: `{run['status']}`",
            f"- Execution mode: `{run['execution_mode']}`",
            f"- Started: `{run['started_at']}`",
            f"- Finished: `{run['finished_at']}`",
            f"- Generated by: `{created_by}`",
            f"- Generated at: `{created_at}`",
            "",
            "## Performance",
            "",
            f"- Total closed trades: {summary.get('total_trades', 0)}",
            f"- Net P&L: {summary.get('net_pnl', 0):.2f}",
            f"- Return: {summary.get('return_pct', 0):.4f}%",
            f"- Maximum drawdown: {summary.get('max_drawdown', 0):.2f}",
            f"- Total fees: {summary.get('total_fees', 0):.2f}",
            f"- Win rate: {summary.get('win_rate_pct', 0):.2f}%",
            f"- Profit factor: {summary.get('profit_factor', 0):.4f}",
            # Sharpe and Sortino are None when there was no deviation to divide
            # by. Printing 0.0000 there would read as "no risk-adjusted edge"
            # when the truth is that the sample cannot support the ratio.
            f"- Sharpe ratio: {_ratio(summary.get('sharpe_ratio'))}",
            f"- Sortino ratio: {_ratio(summary.get('sortino_ratio'))}",
            f"- Recovery factor: {summary.get('recovery_factor', 0):.4f}",
            "",
            "## Workflow Evidence",
            "",
            f"- Signals: {timeline['counts']['signals']}",
            f"- Risk decisions: {timeline['counts']['risk_decisions']}",
            f"- Orders: {timeline['counts']['orders']}",
            f"- Fills: {timeline['counts']['fills']}",
            "",
            "## Reproducibility",
            "",
            "```json",
            json.dumps(run["parameters"], indent=2, sort_keys=True, default=str),
            "```",
            "",
            (
                "This report is generated from persisted platform records. "
                "It is research evidence and not investment advice."
            ),
            "",
        ]
        report_path.write_text("\n".join(lines), encoding="utf-8")

        metadata = {
            "created_by": created_by,
            "strategy": run["strategy"],
            "dataset_id": run["dataset_id"],
            "counts": timeline["counts"],
        }
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO report_artifacts (
                    report_id, report_type, title, path, source_run_id,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    report_id,
                    "strategy_run_markdown",
                    f"Strategy Run Report: {run_id}",
                    str(report_path),
                    run_id,
                    json.dumps(metadata, sort_keys=True),
                    created_at,
                ],
            )
        finally:
            con.close()
        return self.get_report(report_id)

    def list_reports(self, limit: int = 100) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT report_id, report_type, title, path, source_run_id,
                       metadata_json, created_at
                FROM report_artifacts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "reports": [
                {
                    "report_id": row[0],
                    "report_type": row[1],
                    "title": row[2],
                    "path": row[3],
                    "source_run_id": row[4],
                    "metadata": json.loads(row[5]),
                    "created_at": row[6],
                }
                for row in rows
            ]
        }

    def create_robustness_report(
        self,
        experiment_id: str,
        *,
        created_by: str,
    ) -> dict[str, Any]:
        experiment = self.robustness.get(experiment_id)
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        created_at = utc_now()
        report_dir = self.artifacts_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{report_id}.md"
        summary = experiment.get("summary") or {}
        lines = [
            f"# Robustness Experiment Report: {experiment_id}",
            "",
            f"- Strategy: `{experiment['strategy_name']}`",
            f"- Dataset: `{experiment['dataset_id']}`",
            f"- Status: `{experiment['status']}`",
            f"- Verdict: `{experiment['verdict']}`",
            f"- Candidate count: {experiment['candidate_count']}",
            f"- Split ratio: {experiment['split_ratio']:.2f}",
            f"- Generated by: `{created_by}`",
            f"- Generated at: `{created_at}`",
            "",
            "## Chronological Windows",
            "",
            f"- Train: `{experiment['windows']['train_start']}` to "
            f"`{experiment['windows']['train_end']}`",
            f"- Test: `{experiment['windows']['test_start']}` to "
            f"`{experiment['windows']['test_end']}`",
            "",
            "## Selected Candidate",
            "",
            "```json",
            json.dumps(
                experiment["selected_parameters"],
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
            f"- Train run: `{experiment['selected_train_run_id']}`",
            f"- Test run: `{experiment['selected_test_run_id']}`",
            f"- Train return: "
            f"{summary.get('selected_train_metrics', {}).get('return_pct', 0):.4f}%",
            f"- Test return: "
            f"{summary.get('selected_test_metrics', {}).get('return_pct', 0):.4f}%",
            f"- Test profit factor: "
            f"{summary.get('selected_test_metrics', {}).get('profit_factor', 0):.4f}",
            "",
            "## Benchmark",
            "",
            f"- Buy-and-hold test return: "
            f"{(experiment.get('benchmark') or {}).get('return_pct', 0):.4f}%",
            "",
            "## Verdict Checks",
            "",
        ]
        for name, passed in (summary.get("checks") or {}).items():
            lines.append(f"- {name}: {'PASS' if passed else 'FAIL'}")
        lines.extend(
            [
                "",
                "## Parameter Sensitivity",
                "",
                "| Candidate | Selected | Train Return | Test Return | "
                "Test Profit Factor |",
                "|---:|:---:|---:|---:|---:|",
            ]
        )
        for trial in experiment["trials"]:
            lines.append(
                f"| {trial['candidate_index']} | "
                f"{'yes' if trial['selected'] else 'no'} | "
                f"{trial['train_metrics']['return_pct']:.4f}% | "
                f"{trial['test_metrics']['return_pct']:.4f}% | "
                f"{trial['test_metrics']['profit_factor']:.4f} |"
            )
        lines.extend(
            [
                "",
                (
                    "The parameter choice was selected using only the training "
                    "window. The test window was evaluated afterward. This is "
                    "historical research evidence, not a forecast."
                ),
                "",
            ]
        )
        report_path.write_text("\n".join(lines), encoding="utf-8")
        metadata = {
            "created_by": created_by,
            "experiment_id": experiment_id,
            "strategy": experiment["strategy_name"],
            "dataset_id": experiment["dataset_id"],
            "verdict": experiment["verdict"],
        }
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO report_artifacts (
                    report_id, report_type, title, path, source_run_id,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    report_id,
                    "robustness_experiment_markdown",
                    f"Robustness Experiment Report: {experiment_id}",
                    str(report_path),
                    experiment["selected_test_run_id"],
                    json.dumps(metadata, sort_keys=True),
                    created_at,
                ],
            )
        finally:
            con.close()
        return self.get_report(report_id)

    def get_report(self, report_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT report_id, report_type, title, path, source_run_id,
                       metadata_json, created_at
                FROM report_artifacts
                WHERE report_id = ?
                """,
                [report_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(f"Report not found: {report_id}")
        path = Path(row[3])
        return {
            "report_id": row[0],
            "report_type": row[1],
            "title": row[2],
            "path": str(path),
            "source_run_id": row[4],
            "metadata": json.loads(row[5]),
            "created_at": row[6],
            "content": path.read_text(encoding="utf-8") if path.exists() else None,
            "artifact_available": path.exists(),
        }
