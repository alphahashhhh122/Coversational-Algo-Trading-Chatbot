from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect


SUPPORTED_ASSET_CLASSES = {
    "equity",
    "index",
    "futures",
    "options",
    "commodity",
    "crypto",
}
_FEATURE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
_RESERVED_FEATURE_NAMES = {"close", "high", "low", "open", "price", "volume"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MarketDataIngestionService:
    """Store explicit local OHLCV imports with replayable provenance."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def import_ohlcv(
        self,
        *,
        dataset_id: str,
        asset_class: str,
        symbol: str,
        exchange: str,
        interval: str,
        candles: list[dict[str, Any]],
        source_name: str,
    ) -> dict[str, Any]:
        normalized_asset = asset_class.lower()
        if normalized_asset not in SUPPORTED_ASSET_CLASSES:
            raise ValueError(
                "Generic OHLCV import supports equity, index, futures, "
                "options, commodity, and crypto."
            )
        normalized = self._validate_candles(candles)
        canonical = {
            "dataset_id": dataset_id,
            "asset_class": normalized_asset,
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "interval": interval,
            "candles": normalized,
        }
        payload = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
        encoded = payload.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        source_id = f"local_ohlcv_{digest[:20]}"
        now = utc_now()
        report_id = f"quality_{digest[:20]}"
        report_path = f"database:market_ohlcv/{dataset_id}/{digest}"

        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            existing = con.execute(
                """
                SELECT source_id, storage_table
                FROM data_catalog
                WHERE dataset_id = ?
                """,
                [dataset_id],
            ).fetchone()
            if existing and existing[1] != "market_ohlcv":
                raise ValueError(
                    "dataset_id already belongs to a different storage "
                    "contract; choose a new dataset_id."
                )
            if existing:
                con.execute(
                    "DELETE FROM market_ohlcv WHERE source_id = ?",
                    [existing[0]],
                )
            con.execute(
                """
                INSERT OR REPLACE INTO raw_file_registry VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    source_id,
                    "local_api",
                    source_name,
                    digest,
                    len(encoded),
                    now,
                    len(normalized),
                    len(normalized),
                    0,
                    0,
                ],
            )
            con.executemany(
                """
                INSERT INTO market_ohlcv VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    [
                        source_id,
                        normalized_asset,
                        canonical["symbol"],
                        canonical["exchange"],
                        interval,
                        candle["timestamp"],
                        candle["open"],
                        candle["high"],
                        candle["low"],
                        candle["close"],
                        candle["volume"],
                        "clean",
                        now,
                    ]
                    for candle in normalized
                ],
            )
            con.execute(
                """
                INSERT OR REPLACE INTO data_catalog VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    dataset_id,
                    "market_data",
                    f"{normalized_asset}_ohlcv",
                    canonical["symbol"],
                    canonical["exchange"],
                    interval,
                    normalized[0]["timestamp"],
                    normalized[-1]["timestamp"],
                    len(normalized),
                    "market_ohlcv",
                    source_id,
                    "clean",
                    report_path,
                    now,
                ],
            )
            con.execute(
                """
                INSERT OR REPLACE INTO data_quality_reports VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    report_id,
                    source_id,
                    dataset_id,
                    report_path,
                    len(normalized),
                    len(normalized),
                    len(normalized),
                    0,
                    0,
                    0,
                    "clean",
                    now,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

        return {
            "dataset_id": dataset_id,
            "asset_class": normalized_asset,
            "symbol": canonical["symbol"],
            "exchange": canonical["exchange"],
            "interval": interval,
            "row_count": len(normalized),
            "storage_table": "market_ohlcv",
            "source_id": source_id,
            "source_sha256": digest,
            "quality_status": "clean",
            "quality_report_path": report_path,
            "data_source": "local_user_supplied",
            "no_synthetic_fallback": True,
        }

    def import_features(
        self,
        *,
        dataset_id: str,
        symbol: str,
        exchange: str,
        observations: list[dict[str, Any]],
        source_name: str,
    ) -> dict[str, Any]:
        """Store point-in-time numeric features for deterministic rule specs."""
        normalized = self._validate_feature_observations(observations)
        canonical = {
            "dataset_id": dataset_id,
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "observations": normalized,
        }
        payload = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
        encoded = payload.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        source_id = f"local_features_{digest[:20]}"
        now = utc_now()
        report_id = f"quality_{digest[:20]}"
        report_path = f"database:market_features/{dataset_id}/{digest}"

        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            existing = con.execute(
                """
                SELECT source_id, storage_table
                FROM data_catalog
                WHERE dataset_id = ?
                """,
                [dataset_id],
            ).fetchone()
            if existing and existing[1] != "market_features":
                raise ValueError(
                    "dataset_id already belongs to a different storage "
                    "contract; choose a new dataset_id."
                )
            if existing:
                con.execute(
                    "DELETE FROM market_features WHERE source_id = ?",
                    [existing[0]],
                )
            con.execute(
                """
                INSERT OR REPLACE INTO raw_file_registry VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    source_id,
                    "local_api",
                    source_name,
                    digest,
                    len(encoded),
                    now,
                    len(normalized),
                    len(normalized),
                    0,
                    0,
                ],
            )
            con.executemany(
                """
                INSERT INTO market_features VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    [
                        source_id,
                        canonical["symbol"],
                        canonical["exchange"],
                        observation["feature_name"],
                        observation["observed_at"],
                        observation["available_at"],
                        observation["value"],
                        json.dumps(
                            observation["metadata"],
                            sort_keys=True,
                            separators=(",", ":"),
                            default=_json_default,
                        ),
                        "clean",
                        now,
                    ]
                    for observation in normalized
                ],
            )
            con.execute(
                """
                INSERT OR REPLACE INTO data_catalog VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    dataset_id,
                    "alternative_data",
                    "feature_series",
                    canonical["symbol"],
                    canonical["exchange"],
                    None,
                    min(item["observed_at"] for item in normalized),
                    max(item["available_at"] for item in normalized),
                    len(normalized),
                    "market_features",
                    source_id,
                    "clean",
                    report_path,
                    now,
                ],
            )
            con.execute(
                """
                INSERT OR REPLACE INTO data_quality_reports VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    report_id,
                    source_id,
                    dataset_id,
                    report_path,
                    len(normalized),
                    len(normalized),
                    len(normalized),
                    0,
                    0,
                    0,
                    "clean",
                    now,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

        return {
            "dataset_id": dataset_id,
            "symbol": canonical["symbol"],
            "exchange": canonical["exchange"],
            "row_count": len(normalized),
            "feature_names": sorted(
                {item["feature_name"] for item in normalized}
            ),
            "storage_table": "market_features",
            "source_id": source_id,
            "source_sha256": digest,
            "quality_status": "clean",
            "quality_report_path": report_path,
            "data_source": "local_user_supplied",
            "point_in_time_safe": True,
            "no_synthetic_fallback": True,
        }

    @staticmethod
    def _validate_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(candles) < 2:
            raise ValueError("At least two OHLCV candles are required.")
        normalized: list[dict[str, Any]] = []
        seen_timestamps: set[datetime] = set()
        for index, candle in enumerate(candles):
            try:
                timestamp = candle["timestamp"]
                open_price = float(candle["open"])
                high = float(candle["high"])
                low = float(candle["low"])
                close = float(candle["close"])
                volume = float(candle.get("volume", 0.0))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid OHLCV candle at index {index}.") from exc
            if not isinstance(timestamp, datetime):
                raise ValueError(f"Invalid timestamp at candle index {index}.")
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None) if timestamp.tzinfo else timestamp
            values = (open_price, high, low, close, volume)
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"Non-finite OHLCV value at candle index {index}.")
            if min(open_price, high, low, close) <= 0:
                raise ValueError(f"OHLC prices must be positive at candle index {index}.")
            if volume < 0:
                raise ValueError(f"Volume must be non-negative at candle index {index}.")
            if high < max(open_price, close) or low > min(open_price, close):
                raise ValueError(f"OHLC bounds are invalid at candle index {index}.")
            if timestamp in seen_timestamps:
                raise ValueError(f"Duplicate timestamp at candle index {index}.")
            seen_timestamps.add(timestamp)
            normalized.append(
                {
                    "timestamp": timestamp,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )
        normalized.sort(key=lambda candle: candle["timestamp"])
        return normalized

    @staticmethod
    def _validate_feature_observations(
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not observations:
            raise ValueError("At least one feature observation is required.")
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, datetime, datetime]] = set()
        for index, observation in enumerate(observations):
            try:
                feature_name = str(observation["feature_name"])
                observed_at = observation["observed_at"]
                available_at = observation["available_at"]
                value = float(observation["value"])
                metadata = observation.get("metadata") or {}
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid feature observation at index {index}."
                ) from exc
            if (
                not _FEATURE_NAME_PATTERN.fullmatch(feature_name)
                or feature_name.lower() in _RESERVED_FEATURE_NAMES
            ):
                raise ValueError(
                    "feature_name must be a non-reserved identifier using "
                    "letters, numbers, and underscores."
                )
            if not isinstance(observed_at, datetime) or not isinstance(
                available_at, datetime
            ):
                raise ValueError(
                    f"Feature observation {index} needs observed_at and "
                    "available_at timestamps."
                )
            observed_at = _normalize_timestamp(observed_at)
            available_at = _normalize_timestamp(available_at)
            if available_at < observed_at:
                raise ValueError(
                    "available_at must be on or after observed_at to prevent "
                    "ambiguous point-in-time alignment."
                )
            if not math.isfinite(value):
                raise ValueError(
                    f"Non-finite feature value at observation index {index}."
                )
            if not isinstance(metadata, dict):
                raise ValueError("Feature metadata must be an object.")
            try:
                json.dumps(metadata, sort_keys=True, default=_json_default)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Feature metadata at index {index} is not serializable."
                ) from exc
            key = (feature_name, observed_at, available_at)
            if key in seen:
                raise ValueError(
                    f"Duplicate feature observation at index {index}."
                )
            seen.add(key)
            normalized.append(
                {
                    "feature_name": feature_name,
                    "observed_at": observed_at,
                    "available_at": available_at,
                    "value": value,
                    "metadata": metadata,
                }
            )
        normalized.sort(
            key=lambda item: (
                item["feature_name"],
                item["available_at"],
                item["observed_at"],
            )
        )
        return normalized


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported canonical OHLCV value: {type(value).__name__}")


def _normalize_timestamp(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc).replace(tzinfo=None)
        if value.tzinfo
        else value
    )
