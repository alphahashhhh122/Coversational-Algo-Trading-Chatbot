from __future__ import annotations

from typing import Protocol

from ..domain import AuditEvent, Dataset, ToolCall


class DatasetRepository(Protocol):
    def list(self) -> list[Dataset]:
        """Return cataloged datasets in display order."""

    def get(self, dataset_id: str) -> Dataset | None:
        """Return a dataset by catalog identifier."""


class AuditRepository(Protocol):
    def add(self, event: AuditEvent) -> None:
        """Persist an immutable audit event."""

    def list_for_entity(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        """Return audit events for an entity in chronological order."""


class ToolCallRepository(Protocol):
    def add(self, tool_call: ToolCall) -> None:
        """Persist a newly started tool call."""

    def update(self, tool_call: ToolCall) -> None:
        """Persist the latest state of an existing tool call."""

    def get(self, tool_call_id: str) -> ToolCall | None:
        """Return a tool call by identifier."""
