from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.backtest_service import BacktestService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the governed database and optional real backtest."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/iimc_platform.duckdb"),
    )
    parser.add_argument("--run-backtest", action="store_true")
    args = parser.parse_args()

    initialize_database(args.database)
    con = connect(args.database)
    try:
        datasets = con.execute(
            """
            SELECT dataset_id, row_count, quality_status, source_id
            FROM data_catalog
            ORDER BY updated_at DESC
            """
        ).fetchall()
        schema_versions = [
            row[0]
            for row in con.execute(
                """
                SELECT version_id
                FROM schema_versions
                ORDER BY applied_at, version_id
                """
            ).fetchall()
        ]
    finally:
        con.close()

    output: dict[str, object] = {
        "database": str(args.database.resolve()),
        "datasets": [
            {
                "dataset_id": row[0],
                "row_count": row[1],
                "quality_status": row[2],
                "source_id": row[3],
            }
            for row in datasets
        ],
        "schema_versions": schema_versions,
    }
    if args.run_backtest:
        if not datasets:
            raise RuntimeError("No governed dataset is available")
        output["backtest"] = BacktestService(args.database).run(
            strategy_name="ema_crossover",
            dataset_id=datasets[0][0],
            parameters={"fast_period": 9, "slow_period": 21},
            requested_quantity=1,
        )

    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
