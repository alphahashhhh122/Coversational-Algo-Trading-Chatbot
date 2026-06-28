from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ..evaluator import ResponseEvaluator
from ..orchestration import Orchestrator
from ..tools.registry import ToolRegistry
from .conversation_service import ConversationService
from .tool_execution_service import ToolExecutionError, ToolExecutionService


@dataclass(frozen=True)
class ToolEvidence:
    tool_call_id: str
    tool_name: str
    status: str


@dataclass(frozen=True)
class ChatResult:
    session_id: str
    intent: str
    answer: str
    tool_calls: list[ToolEvidence]
    data: dict[str, Any]
    orchestration_mode: str
    evaluation: dict[str, Any]


class ChatService:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_execution_service: ToolExecutionService,
        orchestrator: Orchestrator,
        conversation_service: ConversationService,
        evaluator: ResponseEvaluator | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.tool_execution_service = tool_execution_service
        self.orchestrator = orchestrator
        self.conversation_service = conversation_service
        self.evaluator = evaluator or ResponseEvaluator()

    def answer(
        self,
        message: str,
        session_id: str | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> ChatResult:
        active_registry = (
            self.tool_registry.subset(allowed_tool_names)
            if allowed_tool_names is not None
            else self.tool_registry
        )
        active_session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
        self.conversation_service.ensure_session(active_session_id)
        history = self.conversation_service.history(active_session_id)
        self.conversation_service.append(
            session_id=active_session_id,
            role="user",
            content=message,
        )

        decision = self.orchestrator.select_tool(
            message,
            [
                {"role": item["role"], "content": item["content"]}
                for item in history
            ],
            active_registry,
        )
        if decision.tool_name is None:
            answer = decision.direct_response or "No supported action was selected."
            evaluation = self.evaluator.evaluate(
                answer=answer,
                tool_name=None,
                tool_result=None,
                tool_call_id=None,
            )
            self._store_assistant(
                active_session_id,
                evaluation.answer,
                {
                    "intent": "unsupported",
                    "orchestration_mode": self.orchestrator.mode,
                    "evaluation": evaluation.warnings,
                },
            )
            return ChatResult(
                session_id=active_session_id,
                intent="unsupported",
                answer=evaluation.answer,
                tool_calls=[],
                data={"available_tools": active_registry.list_tools()},
                orchestration_mode=self.orchestrator.mode,
                evaluation={
                    "passed": evaluation.passed,
                    "warnings": evaluation.warnings,
                    "evidence_ids": evaluation.evidence_ids,
                },
            )

        tool = active_registry.get(decision.tool_name)
        validated = tool.validate(decision.arguments)
        request_payload = validated.model_dump(mode="json")
        try:
            tool_call_id, result = self.tool_execution_service.execute(
                tool_name=decision.tool_name,
                request=request_payload,
                handler=lambda: tool.handler(validated),
                session_id=active_session_id,
            )
            generated_answer = self.orchestrator.compose_response(
                message,
                decision,
                result,
            )
            evaluation = self.evaluator.evaluate(
                answer=generated_answer,
                tool_name=decision.tool_name,
                tool_result=result,
                tool_call_id=tool_call_id,
            )
        except ToolExecutionError as exc:
            answer = (
                f"The requested operation failed safely: "
                f"{type(exc.cause).__name__}. "
                "The failed tool lifecycle was recorded for inspection."
            )
            self._store_assistant(
                active_session_id,
                answer,
                {
                    "intent": decision.tool_name,
                    "tool_call_id": exc.tool_call_id,
                    "error_type": type(exc.cause).__name__,
                    "orchestration_mode": self.orchestrator.mode,
                },
            )
            return ChatResult(
                session_id=active_session_id,
                intent=decision.tool_name,
                answer=answer,
                tool_calls=[
                    ToolEvidence(
                        tool_call_id=exc.tool_call_id,
                        tool_name=decision.tool_name,
                        status="failed",
                    )
                ],
                data={"error_type": type(exc.cause).__name__},
                orchestration_mode=self.orchestrator.mode,
                evaluation={
                    "passed": False,
                    "warnings": ["tool_execution_failed"],
                    "evidence_ids": [exc.tool_call_id],
                },
            )

        self._store_assistant(
            active_session_id,
            evaluation.answer,
            {
                "intent": decision.tool_name,
                "tool_call_id": tool_call_id,
                "run_id": result.get("run_id"),
                "dataset_id": result.get("dataset_id"),
                "orchestration_mode": self.orchestrator.mode,
                "evaluation": evaluation.warnings,
            },
        )
        return ChatResult(
            session_id=active_session_id,
            intent=decision.tool_name,
            answer=evaluation.answer,
            tool_calls=[
                ToolEvidence(
                    tool_call_id=tool_call_id,
                    tool_name=decision.tool_name,
                    status="succeeded",
                )
            ],
            data=result,
            orchestration_mode=self.orchestrator.mode,
            evaluation={
                "passed": evaluation.passed,
                "warnings": evaluation.warnings,
                "evidence_ids": evaluation.evidence_ids,
            },
        )

    def _store_assistant(
        self,
        session_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        self.conversation_service.append(
            session_id=session_id,
            role="assistant",
            content=content,
            metadata=metadata,
        )
