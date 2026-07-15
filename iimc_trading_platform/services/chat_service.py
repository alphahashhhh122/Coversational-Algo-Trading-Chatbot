from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from ..evaluator import ResponseEvaluator
from ..orchestration import (
    Orchestrator,
    ToolInvocation,
    grounded_multi_tool_response,
    grounded_tool_response,
)
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
    _EXACT_IDENTIFIER_FIELDS = {
        "dataset_id",
        "run_id",
        "intent_id",
        "approval_id",
        "experiment_id",
        "portfolio_id",
        "spec_id",
    }

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

    _DETERMINISTIC_RESPONSE_TOOLS = {
        "get_execution_readiness",
        "get_openalgo_monitor",
        "check_platform_readiness",
        "run_backtest",
        "run_custom_strategy_spec",
        "prepare_sandbox_order_intent",
        "prepare_live_order_intent",
        "list_sandbox_intents",
    }

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

        invocations = self._invocations_for(decision)
        if len(invocations) > 4:
            return self._reject_compound_request(
                active_session_id,
                "The assistant requested more than four tools for one "
                "message. Split the request into smaller read-only checks.",
                invocations,
            )
        if len(invocations) > 1:
            non_read_only = [
                invocation.tool_name
                for invocation in invocations
                if not active_registry.get(invocation.tool_name).is_read_only
            ]
            if non_read_only:
                return self._reject_compound_request(
                    active_session_id,
                    "Compound chat requests may run only read-only checks. "
                    "Ask for state-changing actions separately and explicitly.",
                    invocations,
                )

        for invocation in invocations:
            clarification = self._identifier_clarification(
                invocation,
                message,
                history,
            )
            if clarification:
                return self._clarify(
                    active_session_id,
                    active_registry,
                    clarification,
                )

        if len(invocations) > 1:
            return self._answer_compound_read_only(
                message,
                active_session_id,
                active_registry,
                invocations,
            )

        invocation = invocations[0]
        tool = active_registry.get(invocation.tool_name)
        validated = tool.validate(invocation.arguments)
        request_payload = validated.model_dump(mode="json")
        try:
            tool_call_id, result = self.tool_execution_service.execute(
                tool_name=invocation.tool_name,
                request=request_payload,
                handler=lambda: tool.handler(validated),
                session_id=active_session_id,
            )
            if invocation.tool_name in self._DETERMINISTIC_RESPONSE_TOOLS:
                generated_answer = grounded_tool_response(
                    invocation.tool_name,
                    result,
                )
            else:
                generated_answer = self.orchestrator.compose_response(
                    message,
                    decision,
                    result,
                )
            evaluation = self.evaluator.evaluate(
                answer=generated_answer,
                tool_name=invocation.tool_name,
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
                intent=invocation.tool_name,
                answer=answer,
                tool_calls=[
                    ToolEvidence(
                        tool_call_id=exc.tool_call_id,
                        tool_name=invocation.tool_name,
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
            intent=invocation.tool_name,
            answer=evaluation.answer,
            tool_calls=[
                ToolEvidence(
                    tool_call_id=tool_call_id,
                    tool_name=invocation.tool_name,
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

    @staticmethod
    def _invocations_for(decision) -> list[ToolInvocation]:
        if decision.tool_calls:
            unique: list[ToolInvocation] = []
            seen: set[tuple[str, str]] = set()
            for invocation in decision.tool_calls:
                arguments = invocation.arguments or {}
                if not isinstance(arguments, dict):
                    raise ValueError(
                        "Tool arguments must be a JSON object or null"
                    )
                key = (
                    invocation.tool_name,
                    json.dumps(arguments, sort_keys=True, default=str),
                )
                if key not in seen:
                    seen.add(key)
                    unique.append(
                        ToolInvocation(
                            tool_name=invocation.tool_name,
                            arguments=arguments,
                            call_id=invocation.call_id,
                        )
                    )
            return unique
        if decision.tool_name is None:
            return []
        return [
            ToolInvocation(
                tool_name=decision.tool_name,
                arguments=decision.arguments,
                call_id=decision.call_id,
            )
        ]

    def _identifier_clarification(
        self,
        invocation: ToolInvocation,
        message: str,
        history: list[dict[str, str]],
    ) -> str | None:
        context = "\n".join(
            [message, *(item.get("content", "") for item in history)]
        )
        for field_name in self._EXACT_IDENTIFIER_FIELDS:
            value = invocation.arguments.get(field_name)
            if not isinstance(value, str) or not value.strip():
                continue
            if _identifier_in_context(value, context):
                continue
            return (
                f"Please provide the exact {field_name}. I will not infer "
                "a stored record identifier."
            )
        return None

    def _clarify(
        self,
        session_id: str,
        registry: ToolRegistry,
        answer: str,
    ) -> ChatResult:
        evaluation = self.evaluator.evaluate(
            answer=answer,
            tool_name=None,
            tool_result=None,
            tool_call_id=None,
        )
        self._store_assistant(
            session_id,
            evaluation.answer,
            {
                "intent": "clarification",
                "orchestration_mode": self.orchestrator.mode,
                "evaluation": evaluation.warnings,
            },
        )
        return ChatResult(
            session_id=session_id,
            intent="clarification",
            answer=evaluation.answer,
            tool_calls=[],
            data={"available_tools": registry.list_tools()},
            orchestration_mode=self.orchestrator.mode,
            evaluation={
                "passed": evaluation.passed,
                "warnings": evaluation.warnings,
                "evidence_ids": evaluation.evidence_ids,
            },
        )

    def _answer_compound_read_only(
        self,
        message: str,
        session_id: str,
        registry: ToolRegistry,
        invocations: list[ToolInvocation],
    ) -> ChatResult:
        results: list[tuple[str, dict[str, Any]]] = []
        evidence: list[ToolEvidence] = []
        evaluations = []
        failures: list[dict[str, str]] = []
        for invocation in invocations:
            tool = registry.get(invocation.tool_name)
            validated = tool.validate(invocation.arguments)
            try:
                tool_call_id, result = self.tool_execution_service.execute(
                    tool_name=invocation.tool_name,
                    request=validated.model_dump(mode="json"),
                    handler=lambda tool=tool, validated=validated: tool.handler(validated),
                    session_id=session_id,
                )
            except ToolExecutionError as exc:
                evidence.append(
                    ToolEvidence(
                        tool_call_id=exc.tool_call_id,
                        tool_name=invocation.tool_name,
                        status="failed",
                    )
                )
                failures.append(
                    {
                        "tool_name": invocation.tool_name,
                        "error_type": type(exc.cause).__name__,
                    }
                )
                continue
            results.append((invocation.tool_name, result))
            evidence.append(
                ToolEvidence(
                    tool_call_id=tool_call_id,
                    tool_name=invocation.tool_name,
                    status="succeeded",
                )
            )
            evaluations.append(
                self.evaluator.evaluate(
                    answer=grounded_tool_response(invocation.tool_name, result),
                    tool_name=invocation.tool_name,
                    tool_result=result,
                    tool_call_id=tool_call_id,
                )
            )

        answer = grounded_multi_tool_response(results) if results else (
            "The requested read-only checks failed safely. Their audited "
            "tool records are available for inspection."
        )
        if failures:
            answer += "\n\nSome checks failed safely: " + ", ".join(
                item["tool_name"] for item in failures
            ) + "."
        evidence_ids = [
            evidence_id
            for evaluation in evaluations
            for evidence_id in evaluation.evidence_ids
        ]
        self._store_assistant(
            session_id,
            answer,
            {
                "intent": "multi_tool",
                "tool_call_ids": [item.tool_call_id for item in evidence],
                "tool_names": [item.tool_name for item in evidence],
                "orchestration_mode": self.orchestrator.mode,
                "failures": failures,
            },
        )
        return ChatResult(
            session_id=session_id,
            intent="multi_tool",
            answer=answer,
            tool_calls=evidence,
            data={
                "tool_results": {
                    tool_name: result for tool_name, result in results
                },
                "failures": failures,
            },
            orchestration_mode=self.orchestrator.mode,
            evaluation={
                "passed": not failures and all(
                    item.passed for item in evaluations
                ),
                "warnings": [
                    warning
                    for item in evaluations
                    for warning in item.warnings
                ],
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
            },
        )

    def _reject_compound_request(
        self,
        session_id: str,
        answer: str,
        invocations: list[ToolInvocation],
    ) -> ChatResult:
        requested_tools = [item.tool_name for item in invocations]
        self._store_assistant(
            session_id,
            answer,
            {
                "intent": "compound_request_rejected",
                "requested_tools": requested_tools,
                "orchestration_mode": self.orchestrator.mode,
            },
        )
        return ChatResult(
            session_id=session_id,
            intent="compound_request_rejected",
            answer=answer,
            tool_calls=[],
            data={"requested_tools": requested_tools},
            orchestration_mode=self.orchestrator.mode,
            evaluation={
                "passed": True,
                "warnings": [],
                "evidence_ids": [],
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


def _identifier_in_context(value: str, context: str) -> bool:
    escaped = re.escape(value.strip())
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_.-]){escaped}(?![A-Za-z0-9_.-])",
            context,
            flags=re.IGNORECASE,
        )
    )
