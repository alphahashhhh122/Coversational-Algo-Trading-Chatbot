from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..domain import AuditEvent
from ..repositories import AuditRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditService:
    def __init__(self, repository: AuditRepository):
        self.repository = repository

    def record(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            audit_id=f"audit_{uuid.uuid4().hex}",
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            payload=payload or {},
            created_at=utc_now(),
        )
        self.repository.add(event)
        return event

    def history(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        return self.repository.list_for_entity(entity_type, entity_id)
