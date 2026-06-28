from __future__ import annotations

import json
from pathlib import Path

from ..db import connect
from ..domain import (
    AuditEvent,
    DataDomain,
    DataQualityStatus,
    Dataset,
    DatasetQuality,
    ToolCall,
    ToolCallStatus,
)


class DuckDBDatasetRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def list(self) -> list[Dataset]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT
                    catalog.dataset_id,
                    catalog.data_domain,
                    catalog.data_type,
                    catalog.symbol,
                    catalog.exchange,
                    catalog.interval,
                    catalog.start_ts,
                    catalog.end_ts,
                    catalog.row_count,
                    catalog.storage_table,
                    catalog.source_id,
                    catalog.quality_status,
                    COALESCE(quality.total_rows, catalog.row_count),
                    COALESCE(quality.valid_rows, catalog.row_count),
                    COALESCE(quality.duplicate_rows, 0),
                    COALESCE(quality.invalid_rows, 0)
                FROM data_catalog AS catalog
                LEFT JOIN (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY dataset_id
                               ORDER BY created_at DESC
                           ) AS row_number
                    FROM data_quality_reports
                ) AS quality
                    ON quality.dataset_id = catalog.dataset_id
                   AND quality.row_number = 1
                ORDER BY catalog.updated_at DESC
                """
            ).fetchall()
        finally:
            con.close()
        return [self._row_to_dataset(row) for row in rows]

    def get(self, dataset_id: str) -> Dataset | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT
                    catalog.dataset_id,
                    catalog.data_domain,
                    catalog.data_type,
                    catalog.symbol,
                    catalog.exchange,
                    catalog.interval,
                    catalog.start_ts,
                    catalog.end_ts,
                    catalog.row_count,
                    catalog.storage_table,
                    catalog.source_id,
                    catalog.quality_status,
                    COALESCE(quality.total_rows, catalog.row_count),
                    COALESCE(quality.valid_rows, catalog.row_count),
                    COALESCE(quality.duplicate_rows, 0),
                    COALESCE(quality.invalid_rows, 0)
                FROM data_catalog AS catalog
                LEFT JOIN (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY dataset_id
                               ORDER BY created_at DESC
                           ) AS row_number
                    FROM data_quality_reports
                ) AS quality
                    ON quality.dataset_id = catalog.dataset_id
                   AND quality.row_number = 1
                WHERE catalog.dataset_id = ?
                """,
                [dataset_id],
            ).fetchone()
        finally:
            con.close()
        return self._row_to_dataset(row) if row else None

    @staticmethod
    def _row_to_dataset(row) -> Dataset:
        quality_status = DataQualityStatus(row[11])
        quality = DatasetQuality(
            status=quality_status,
            total_rows=int(row[12]),
            valid_rows=int(row[13]),
            duplicate_rows=int(row[14]),
            invalid_rows=int(row[15]),
            warnings=(
                []
                if quality_status == DataQualityStatus.CLEAN
                else [quality_status.value]
            ),
        )
        return Dataset(
            dataset_id=row[0],
            data_domain=DataDomain(row[1]),
            data_type=row[2],
            symbol=row[3],
            exchange=row[4],
            interval=row[5],
            start_ts=row[6],
            end_ts=row[7],
            row_count=int(row[8]),
            storage_table=row[9],
            source_id=row[10],
            quality=quality,
        )


class DuckDBAuditRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def add(self, event: AuditEvent) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event.audit_id,
                    event.entity_type,
                    event.entity_id,
                    event.action,
                    event.actor,
                    json.dumps(event.payload, sort_keys=True, default=str),
                    event.created_at,
                ],
            )
        finally:
            con.close()

    def list_for_entity(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT audit_id, entity_type, entity_id, action, actor,
                       payload_json, created_at
                FROM audit_events
                WHERE entity_type = ? AND entity_id = ?
                ORDER BY created_at, audit_id
                """,
                [entity_type, entity_id],
            ).fetchall()
        finally:
            con.close()
        return [
            AuditEvent(
                audit_id=row[0],
                entity_type=row[1],
                entity_id=row[2],
                action=row[3],
                actor=row[4],
                payload=json.loads(row[5]),
                created_at=row[6],
            )
            for row in rows
        ]


class DuckDBToolCallRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def add(self, tool_call: ToolCall) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO tool_calls (
                    tool_call_id,
                    session_id,
                    tool_name,
                    request_json,
                    response_json,
                    status,
                    error_message,
                    created_at,
                    finished_at,
                    trace_id,
                    span_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._insert_values(tool_call),
            )
        finally:
            con.close()

    def update(self, tool_call: ToolCall) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE tool_calls
                SET session_id = ?, tool_name = ?, request_json = ?,
                    response_json = ?, status = ?, error_message = ?,
                    created_at = ?, finished_at = ?, trace_id = ?,
                    span_id = ?
                WHERE tool_call_id = ?
                """,
                self._update_values(tool_call),
            )
        finally:
            con.close()

    def get(self, tool_call_id: str) -> ToolCall | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT tool_call_id, session_id, tool_name, request_json,
                       response_json, status, error_message, created_at,
                       finished_at, trace_id, span_id
                FROM tool_calls
                WHERE tool_call_id = ?
                """,
                [tool_call_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return ToolCall(
            tool_call_id=row[0],
            session_id=row[1],
            tool_name=row[2],
            request_json=row[3],
            response_json=row[4],
            status=ToolCallStatus(row[5]),
            error_message=row[6],
            created_at=row[7],
            finished_at=row[8],
            trace_id=row[9],
            span_id=row[10],
        )

    @staticmethod
    def _insert_values(tool_call: ToolCall) -> list[object]:
        return [
            tool_call.tool_call_id,
            tool_call.session_id,
            tool_call.tool_name,
            tool_call.request_json,
            tool_call.response_json,
            tool_call.status.value,
            tool_call.error_message,
            tool_call.created_at,
            tool_call.finished_at,
            tool_call.trace_id,
            tool_call.span_id,
        ]

    @staticmethod
    def _update_values(tool_call: ToolCall) -> list[object]:
        return [
            tool_call.session_id,
            tool_call.tool_name,
            tool_call.request_json,
            tool_call.response_json,
            tool_call.status.value,
            tool_call.error_message,
            tool_call.created_at,
            tool_call.finished_at,
            tool_call.trace_id,
            tool_call.span_id,
            tool_call.tool_call_id,
        ]
