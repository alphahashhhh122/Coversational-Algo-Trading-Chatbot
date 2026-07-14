from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from iimc_trading_platform.config import load_config
from iimc_trading_platform.db import connect
from iimc_trading_platform.domain import ExecutionMode
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services import (
    BacktestService,
    EvidenceService,
    foundation_health,
    operational_summary,
)
from iimc_trading_platform.tools.catalog_tools import list_datasets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build operator evidence from the persisted platform."
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--create-report",
        action="store_true",
        help="Persist a Markdown report artifact for the selected run.",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help=(
            "Create a canonical full-dataset EMA 9/21 research run when "
            "one is not already available."
        ),
    )
    args = parser.parse_args()

    config = load_config()
    initialize_database(config.database_path)
    backtests = BacktestService(config.database_path)
    evidence = EvidenceService(
        config.database_path,
        config.artifacts_dir,
    )
    runs = backtests.list_runs(limit=100)
    selected = _select_canonical_run(backtests, runs, args.run_id)
    if selected is None and args.prepare:
        datasets = list_datasets(config.database_path)["datasets"]
        if not datasets:
            raise SystemExit(
                "No governed dataset is available. Ingest data first."
            )
        created = backtests.run(
            strategy_name="ema_crossover",
            dataset_id=datasets[0]["dataset_id"],
            parameters={
                "fast_period": 9,
                "slow_period": 21,
                "stop_loss_pct": 0.02,
            },
            execution_mode=ExecutionMode.RESEARCH,
            requested_quantity=1,
            fee_bps=1.0,
            slippage_bps=0.5,
        )
        selected = backtests.get_result(created["run_id"])
    if selected is None:
        raise SystemExit(
            "No canonical completed EMA run is available. "
            "Run with --prepare or provide --run-id."
        )

    run_id = selected["run_id"]
    timeline = evidence.run_timeline(run_id)
    result = {
        "health": foundation_health(config),
        "operations": operational_summary(config),
        "datasets": list_datasets(config.database_path),
        "selected_run": timeline["run"],
        "workflow_counts": timeline["counts"],
        "performance": backtests.get_performance(run_id)["summary"],
        "first_events": timeline["events"][:8],
        "storage": _storage_evidence(config.database_path, run_id),
        "safety": {
            "live_trading_enabled": config.allow_live_trading,
            "openalgo_configured": bool(config.openalgo_api_key),
            "data_source": "real",
            "visible_in_openalgo": False,
            "openalgo_boundary": (
                "IIMC historical backtests remain local dashboard/report "
                "evidence. OpenAlgo reflects only OpenAlgo-routed analyzer, "
                "paper, or live broker activity."
            ),
            "submission_policy": (
                "Human approval and an explicit submit API action are required."
            ),
        },
    }
    if args.create_report:
        report = evidence.create_run_report(
            run_id,
            created_by="operator_evidence",
        )
        result["report"] = {
            "report_id": report["report_id"],
            "path": report["path"],
            "artifact_available": report["artifact_available"],
        }
    print(json.dumps(result, indent=2, default=str))


def _select_canonical_run(
    backtests: BacktestService,
    runs: list[dict],
    requested_run_id: str | None,
) -> dict | None:
    if requested_run_id:
        run = backtests.get_result(requested_run_id)
        return run if run and run["status"] == "completed" else None

    candidates = []
    for item in runs:
        if item["status"] != "completed" or item["strategy"] != "ema_crossover":
            continue
        run = backtests.get_result(item["run_id"])
        if run is None:
            continue
        parameters = run["parameters"]
        if parameters.get("fast_period") != 9 or parameters.get("slow_period") != 21:
            continue
        full_dataset = (
            parameters.get("window_start") is None
            and parameters.get("window_end") is None
        )
        candidates.append(
            (
                full_dataset,
                int(run["metrics"].get("candle_count", 0)),
                run["started_at"],
                run,
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[:3])[3]


def _storage_evidence(db_path: Path, run_id: str) -> dict:
    con = connect(db_path)
    try:
        table_counts = {
            "strategy_runs": con.execute(
                "SELECT COUNT(*) FROM strategy_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()[0],
            "strategy_signals": con.execute(
                "SELECT COUNT(*) FROM strategy_signals WHERE run_id = ?",
                [run_id],
            ).fetchone()[0],
            "risk_decisions": con.execute(
                "SELECT COUNT(*) FROM risk_decisions WHERE run_id = ?",
                [run_id],
            ).fetchone()[0],
            "order_events": con.execute(
                "SELECT COUNT(*) FROM order_events WHERE run_id = ?",
                [run_id],
            ).fetchone()[0],
            "order_state_events": con.execute(
                """
                SELECT COUNT(*)
                FROM order_state_events
                WHERE order_id IN (
                    SELECT order_id FROM order_events WHERE run_id = ?
                )
                """,
                [run_id],
            ).fetchone()[0],
            "trade_fills": con.execute(
                "SELECT COUNT(*) FROM trade_fills WHERE run_id = ?",
                [run_id],
            ).fetchone()[0],
            "performance_summaries": con.execute(
                "SELECT COUNT(*) FROM performance_summaries WHERE run_id = ?",
                [run_id],
            ).fetchone()[0],
            "experiment_manifests": con.execute(
                "SELECT COUNT(*) FROM experiment_manifests WHERE run_id = ?",
                [run_id],
            ).fetchone()[0],
        }
        schema_version_count = con.execute(
            "SELECT COUNT(*) FROM schema_versions"
        ).fetchone()[0]
    finally:
        con.close()
    return {
        "database": str(db_path.resolve()),
        "run_scoped_table_counts": table_counts,
        "schema_version_count": schema_version_count,
        "storage_model": (
            "Append-only lifecycle evidence is separated by domain and linked "
            "through run_id, signal_id, decision_id, and order_id."
        ),
    }


if __name__ == "__main__":
    main()
