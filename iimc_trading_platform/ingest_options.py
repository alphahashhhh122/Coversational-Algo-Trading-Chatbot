from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from .db import DEFAULT_DB_PATH, connect
from .domain import DataDomain, DataQualityStatus
from .infrastructure import initialize_database


REQUIRED_COLUMNS = {
    "datetime",
    "strike_label",
    "option_type",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
    "iv",
    "strike_price",
    "spot",
}


@dataclass
class FileQuality:
    file_name: str
    total_rows: int = 0
    valid_rows: int = 0
    duplicate_rows: int = 0
    invalid_rows: int = 0
    missing_required_rows: int = 0
    invalid_datetime_rows: int = 0
    invalid_numeric_rows: int = 0
    invalid_ohlc_rows: int = 0


@dataclass
class IngestionReport:
    run_id: str
    source_id: str
    source_path: str
    source_sha256: str
    underlying: str
    exchange: str
    expiry: str
    interval: str
    started_at: str
    finished_at: str | None = None
    total_rows: int = 0
    valid_rows: int = 0
    inserted_rows: int = 0
    duplicate_rows: int = 0
    invalid_rows: int = 0
    pair_gap_count: int = 0
    start_ts: str | None = None
    end_ts: str | None = None
    strike_min: float | None = None
    strike_max: float | None = None
    quality_status: str = "unknown"
    files: list[FileQuality] = field(default_factory=list)
    invalid_reasons: dict[str, int] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_metadata(zip_path: Path) -> tuple[str, str, str]:
    name = zip_path.stem.upper()
    underlying = name.split("_", 1)[0] if "_" in name else "UNKNOWN"

    interval_match = re.search(r"_(\d+[MHD])_", name)
    interval = interval_match.group(1).lower() if interval_match else "unknown"

    expiry_match = re.search(r"_(MONTH_E\d+|WEEK_E\d+|CURRENT_MONTH|NEXT_MONTH)", name)
    expiry = expiry_match.group(1) if expiry_match else "UNKNOWN_EXPIRY"
    return underlying, expiry, interval


