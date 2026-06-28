from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from opentelemetry.trace import Status, StatusCode

from ..domain import ToolCall, ToolCallStatus
from ..repositories import ToolCallRepository
from ..telemetry import current_trace_context, start_span
from .audit_service import AuditService


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ToolExecutionError(RuntimeError):
    def __init__(self, tool_call_id: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.tool_call_id = tool_call_id
        self.cause = cause


class ToolExecutionService:
    def __init__(
        self,
        repository: ToolCallRepository,
        audit_service: AuditService,
    ):
        self.repository = repository
        self.audit_service = audit_service

    def execute(
        self,
        *,
        tool_name: str,
        request: dict[str, Any],
        handler: Callable[[], Any],
        session_id: str | None = None,
    ) -> tuple[str, Any]:
        with start_span(
            "tool.execute",
            {
                "tool.name": tool_name,
                "session.id": session_id,
            },
        ) as span:
            trace_context = current_trace_context(span)
            tool_call = ToolCall(
                tool_call_id=f"tool_{uuid.uuid4().hex}",
                session_id=session_id,
                tool_name=tool_name,
                request_json=json.dumps(
                    request,
                    sort_keys=True,
                    default=str,
                ),
                response_json=None,
                status=ToolCallStatus.RUNNING,
                created_at=utc_now(),
                trace_id=trace_context.trace_id,
                span_id=trace_context.span_id,
            )
            span.set_attribute("tool.call_id", tool_call.tool_call_id)
            self.repository.add(tool_call)
            audit_context = {
                "trace_id": trace_context.trace_id,
                "span_id": trace_context.span_id,
            }
            self.audit_service.record(
                entity_type="tool_call",
                entity_id=tool_call.tool_call_id,
                action="started",
                actor="system",
                payload={
                    "tool_name": tool_name,
                    "session_id": session_id,
                    **audit_context,
                },
            )

            try:
                result = handler()
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                failed = replace(
                    tool_call,
                    status=ToolCallStatus.FAILED,
                    error_message=str(exc),
                    finished_at=utc_now(),
                )
                self.repository.update(failed)
                self.audit_service.record(
                    entity_type="tool_call",
                    entity_id=tool_call.tool_call_id,
                    action="failed",
                    actor="system",
                    payload={
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        **audit_context,
                    },
                )
                raise ToolExecutionError(
                    tool_call.tool_call_id,
                    exc,
                ) from exc

            completed = replace(
                tool_call,
                response_json=json.dumps(
                    result,
                    sort_keys=True,
                    default=str,
                ),
                status=ToolCallStatus.SUCCEEDED,
                finished_at=utc_now(),
            )
            self.repository.update(completed)
            self.audit_service.record(
                entity_type="tool_call",
                entity_id=tool_call.tool_call_id,
                action="succeeded",
                actor="system",
                payload={"tool_name": tool_name, **audit_context},
            )
            span.set_status(Status(StatusCode.OK))
            return tool_call.tool_call_id, result
