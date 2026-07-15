from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect


ANALYTICAL_TABLES = {"market_ohlcv", "options_ohlcv"}
ARCHIVE_PREFIXES = ("legacy_",)


@dataclass(frozen=True)
class ForeignKey:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    on_delete: str = "RESTRICT"


FOREIGN_KEYS = [
    ForeignKey(
        "auth_sessions",
        "user_id",
        "app_users",
        "user_id",
        "CASCADE",
    ),
    ForeignKey(
        "chat_messages",
        "session_id",
        "chat_sessions",
        "session_id",
        "CASCADE",
    ),
    ForeignKey(
        "knowledge_chunks",
        "document_id",
        "knowledge_documents",
        "document_id",
        "CASCADE",
    ),
    ForeignKey(
        "ai_eval_results",
        "eval_run_id",
        "ai_eval_runs",
        "eval_run_id",
        "CASCADE",
    ),
    ForeignKey(
        "retrieval_eval_results",
        "eval_run_id",
        "retrieval_eval_runs",
        "eval_run_id",
        "CASCADE",
    ),
    ForeignKey(
        "job_runs",
        "job_id",
        "scheduled_jobs",
        "job_id",
        "CASCADE",
    ),
    ForeignKey(
        "portfolio_positions",
        "portfolio_id",
        "portfolios",
        "portfolio_id",
        "CASCADE",
    ),
    ForeignKey(
        "portfolio_ledger",
        "portfolio_id",
        "portfolios",
        "portfolio_id",
    ),
    ForeignKey(
        "portfolio_risk_decisions",
        "portfolio_id",
        "portfolios",
        "portfolio_id",
    ),
    ForeignKey(
        "risk_reservations",
        "portfolio_id",
        "portfolios",
        "portfolio_id",
    ),
    ForeignKey(
        "strategy_signals",
        "run_id",
        "strategy_runs",
        "run_id",
        "CASCADE",
    ),
    ForeignKey(
        "risk_decisions",
        "run_id",
        "strategy_runs",
        "run_id",
        "CASCADE",
    ),
    ForeignKey(
        "order_events",
        "run_id",
        "strategy_runs",
        "run_id",
        "CASCADE",
    ),
    ForeignKey(
        "order_state_events",
        "order_id",
        "order_events",
        "order_id",
        "CASCADE",
    ),
    ForeignKey(
        "trade_fills",
        "order_id",
        "order_events",
        "order_id",
    ),
    ForeignKey(
        "performance_summaries",
        "run_id",
        "strategy_runs",
        "run_id",
        "CASCADE",
    ),
    ForeignKey(
        "experiment_manifests",
        "run_id",
        "strategy_runs",
        "run_id",
        "CASCADE",
    ),
    ForeignKey(
        "robustness_trials",
        "experiment_id",
        "robustness_experiments",
        "experiment_id",
        "CASCADE",
    ),
    ForeignKey(
        "market_news_articles",
        "fetch_id",
        "market_news_fetches",
        "fetch_id",
        "CASCADE",
    ),
]


INDEXES: dict[str, list[tuple[str, ...]]] = {
    "approval_requests": [("status", "created_at")],
    "audit_events": [("entity_type", "entity_id", "created_at")],
    "auth_sessions": [("user_id",), ("expires_at",)],
    "chat_messages": [("session_id", "created_at")],
    "freshness_assessments": [
        ("dataset_id", "purpose", "created_at"),
    ],
    "job_runs": [("job_id", "started_at"), ("status", "started_at")],
    "market_news_articles": [("symbol", "retrieved_at"), ("sha256",)],
    "market_news_fetches": [("provider", "retrieved_at")],
    "openalgo_snapshots": [("snapshot_type", "captured_at")],
    "operational_alerts": [("status", "severity", "last_seen_at")],
    "order_events": [("run_id", "created_at"), ("status", "updated_at")],
    "order_intents": [("status", "updated_at")],
    "portfolio_ledger": [("portfolio_id", "created_at")],
    "portfolio_risk_decisions": [("portfolio_id", "created_at")],
    "retrieval_events": [("created_at",)],
    "risk_decisions": [("run_id", "created_at")],
    "risk_reservations": [("portfolio_id", "status", "expires_at")],
    "strategy_runs": [("dataset_id", "started_at"), ("status",)],
    "strategy_signals": [("run_id", "timestamp")],
    "tool_calls": [("session_id", "created_at"), ("status", "created_at")],
    "trade_fills": [("run_id", "filled_at")],
    "work_tasks": [("status", "next_attempt_at")],
}