def parse_float(value: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError("empty numeric value")
    return float(value)


def parse_int(value: str) -> int:
    if value is None or str(value).strip() == "":
        raise ValueError("empty integer value")
    return int(float(value))


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def normalize_option_type(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"CE", "CALL"}:
        return "CALL"
    if normalized in {"PE", "PUT"}:
        return "PUT"
    raise ValueError(f"invalid option_type {value!r}")


def validate_row(row: dict[str, str]) -> tuple[dict[str, object] | None, str | None]:
    if not REQUIRED_COLUMNS.issubset(row.keys()):
        return None, "missing_required_columns"

    if any(row.get(col) in (None, "") for col in REQUIRED_COLUMNS):
        return None, "missing_required_value"

    try:
        ts = parse_timestamp(row["datetime"])
    except Exception:
        return None, "invalid_datetime"

    try:
        open_ = parse_float(row["open"])
        high = parse_float(row["high"])
        low = parse_float(row["low"])
        close = parse_float(row["close"])
        volume = parse_int(row["volume"])
        oi = parse_int(row["oi"])
        iv = parse_float(row["iv"])
        strike_price = parse_float(row["strike_price"])
        spot = parse_float(row["spot"])
        option_type = normalize_option_type(row["option_type"])
    except Exception:
        return None, "invalid_numeric"

    if high < max(open_, close, low) or low > min(open_, close, high):
        return None, "invalid_ohlc"
    if volume < 0 or oi < 0 or strike_price <= 0:
        return None, "invalid_numeric"

    return (
        {
            "timestamp": ts,
            "strike_label": row["strike_label"].strip().upper(),
            "option_type": option_type,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "oi": oi,
            "iv": iv,
            "strike_price": strike_price,
            "spot": spot,
        },
        None,
    )


def iter_csv_entries(zip_path: Path):
    with ZipFile(zip_path) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(".csv"):
                continue
            with archive.open(entry) as raw:
                text = (line.decode("utf-8-sig") for line in raw)
                yield entry.filename, csv.DictReader(text)


def ingest_options_zip(
    zip_path: Path,
    db_path: Path = DEFAULT_DB_PATH,
    artifacts_dir: Path = Path("artifacts/data_quality"),
    exchange: str = "NFO",
) -> IngestionReport:
    zip_path = zip_path.resolve()
    underlying, expiry, interval = infer_metadata(zip_path)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = f"ingest_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    source_hash = sha256_file(zip_path)
    source_id = f"{zip_path.stem}_{source_hash[:12]}"

    report = IngestionReport(
        run_id=run_id,
        source_id=source_id,
        source_path=str(zip_path),
        source_sha256=source_hash,
        underlying=underlying,
        exchange=exchange,
        expiry=expiry,
        interval=interval,
        started_at=now.isoformat(timespec="seconds"),
    )

    unique_rows: dict[tuple[object, ...], dict[str, object]] = {}
    invalid_reasons: Counter[str] = Counter()
    pair_sides: dict[tuple[datetime, float], set[str]] = defaultdict(set)

    for file_name, reader in iter_csv_entries(zip_path):
        file_report = FileQuality(file_name=file_name)
        for raw_row in reader:
            file_report.total_rows += 1
            report.total_rows += 1
            row, reason = validate_row(raw_row)
            if reason:
                file_report.invalid_rows += 1
                report.invalid_rows += 1
                invalid_reasons[reason] += 1
                if reason == "missing_required_columns" or reason == "missing_required_value":
                    file_report.missing_required_rows += 1
                elif reason == "invalid_datetime":
                    file_report.invalid_datetime_rows += 1
                elif reason == "invalid_numeric":
                    file_report.invalid_numeric_rows += 1
                elif reason == "invalid_ohlc":
                    file_report.invalid_ohlc_rows += 1
                continue

            key = (
                underlying,
                exchange,
                expiry,
                interval,
                row["timestamp"],
                row["strike_price"],
                row["option_type"],
            )
            if key in unique_rows:
                file_report.duplicate_rows += 1
                report.duplicate_rows += 1
                continue

            unique_rows[key] = row
            pair_sides[(row["timestamp"], row["strike_price"])].add(str(row["option_type"]))
            file_report.valid_rows += 1
            report.valid_rows += 1

        report.files.append(file_report)

    report.pair_gap_count = sum(1 for sides in pair_sides.values() if sides != {"CALL", "PUT"})
    report.invalid_reasons = dict(invalid_reasons)

    created_at = datetime.now(timezone.utc).replace(tzinfo=None)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / f"{run_id}.json"

    temp_dir = artifacts_dir / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_csv = temp_dir / f"{run_id}_clean_options.csv"
    with temp_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "underlying",
                "exchange",
                "expiry",
                "interval",
                "timestamp",
                "strike_label",
                "strike_price",
                "option_type",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "oi",
                "iv",
                "spot",
                "source_id",
                "source_file",
                "quality_status",
                "created_at",
            ]
        )
        for row in unique_rows.values():
            writer.writerow(
                [
                    underlying,
                    exchange,
                    expiry,
                    interval,
                    row["timestamp"].isoformat(sep=" "),
                    row["strike_label"],
                    row["strike_price"],
                    row["option_type"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    row["oi"],
                    row["iv"],
                    row["spot"],
                    source_id,
                    zip_path.name,
                    DataQualityStatus.CLEAN.value,
                    created_at.isoformat(sep=" "),
                ]
            )

    initialize_database(db_path)
    con = connect(db_path)
    try:
        con.execute("BEGIN TRANSACTION")
        before_count = con.execute("SELECT COUNT(*) FROM options_ohlcv").fetchone()[0]
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE tmp_options_ohlcv AS
            SELECT
                underlying::VARCHAR AS underlying,
                exchange::VARCHAR AS exchange,
                expiry::VARCHAR AS expiry,
                interval::VARCHAR AS interval,
                CAST(timestamp AS TIMESTAMP) AS timestamp,
                strike_label::VARCHAR AS strike_label,
                CAST(strike_price AS DOUBLE) AS strike_price,
                option_type::VARCHAR AS option_type,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS BIGINT) AS volume,
                CAST(oi AS BIGINT) AS oi,
                CAST(iv AS DOUBLE) AS iv,
                CAST(spot AS DOUBLE) AS spot,
                source_id::VARCHAR AS source_id,
                source_file::VARCHAR AS source_file,
                quality_status::VARCHAR AS quality_status,
                CAST(created_at AS TIMESTAMP) AS created_at
            FROM read_csv_auto(?, header = true)
            """,
            [str(temp_csv)],
        )
        con.execute(
            """
            INSERT OR REPLACE INTO options_ohlcv
            SELECT
                underlying,
                exchange,
                expiry,
                interval,
                timestamp,
                strike_label,
                strike_price,
                option_type,
                open,
                high,
                low,
                close,
                volume,
                oi,
                iv,
                spot,
                source_id,
                source_file,
                quality_status,
                created_at
            FROM tmp_options_ohlcv
            """
        )
        after_count = con.execute("SELECT COUNT(*) FROM options_ohlcv").fetchone()[0]
        report.inserted_rows = after_count - before_count

        start_ts, end_ts, strike_min, strike_max, row_count = con.execute(
            """
            SELECT MIN(timestamp), MAX(timestamp), MIN(strike_price), MAX(strike_price), COUNT(*)
            FROM options_ohlcv
            WHERE underlying = ? AND exchange = ? AND expiry = ? AND interval = ?
            """,
            [underlying, exchange, expiry, interval],
        ).fetchone()

        quality_status = DataQualityStatus.CLEAN
        if report.invalid_rows or report.pair_gap_count:
            quality_status = DataQualityStatus.CLEAN_WITH_WARNINGS
        if not row_count:
            quality_status = DataQualityStatus.EMPTY

        report.start_ts = start_ts.isoformat(sep=" ") if start_ts else None
        report.end_ts = end_ts.isoformat(sep=" ") if end_ts else None
        report.strike_min = strike_min
        report.strike_max = strike_max
        report.quality_status = quality_status.value
        report.finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(
            timespec="seconds"
        )

        dataset_id = f"{underlying}_{expiry}_{interval}_options"
        con.execute(
            """
            INSERT OR REPLACE INTO raw_file_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source_id,
                str(zip_path),
                zip_path.name,
                source_hash,
                zip_path.stat().st_size,
                created_at,
                report.total_rows,
                report.valid_rows,
                report.duplicate_rows,
                report.invalid_rows,
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
                DataDomain.MARKET_DATA.value,
                "options_ohlcv",
                underlying,
                exchange,
                interval,
                start_ts,
                end_ts,
                row_count,
                "options_ohlcv",
                source_id,
                quality_status.value,
                str(report_path),
                created_at,
            ],
        )
        con.execute(
            """
            INSERT OR REPLACE INTO data_quality_reports VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                run_id,
                source_id,
                dataset_id,
                str(report_path),
                report.total_rows,
                report.valid_rows,
                report.inserted_rows,
                report.duplicate_rows,
                report.invalid_rows,
                report.pair_gap_count,
                quality_status.value,
                created_at,
            ],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
        temp_csv.unlink(missing_ok=True)

    report_path.write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NIFTY options ZIP into DuckDB.")
    parser.add_argument("--zip", required=True, type=Path, help="Path to options ZIP file.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, type=Path, help="DuckDB database path.")
    parser.add_argument(
        "--artifacts",
        default=Path("artifacts/data_quality"),
        type=Path,
        help="Directory for quality report JSON files.",
    )
    parser.add_argument("--exchange", default="NFO", help="Exchange label for options data.")
    args = parser.parse_args()

    report = ingest_options_zip(args.zip, args.db, args.artifacts, args.exchange)
    print(json.dumps(asdict(report), indent=2, default=str))


if __name__ == "__main__":
    main()
