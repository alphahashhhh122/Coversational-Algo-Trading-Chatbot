from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import connect


class HistoricalDataService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def local_coverage(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: str,
    ) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT dataset_id, data_type, start_ts, end_ts, row_count,
                       quality_status
                FROM data_catalog
                WHERE UPPER(symbol) = UPPER(?)
                  AND UPPER(exchange) = UPPER(?)
                  AND (interval = ? OR ? = '')
                ORDER BY updated_at DESC
                """,
                [symbol, exchange, interval, interval],
            ).fetchall()
        finally:
            con.close()
        return {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "interval": interval,
            "historical_available_locally": bool(rows),
            "no_synthetic_fallback": True,
            "datasets": [
                {
                    "dataset_id": row[0],
                    "data_type": row[1],
                    "start_ts": row[2],
                    "end_ts": row[3],
                    "row_count": row[4],
                    "quality_status": row[5],
                    "data_source": "real",
                }
                for row in rows
            ],
        }
