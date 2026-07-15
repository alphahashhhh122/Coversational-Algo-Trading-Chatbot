from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import connect
from .openalgo_readiness_service import OpenAlgoReadinessService


SUPPORTED_ASSET_CLASSES = {
    "equity",
    "index",
    "futures",
    "options",
    "commodity",
    "crypto",
}


class CapabilityCoverageService:
    def __init__(
        self,
        db_path: Path,
        openalgo: OpenAlgoReadinessService,
    ) -> None:
        self.db_path = db_path
        self.openalgo = openalgo

    def platform_status(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        normalized_asset_class = asset_class.lower()
        local_dataset = self._local_dataset(
            symbol=symbol,
            exchange=exchange,
            asset_class=normalized_asset_class,
            interval=interval,
        )
        supported = normalized_asset_class in SUPPORTED_ASSET_CLASSES
        openalgo_readiness = self.openalgo.readiness(
            symbol=symbol,
            exchange=exchange,
            asset_class=normalized_asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
        unsupported_reason = None
        if not supported:
            unsupported_reason = (
                f"Asset class {asset_class!r} is not in the local readiness "
                "vocabulary."
            )
        elif not openalgo_readiness["provider_configured"]:
            unsupported_reason = "OpenAlgo credentials are not configured."
        elif not openalgo_readiness["verified_now"]:
            unsupported_reason = openalgo_readiness["unsupported_reason"]

        return {
            "ok": supported,
            "status": "ready_check_completed" if supported else "unsupported",
            "safe_failure": not supported,
            "message": (
                "Readiness validation completed."
                if supported
                else unsupported_reason
            ),
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "asset_class": normalized_asset_class,
            "interval": interval,
            "start_date": start_date,
            "end_date": end_date,
            "provider_configured": openalgo_readiness["provider_configured"],
            "quote_available": openalgo_readiness["quote_available"],
            "historical_available": openalgo_readiness["historical_available"],
            "local_dataset_exists": local_dataset is not None,
            "rows_available": local_dataset["row_count"] if local_dataset else 0,
            "local_dataset": local_dataset,
            "analyzer_path_status": openalgo_readiness["analyzer_path_status"],
            "paper_path_status": openalgo_readiness["paper_path_status"],
            "live_path_status": openalgo_readiness["live_path_status"],
            "supported_by_architecture": supported,
            "verified_now": openalgo_readiness["verified_now"],
            "unsupported_reason": unsupported_reason,
            "no_synthetic_fallback": True,
        }

    def _local_dataset(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
    ) -> dict[str, Any] | None:
        data_type = {
            "options": "options_ohlcv",
            "equity": "equity_ohlcv",
            "index": "index_ohlcv",
            "futures": "futures_ohlcv",
            "commodity": "commodity_ohlcv",
            "crypto": "crypto_ohlcv",
        }.get(asset_class)
        storage_table = (
            "options_ohlcv" if asset_class == "options" else "market_ohlcv"
        )
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT dataset_id, data_domain, data_type, symbol, exchange,
                       interval, start_ts, end_ts, row_count, quality_status
                FROM data_catalog
                WHERE UPPER(symbol) = UPPER(?)
                  AND UPPER(exchange) = UPPER(?)
                  AND (? IS NULL OR data_type = ?)
                  AND storage_table = ?
                  AND (interval = ? OR ? = '')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                [
                    symbol,
                    exchange,
                    data_type,
                    data_type,
                    storage_table,
                    interval,
                    interval,
                ],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {
            "dataset_id": row[0],
            "data_domain": row[1],
            "data_type": row[2],
            "symbol": row[3],
            "exchange": row[4],
            "interval": row[5],
            "start_ts": row[6],
            "end_ts": row[7],
            "row_count": row[8],
            "quality_status": row[9],
            "data_source": "real",
        }