class StorageMigrationService:
    def __init__(self, db_path: Path, artifacts_dir: Path) -> None:
        self.db_path = db_path
        self.artifacts_dir = artifacts_dir

    def generate(self) -> dict[str, Any]:
        schema = self._schema()
        placements = {
            table: _placement(table)
            for table in schema
        }
        unclassified = [
            table
            for table, placement in placements.items()
            if placement is None
        ]
        if unclassified:
            raise ValueError(
                "Unclassified storage tables: "
                + ", ".join(unclassified)
            )
        orphan_counts = self._foreign_key_orphans(schema)
        invalid = {
            name: count
            for name, count in orphan_counts.items()
            if count > 0
        }
        if invalid:
            raise ValueError(
                "Declared PostgreSQL foreign keys have orphan rows: "
                + json.dumps(invalid, sort_keys=True)
            )
        transaction_tables = [
            table
            for table, placement in placements.items()
            if placement == "postgresql"
        ]
        ddl = self._postgresql_ddl(
            schema,
            transaction_tables,
        )
        schema_sha256 = hashlib.sha256(
            json.dumps(
                schema,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        ddl_sha256 = hashlib.sha256(
            ddl.encode("utf-8")
        ).hexdigest()
        generated_at = _utc_now()
        manifest = {
            "manifest_version": "storage_migration_v1",
            "generated_at": generated_at,
            "source": {
                "engine": "duckdb",
                "database_path": str(self.db_path.resolve()),
                "schema_sha256": schema_sha256,
            },
            "targets": {
                "transactional": "postgresql",
                "analytical": "object_storage_parquet",
                "archive": "object_storage_archive",
            },
            "tables": [
                {
                    "table": table,
                    "row_count": schema[table]["row_count"],
                    "placement": placements[table],
                    "migration_strategy": _strategy(table),
                    "primary_key": schema[table]["primary_key"],
                    "partition_keys": (
                        ["underlying", "expiry", "trade_date"]
                        if table == "options_ohlcv"
                        else []
                    ),
                }
                for table in sorted(schema)
            ],
            "foreign_key_orphan_counts": orphan_counts,
            "postgresql_ddl_sha256": ddl_sha256,
            "cutover": [
                "Freeze writes and complete a verified DuckDB backup.",
                "Bulk load PostgreSQL tables and partitioned Parquet history.",
                "Verify row counts, primary keys, foreign keys, and checksums.",
                "Run read-only shadow traffic against the new stores.",
                "Switch transactional writes, then retain rollback access.",
            ],
        }
        output_dir = self.artifacts_dir / "migrations"
        output_dir.mkdir(parents=True, exist_ok=True)
        ddl_path = output_dir / "postgresql_schema.sql"
        manifest_path = output_dir / "storage_migration_manifest.json"
        ddl_path.write_text(ddl, encoding="utf-8")
        manifest_path.write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )
        counts = {
            placement: sum(
                1 for value in placements.values() if value == placement
            )
            for placement in {
                "postgresql",
                "object_storage_parquet",
                "object_storage_archive",
            }
        }
        return {
            "manifest_version": manifest["manifest_version"],
            "generated_at": generated_at,
            "table_count": len(schema),
            "placement_counts": counts,
            "foreign_key_count": len(orphan_counts),
            "foreign_keys_verified": True,
            "schema_sha256": schema_sha256,
            "ddl_sha256": ddl_sha256,
            "ddl_path": str(ddl_path.resolve()),
            "manifest_path": str(manifest_path.resolve()),
        }

    def export_analytical_history(self) -> dict[str, Any]:
        export_id = (
            f"options_ohlcv_{_utc_now().strftime('%Y%m%dT%H%M%SZ')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        target = (
            self.artifacts_dir
            / "migrations"
            / "object_storage"
            / export_id
        )
        target.mkdir(parents=True, exist_ok=False)
        con = connect(self.db_path)
        try:
            source_count = con.execute(
                "SELECT COUNT(*) FROM options_ohlcv"
            ).fetchone()[0]
            con.execute(
                f"""
                COPY (
                    SELECT *,
                           CAST("timestamp" AS DATE) AS trade_date
                    FROM options_ohlcv
                )
                TO {_sql_literal(target)}
                (
                    FORMAT PARQUET,
                    PARTITION_BY (underlying, expiry, trade_date),
                    COMPRESSION ZSTD
                )
                """
            )
            parquet_glob = (
                target.resolve().as_posix() + "/**/*.parquet"
            )
            restored_count = con.execute(
                f"""
                SELECT COUNT(*)
                FROM read_parquet(
                    {_string_literal(parquet_glob)},
                    hive_partitioning = TRUE
                )
                """
            ).fetchone()[0]
        finally:
            con.close()
        if restored_count != source_count:
            raise ValueError(
                "Analytical export row count does not match source"
            )
        files = [
            {
                "path": path.relative_to(target).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
            for path in sorted(target.rglob("*.parquet"))
        ]
        if source_count and not files:
            raise ValueError("Analytical export created no Parquet files")
        manifest = {
            "export_id": export_id,
            "format": "parquet",
            "compression": "zstd",
            "source_table": "options_ohlcv",
            "partition_keys": [
                "underlying",
                "expiry",
                "trade_date",
            ],
            "source_row_count": source_count,
            "verified_row_count": restored_count,
            "file_count": len(files),
            "files": files,
            "created_at": _utc_now(),
        }
        manifest_path = target / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )
        return {
            **{
                key: value
                for key, value in manifest.items()
                if key != "files"
            },
            "target_path": str(target.resolve()),
            "manifest_path": str(manifest_path.resolve()),
        }

    def _schema(self) -> dict[str, dict[str, Any]]:
        con = connect(self.db_path)
        try:
            tables = [
                row[0]
                for row in con.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                ).fetchall()
            ]
            constraints = con.execute(
                """
                SELECT table_name, constraint_type,
                       constraint_column_names, constraint_name
                FROM duckdb_constraints()
                WHERE schema_name = 'main'
                  AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
                ORDER BY table_name, constraint_index
                """
            ).fetchall()
            constraints_by_table: dict[str, list[dict[str, Any]]] = {}
            for table, kind, columns, name in constraints:
                constraints_by_table.setdefault(table, []).append(
                    {
                        "type": kind,
                        "columns": list(columns),
                        "name": name,
                    }
                )
            schema = {}
            for table in tables:
                columns = con.execute(
                    """
                    SELECT column_name, data_type, is_nullable,
                           column_default, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = 'main' AND table_name = ?
                    ORDER BY ordinal_position
                    """,
                    [table],
                ).fetchall()
                table_constraints = constraints_by_table.get(table, [])
                primary = next(
                    (
                        item["columns"]
                        for item in table_constraints
                        if item["type"] == "PRIMARY KEY"
                    ),
                    [],
                )
                schema[table] = {
                    "columns": [
                        {
                            "name": row[0],
                            "type": row[1],
                            "nullable": row[2] == "YES",
                            "default": row[3],
                        }
                        for row in columns
                    ],
                    "constraints": table_constraints,
                    "primary_key": primary,
                    "row_count": con.execute(
                        f"SELECT COUNT(*) FROM {_quote(table)}"
                    ).fetchone()[0],
                }
        finally:
            con.close()
        return schema

    def _foreign_key_orphans(
        self,
        schema: dict[str, dict[str, Any]],
    ) -> dict[str, int]:
        con = connect(self.db_path)
        try:
            counts = {}
            for relation in FOREIGN_KEYS:
                if (
                    relation.child_table not in schema
                    or relation.parent_table not in schema
                ):
                    continue
                name = (
                    f"{relation.child_table}.{relation.child_column}"
                    f"->{relation.parent_table}.{relation.parent_column}"
                )
                counts[name] = con.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {_quote(relation.child_table)} AS child
                    LEFT JOIN {_quote(relation.parent_table)} AS parent
                      ON child.{_quote(relation.child_column)}
                       = parent.{_quote(relation.parent_column)}
                    WHERE child.{_quote(relation.child_column)} IS NOT NULL
                      AND parent.{_quote(relation.parent_column)} IS NULL
                    """
                ).fetchone()[0]
        finally:
            con.close()
        return counts

    def _postgresql_ddl(
        self,
        schema: dict[str, dict[str, Any]],
        tables: list[str],
    ) -> str:
        statements = [
            "-- Generated from the governed DuckDB schema.",
            "-- Existing naive timestamps are interpreted as UTC during load.",
            "BEGIN;",
            "",
        ]
        for table in sorted(tables):
            definition = schema[table]
            lines = []
            for column in definition["columns"]:
                line = (
                    f"  {_quote(column['name'])} "
                    f"{_postgres_type(column['type'], column['name'])}"
                )
                if not column["nullable"]:
                    line += " NOT NULL"
                default = _postgres_default(column["default"])
                if default is not None:
                    line += f" DEFAULT {default}"
                lines.append(line)
            for constraint in definition["constraints"]:
                kind = constraint["type"]
                columns = ", ".join(
                    _quote(column)
                    for column in constraint["columns"]
                )
                lines.append(
                    f"  CONSTRAINT {_quote(constraint['name'])} "
                    f"{kind} ({columns})"
                )
            statements.append(
                f"CREATE TABLE {_quote(table)} (\n"
                + ",\n".join(lines)
                + "\n);"
            )
            statements.append("")
        for relation in FOREIGN_KEYS:
            if (
                relation.child_table not in tables
                or relation.parent_table not in tables
            ):
                continue
            constraint_name = (
                f"fk_{relation.child_table}_{relation.child_column}"
            )
            statements.extend(
                [
                    f"ALTER TABLE {_quote(relation.child_table)}",
                    f"  ADD CONSTRAINT {_quote(constraint_name)}",
                    f"  FOREIGN KEY ({_quote(relation.child_column)})",
                    (
                        f"  REFERENCES {_quote(relation.parent_table)} "
                        f"({_quote(relation.parent_column)})"
                    ),
                    f"  ON DELETE {relation.on_delete};",
                    "",
                ]
            )
        for table, index_columns in sorted(INDEXES.items()):
            if table not in tables:
                continue
            for columns in index_columns:
                index_name = f"idx_{table}_{'_'.join(columns)}"
                rendered = ", ".join(_quote(item) for item in columns)
                statements.append(
                    f"CREATE INDEX {_quote(index_name)} "
                    f"ON {_quote(table)} ({rendered});"
                )
        statements.extend(["", "COMMIT;", ""])
        return "\n".join(statements)


def _placement(table: str) -> str | None:
    if table in ANALYTICAL_TABLES:
        return "object_storage_parquet"
    if table.startswith(ARCHIVE_PREFIXES):
        return "object_storage_archive"
    return "postgresql"


def _strategy(table: str) -> str:
    placement = _placement(table)
    if placement == "object_storage_parquet":
        return (
            "Export partitioned Parquet, verify source checksum and row count, "
            "then register partitions in the analytical query layer."
        )
    if placement == "object_storage_archive":
        return (
            "Export immutable Parquet archive with manifest checksum; exclude "
            "from the transactional application schema."
        )
    return (
        "Bulk load in dependency order, verify row count and primary key, "
        "then apply foreign keys and indexes."
    )


def _postgres_type(source: str, column_name: str) -> str:
    normalized = source.upper()
    if normalized in {"VARCHAR", "TEXT"}:
        if column_name.endswith("_json"):
            return "JSONB"
        return "TEXT"
    if normalized == "BIGINT":
        return "BIGINT"
    if normalized in {"DOUBLE", "DOUBLE PRECISION"}:
        return "DOUBLE PRECISION"
    if normalized == "BOOLEAN":
        return "BOOLEAN"
    if normalized.startswith("TIMESTAMP"):
        return "TIMESTAMPTZ"
    if normalized.startswith("DECIMAL"):
        return normalized
    if normalized in {"INTEGER", "SMALLINT", "DATE"}:
        return normalized
    raise ValueError(f"Unsupported PostgreSQL type mapping: {source}")


def _postgres_default(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    cast_match = re.fullmatch(
        r"CAST\((.+) AS (?:VARCHAR|BIGINT|BOOLEAN)\)",
        normalized,
        flags=re.IGNORECASE,
    )
    if cast_match:
        return cast_match.group(1)
    if normalized.lower() in {"current_timestamp", "now()"}:
        return "CURRENT_TIMESTAMP"
    return normalized


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_literal(path: Path) -> str:
    return _string_literal(str(path.resolve()))


def _string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
