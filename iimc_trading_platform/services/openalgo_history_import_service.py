from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..infrastructure.openalgo import OpenAlgoClient, OpenAlgoError
from .instrument_discovery_service import InstrumentDiscoveryService
from .market_data_ingestion_service import (
    SUPPORTED_ASSET_CLASSES,
    MarketDataIngestionService,
)


class OpenAlgoHistoryImportService:
    """Import verified OpenAlgo candles as governed local research data."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.ingestion = MarketDataIngestionService(config.database_path)

    def import_history(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_asset_class = asset_class.lower()
        if normalized_asset_class not in SUPPORTED_ASSET_CLASSES:
            raise ValueError(f"Unsupported asset class: {asset_class}")
        if not self.config.openalgo_api_key:
            raise ValueError("OPENALGO_API_KEY is required to import history")

        resolution = InstrumentDiscoveryService(self.config).validate_symbol(
            symbol=symbol,
            exchange=exchange,
        )
        if not resolution.get("ok"):
            raise ValueError(
                "OpenAlgo could not validate the requested instrument: "
                f"{resolution.get('message', resolution.get('status'))}"
            )
        resolved_symbol = str(resolution["resolved_symbol"]).upper()
        resolved_exchange = str(resolution["resolved_exchange"]).upper()
        try:
            response = self._client().historical(
                symbol=resolved_symbol,
                exchange=resolved_exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
            )
        except OpenAlgoError as exc:
            raise ValueError(f"OpenAlgo history import failed: {exc}") from exc

        candles = _normalize_history_candles(response.get("data"))
        if len(candles) < 2:
            raise ValueError("OpenAlgo returned fewer than two usable candles")
        target_dataset_id = dataset_id or _default_dataset_id(
            symbol=resolved_symbol,
            exchange=resolved_exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
        imported = self.ingestion.import_ohlcv(
            dataset_id=target_dataset_id,
            asset_class=normalized_asset_class,
            symbol=resolved_symbol,
            exchange=resolved_exchange,
            interval=interval,
            candles=candles,
            source_name=(
                "openalgo_history:"
                f"{resolved_symbol}:{resolved_exchange}:{interval}:"
                f"{start_date}:{end_date}"
            ),
            source_kind="openalgo_api",
            data_source="openalgo_history",
        )
        return {
            **imported,
            "provider": "openalgo",
            "requested_symbol": symbol.upper(),
            "requested_exchange": exchange.upper(),
            "resolved_symbol": resolved_symbol,
            "resolved_exchange": resolved_exchange,
            "start_date": start_date,
            "end_date": end_date,
            "instrument": resolution.get("instrument", {}),
        }

    def _client(self) -> OpenAlgoClient:
        return OpenAlgoClient(
            self.config.openalgo_base_url,
            self.config.openalgo_api_key or "",
        )


def _normalize_history_candles(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("OpenAlgo history response must contain a candle list")
    candles_by_timestamp: dict[datetime, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"OpenAlgo history candle {index} is not an object")
        try:
            timestamp = _timestamp(item.get("timestamp"))
            candle = {
                "timestamp": timestamp,
                "open": _number(item.get("open"), "open", index),
                "high": _number(item.get("high"), "high", index),
                "low": _number(item.get("low"), "low", index),
                "close": _number(item.get("close"), "close", index),
                "volume": _number(item.get("volume", 0), "volume", index),
            }
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid OpenAlgo history candle {index}: {exc}") from exc
        candles_by_timestamp[timestamp] = candle
    return [candles_by_timestamp[key] for key in sorted(candles_by_timestamp)]


def _timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        epoch = float(value)
        if epoch > 10_000_000_000:
            epoch /= 1000
        return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.astimezone(timezone.utc).replace(tzinfo=None)
            if parsed.tzinfo
            else parsed
        )
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc).replace(tzinfo=None)
            if value.tzinfo
            else value
        )
    raise ValueError("timestamp is required")


def _number(value: Any, name: str, index: int) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} at candle {index} must be finite")
    if name in {"open", "high", "low", "close"} and number <= 0:
        raise ValueError(f"{name} at candle {index} must be positive")
    if name == "volume" and number < 0:
        raise ValueError(f"volume at candle {index} must not be negative")
    return number


def _default_dataset_id(
    *,
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
) -> str:
    safe_interval = re.sub(r"[^a-z0-9]+", "", interval.lower()) or "data"
    start = re.sub(r"[^0-9]", "", start_date)[:8] or "start"
    end = re.sub(r"[^0-9]", "", end_date)[:8] or "end"
    return f"openalgo_{symbol.lower()}_{exchange.lower()}_{safe_interval}_{start}_{end}"
