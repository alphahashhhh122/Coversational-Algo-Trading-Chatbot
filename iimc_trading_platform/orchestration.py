from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

from .tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolInvocation:
    tool_name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class OrchestrationDecision:
    tool_name: str | None
    arguments: dict[str, Any]
    direct_response: str | None = None
    call_id: str | None = None
    provider_items: list[Any] = field(default_factory=list)
    tool_calls: list[ToolInvocation] = field(default_factory=list)


class Orchestrator(Protocol):
    mode: str

    def select_tool(
        self,
        message: str,
        history: list[dict[str, str]],
        registry: ToolRegistry,
    ) -> OrchestrationDecision: ...

    def compose_response(
        self,
        message: str,
        decision: OrchestrationDecision,
        tool_result: dict[str, Any],
    ) -> str: ...


def grounded_tool_response(tool_name: str, result: dict[str, Any]) -> str:
    return _grounded_fallback_response(tool_name, result)


class OpenAIResponsesOrchestrator:
    mode = "openai_responses"

    def __init__(self, api_key: str, model: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The openai package is required for LLM orchestration"
            ) from exc
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def select_tool(
        self,
        message: str,
        history: list[dict[str, str]],
        registry: ToolRegistry,
    ) -> OrchestrationDecision:
        input_items: list[dict[str, str]] = [
            {"role": item["role"], "content": item["content"]}
            for item in history[-12:]
        ]
        input_items.append({"role": "user", "content": message})
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "You are the orchestration layer for an audited trading "
                "research platform. Select only registered tools. Never invent "
                "dataset IDs, run IDs, prices, P&L, risk decisions, or broker "
                "state. Use research mode unless the user explicitly requests "
                "another mode. Do not claim live execution. For compound "
                "questions, select at most four read-only tools. Never combine "
                "a state-changing tool with any other tool."
            ),
            input=input_items,
            tools=registry.openai_tools(),
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
        )
        tool_calls = [
            ToolInvocation(
                tool_name=item.name,
                arguments=_tool_arguments(item.arguments),
                call_id=item.call_id,
            )
            for item in response.output
            if item.type == "function_call"
        ]
        if tool_calls:
            first = tool_calls[0]
            return OrchestrationDecision(
                tool_name=first.tool_name,
                arguments=first.arguments,
                call_id=first.call_id,
                provider_items=list(response.output),
                tool_calls=tool_calls,
            )
        return OrchestrationDecision(
            tool_name=None,
            arguments={},
            direct_response=response.output_text or None,
            provider_items=list(response.output),
        )

    def compose_response(
        self,
        message: str,
        decision: OrchestrationDecision,
        tool_result: dict[str, Any],
    ) -> str:
        if decision.call_id is None:
            return decision.direct_response or "No tool was selected."
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "Explain the tool result accurately and concisely. Cite every "
                "provided evidence ID. Do not introduce financial numbers that "
                "are absent from the tool output. State that backtests are "
                "historical simulations when relevant."
            ),
            input=[
                {"role": "user", "content": message},
                *decision.provider_items,
                {
                    "type": "function_call_output",
                    "call_id": decision.call_id,
                    "output": json.dumps(tool_result, default=str),
                },
            ],
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
        )
        return response.output_text


class GroqToolOrchestrator:
    mode = "groq_chat_completions"

    def __init__(self, api_key: str, model: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The openai package is required for Groq orchestration"
            ) from exc
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        self.model = model

    def select_tool(
        self,
        message: str,
        history: list[dict[str, str]],
        registry: ToolRegistry,
    ) -> OrchestrationDecision:
        messages = _chat_messages(message, history)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                *messages,
            ],
            tools=_chat_completion_tools(registry),
            tool_choice="auto",
            temperature=0,
        )
        choice = response.choices[0].message
        tool_calls = getattr(choice, "tool_calls", None) or []
        if tool_calls:
            invocations = [
                ToolInvocation(
                    tool_name=item.function.name,
                    arguments=_tool_arguments(
                        getattr(item.function, "arguments", None)
                    ),
                    call_id=item.id,
                )
                for item in tool_calls
            ]
            first = invocations[0]
            return OrchestrationDecision(
                tool_name=first.tool_name,
                arguments=first.arguments,
                call_id=first.call_id,
                provider_items=[
                    {
                        "role": "assistant",
                        "content": choice.content or "",
                        "tool_calls": [
                            {
                                "id": item.id,
                                "type": item.type,
                                "function": {
                                    "name": item.function.name,
                                    "arguments": item.function.arguments,
                                },
                            }
                            for item in tool_calls
                        ],
                    }
                ],
                tool_calls=invocations,
            )
        return OrchestrationDecision(
            tool_name=None,
            arguments={},
            direct_response=choice.content or None,
        )

    def compose_response(
        self,
        message: str,
        decision: OrchestrationDecision,
        tool_result: dict[str, Any],
    ) -> str:
        if decision.call_id is None:
            return decision.direct_response or "No tool was selected."
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Explain the tool result accurately and concisely. "
                        "Do not invent prices, P&L, broker state, or missing "
                        "evidence. State historical simulation limits when "
                        "the result is a backtest."
                    ),
                },
                {"role": "user", "content": message},
                *decision.provider_items,
                {
                    "role": "tool",
                    "tool_call_id": decision.call_id,
                    "content": json.dumps(tool_result, default=str),
                },
            ],
            temperature=0,
        )
        return response.choices[0].message.content or ""


class OfflineOrchestrator:
    """
    Explicit degraded mode for local tests without an API key.

    This is not presented as the production AI path. It keeps deterministic
    tools testable when external model access is unavailable.
    """

    mode = "offline_fallback"

    def select_tool(
        self,
        message: str,
        history: list[dict[str, str]],
        registry: ToolRegistry,
    ) -> OrchestrationDecision:
        text = message.lower()
        run_id = _extract_identifier(message, "run_")
        run_ids = _extract_identifiers(message, "run_")
        dataset_id = _dataset_from_text(message)
        intent_id = _extract_identifier(message, "intent_")
        experiment_id = _extract_identifier(message, "robust_")
        portfolio_id = _extract_identifier(message, "portfolio_")
        tool_names = {
            tool["name"]
            for tool in registry.list_tools()
        }

        asks_for_catalog = any(
            phrase in text
            for phrase in ("available", "list", "what", "show")
        )
        if (
            asks_for_catalog
            and "dataset" in text
            and "strateg" in text
            and {"list_datasets", "list_strategies"}.issubset(tool_names)
        ):
            invocations = [
                ToolInvocation("list_datasets", {}),
                ToolInvocation("list_strategies", {}),
            ]
            return OrchestrationDecision(
                tool_name=invocations[0].tool_name,
                arguments=invocations[0].arguments,
                tool_calls=invocations,
            )

        if (
            "get_platform_summary" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "platform summary",
                    "platform overview",
                    "platform status",
                    "platform capability",
                    "platform capabilities",
                    "dashboard summary",
                    "command center",
                    "what can the platform do",
                )
            )
        ):
            return OrchestrationDecision("get_platform_summary", {})
        if (
            "get_openalgo_monitor" in tool_names
            and "openalgo" in text
            and any(word in text for word in ("monitor", "status", "ready", "check"))
        ):
            return OrchestrationDecision("get_openalgo_monitor", {})
        if (
            "search_instruments" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "search instrument",
                    "search symbol",
                    "find contract",
                    "find instrument",
                    "contract lookup",
                    "symbol lookup",
                )
            )
        ):
            return OrchestrationDecision(
                "search_instruments",
                {
                    "query": _symbol_from_text(message) or message[:200],
                    "exchange": _exchange_from_text(message, default="NFO"),
                },
            )
        if (
            "validate_instrument_symbol" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "validate symbol",
                    "validate instrument",
                    "symbol details",
                    "instrument details",
                )
            )
        ):
            return OrchestrationDecision(
                "validate_instrument_symbol",
                {
                    "symbol": _symbol_from_text(message) or "NIFTY",
                    "exchange": _exchange_from_text(message, default="NFO"),
                },
            )
        if (
            "get_market_news" in tool_names
            and any(word in text for word in ("news", "headline", "research update"))
        ):
            symbol = _symbol_from_text(message)
            return OrchestrationDecision(
                "get_market_news",
                {
                    "query": message,
                    "symbol": symbol,
                },
            )
        if (
            "list_strategy_personas" in tool_names
            and not ("custom" in text and "strateg" in text)
            and any(
                word in text
                for word in (
                    "persona",
                    "personas",
                    "profile",
                    "style",
                    "buffett",
                    "warren",
                    "conservative",
                    "momentum",
                    "risk-off",
                    "risk off",
                )
            )
        ):
            persona_id = _persona_from_text(text)
            if persona_id and "get_strategy_persona" in tool_names:
                return OrchestrationDecision(
                    "get_strategy_persona",
                    {"persona_id": persona_id},
                )
            return OrchestrationDecision("list_strategy_personas", {})
        if (
            "get_research_context" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "research context",
                    "market research",
                    "research this",
                    "research symbol",
                    "analyse",
                    "analyze",
                )
            )
            and not any(word in text for word in ("news", "headline"))
        ):
            return OrchestrationDecision(
                "get_research_context",
                _readiness_arguments(message),
            )
        if (
            "create_research_brief" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "research brief",
                    "create brief",
                    "generate brief",
                    "market brief",
                    "brief for",
                )
            )
        ):
            return OrchestrationDecision(
                "create_research_brief",
                _readiness_arguments(message),
            )
        if (
            "get_execution_readiness" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "execution readiness",
                    "trading readiness",
                    "can i paper trade",
                    "can we paper trade",
                    "can i live trade",
                    "can we live trade",
                    "what is blocked",
                    "what is ready",
                    "workflow readiness",
                )
            )
        ):
            return OrchestrationDecision(
                "get_execution_readiness",
                _readiness_arguments(message),
            )
        if (
            "check_platform_readiness" in tool_names
            and any(
                word in text
                for word in (
                    "readiness",
                    "ready",
                    "provider",
                    "quote",
                    "historical data",
                    "can we trade",
                )
            )
            and not ("openalgo" in text and "monitor" in text)
        ):
            return OrchestrationDecision(
                "check_platform_readiness",
                _readiness_arguments(message),
            )
        if (
            "get_openalgo_snapshot" in tool_names
            and "openalgo" in text
        ):
            for snapshot_type in (
                "funds",
                "positionbook",
                "orderbook",
                "tradebook",
            ):
                if snapshot_type in text:
                    return OrchestrationDecision(
                        "get_openalgo_snapshot",
                        {"snapshot_type": snapshot_type},
                    )
        if (
            "list_pending_approvals" in tool_names
            and "pending" in text
            and "approval" in text
        ):
            return OrchestrationDecision("list_pending_approvals", {})
        if (
            "prepare_sandbox_order_intent" in tool_names
            and any(word in text for word in ("prepare", "create", "draft"))
            and any(
                phrase in text
                for phrase in (
                    "sandbox order",
                    "paper order",
                    "paper trading order",
                    "openalgo intent",
                    "sandbox intent",
                )
            )
        ):
            decision_id = _extract_identifier(message, "risk_")
            if not decision_id:
                return OrchestrationDecision(
                    tool_name=None,
                    arguments={},
                    direct_response=(
                        "Please provide the approved risk decision_id "
                        "(for example risk_...) before I prepare an "
                        "OpenAlgo sandbox intent. I cannot approve or submit "
                        "orders from chat."
                    ),
                )
            return OrchestrationDecision(
                "prepare_sandbox_order_intent",
                _sandbox_intent_arguments(message, decision_id),
            )
        if (
            "list_sandbox_intents" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "sandbox intent",
                    "sandbox intents",
                    "paper order",
                    "paper orders",
                    "paper trading",
                    "openalgo intent",
                    "openalgo intents",
                )
            )
        ):
            return OrchestrationDecision("list_sandbox_intents", {})
        if (
            "reconcile_sandbox_intent" in tool_names
            and intent_id
            and any(word in text for word in ("reconcile", "status", "refresh"))
        ):
            return OrchestrationDecision(
                "reconcile_sandbox_intent",
                {"intent_id": intent_id, "actor": "chat_user"},
            )
        if "fresh" in text or "stale" in text:
            purpose = (
                "current_market"
                if any(word in text for word in ("current", "live", "latest"))
                else "historical_research"
            )
            if dataset_id:
                return OrchestrationDecision(
                    "assess_dataset_freshness",
                    {"dataset_id": dataset_id, "purpose": purpose},
                )
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Please provide the dataset_id to assess freshness."
                ),
            )
        if any(
            phrase in text
            for phrase in (
                "architecture",
                "documentation",
                "risk policy",
                "how does the platform",
                "knowledge base",
            )
        ):
            return OrchestrationDecision(
                "search_knowledge",
                {"query": message, "limit": 5},
            )
        if "risk" in text and run_id:
            return OrchestrationDecision(
                "get_risk_decisions",
                {"run_id": run_id},
            )
        if (
            "risk" in text
            and any(word in text for word in ("decision", "check", "result"))
            and not run_id
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Please provide the run_id for the risk evidence."
                ),
            )
        if "compare_runs" in tool_names and len(run_ids) >= 2 and "compar" in text:
            return OrchestrationDecision(
                "compare_runs",
                {"run_ids": run_ids[:10]},
            )
        if (
            "create_run_report" in tool_names
            and run_id
            and any(word in text for word in ("report", "summary artifact"))
        ):
            return OrchestrationDecision(
                "create_run_report",
                {"run_id": run_id},
            )
        if (
            "get_run_timeline" in tool_names
            and run_id
            and any(
                phrase in text
                for phrase in (
                    "full workflow",
                    "complete workflow",
                    "end to end",
                    "signal to fill",
                    "timeline",
                )
            )
        ):
            return OrchestrationDecision(
                "get_run_timeline",
                {"run_id": run_id},
            )
        if (
            "get_robustness_experiment" in tool_names
            and experiment_id
            and any(
                word in text
                for word in ("robust", "experiment", "out-of-sample", "oos")
            )
        ):
            return OrchestrationDecision(
                "get_robustness_experiment",
                {"experiment_id": experiment_id},
            )
        if (
            "list_robustness_experiments" in tool_names
            and "robust" in text
            and "list" in text
        ):
            return OrchestrationDecision(
                "list_robustness_experiments",
                {},
            )
        if (
            "get_portfolio_snapshot" in tool_names
            and portfolio_id
            and any(
                word in text
                for word in (
                    "portfolio",
                    "position",
                    "exposure",
                    "cash",
                    "kill switch",
                )
            )
        ):
            return OrchestrationDecision(
                "get_portfolio_snapshot",
                {"portfolio_id": portfolio_id},
            )
        if (
            "portfolio" in text
            and any(word in text for word in ("exposure", "cash", "position"))
            and not portfolio_id
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Please provide the portfolio_id to inspect its state."
                ),
            )
        if (
            "list_portfolios" in tool_names
            and "portfolio" in text
            and any(word in text for word in ("list", "available", "show"))
        ):
            return OrchestrationDecision("list_portfolios", {})
        if (
            "prepare_live_order_intent" in tool_names
            and any(word in text for word in ("prepare", "create", "draft"))
            and "live" in text
            and "order" in text
        ):
            decision_id = _extract_identifier(message, "risk_")
            if not decision_id:
                return OrchestrationDecision(
                    tool_name=None,
                    arguments={},
                    direct_response=(
                        "Please provide the approved live risk decision_id "
                        "(for example risk_...) before I prepare a live "
                        "OpenAlgo order intent. Live submission still requires "
                        "separate human approval."
                    ),
                )
            return OrchestrationDecision(
                "prepare_live_order_intent",
                _sandbox_intent_arguments(message, decision_id),
            )
        if (
            "live" in text
            and "order" in text
            and any(word in text for word in ("place", "submit", "execute"))
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "I cannot directly place live orders from chat. I can "
                    "prepare a live order intent from an approved live risk "
                    "decision; submission requires explicit human approval."
                ),
            )
        if any(word in text for word in ("order", "fill")) and run_id:
            return OrchestrationDecision(
                "get_order_timeline",
                {"run_id": run_id},
            )
        if (
            any(word in text for word in ("order", "fill"))
            and any(
                word in text
                for word in ("timeline", "history", "evidence", "show")
            )
            and not run_id
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Please provide the run_id for the order timeline."
                ),
            )
        if any(word in text for word in ("performance", "drawdown", "equity")) and run_id:
            return OrchestrationDecision(
                "get_performance",
                {"run_id": run_id},
            )
        if (
            any(
                word in text
                for word in ("performance", "drawdown", "equity")
            )
            and not run_id
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Please provide the run_id for performance evidence."
                ),
            )
        if (
            "list_custom_strategy_specs" in tool_names
            and "custom" in text
            and "strateg" in text
            and any(word in text for word in ("list", "show", "drafts"))
        ):
            return OrchestrationDecision("list_custom_strategy_specs", {})
        custom_spec_id = _extract_identifier(message, "custom_")
        if (
            "run_custom_strategy_spec" in tool_names
            and custom_spec_id
            and any(word in text for word in ("backtest", "simulate", "run"))
            and any(word in text for word in ("custom", "spec", "strategy"))
        ):
            if not dataset_id:
                return OrchestrationDecision(
                    tool_name=None,
                    arguments={},
                    direct_response=(
                        "Please provide the dataset_id to backtest the custom "
                        "strategy spec, for example: backtest custom_... on "
                        "nifty_options."
                    ),
                )
            arguments = {
                "spec_id": custom_spec_id,
                "dataset_id": dataset_id,
            }
            if any(
                phrase in text
                for phrase in ("semi-auto", "semi auto", "paper", "approval")
            ):
                arguments["execution_mode"] = "semi_auto"
            return OrchestrationDecision(
                "run_custom_strategy_spec",
                arguments,
            )
        if (
            "get_custom_strategy_capabilities" in tool_names
            and "strateg" in text
            and any(
                word in text
                for word in (
                    "support",
                    "capability",
                    "indicator",
                    "rule",
                    "available",
                    "can i",
                )
            )
            and not any(
                word in text for word in ("create", "draft", "backtest")
            )
        ):
            return OrchestrationDecision(
                "get_custom_strategy_capabilities",
                {},
            )
        if (
            "create_custom_strategy_spec" in tool_names
            and any(word in text for word in ("custom", "combine", "combined"))
            and "strateg" in text
            and any(word in text for word in ("create", "draft", "spec", "using"))
        ):
            return OrchestrationDecision(
                "create_custom_strategy_spec",
                _custom_strategy_spec_arguments(message),
            )
        if "run_" in text and run_id:
            return OrchestrationDecision(
                "get_backtest_result",
                {"run_id": run_id},
            )
        if (
            "run_backtest" in tool_names
            and any(
                word in text
                for word in ("backtest", "simulate", "test strategy")
            )
        ):
            strategy_name = _strategy_from_text(text)
            arguments: dict[str, Any] = {
                "strategy_name": strategy_name,
                "parameters": _strategy_parameters(text, strategy_name),
            }
            if any(
                phrase in text
                for phrase in ("semi-auto", "semi auto", "paper", "approval")
            ):
                arguments["execution_mode"] = "semi_auto"
            if "live" in text:
                arguments["execution_mode"] = "live"
            if dataset_id:
                arguments["dataset_id"] = dataset_id
            return OrchestrationDecision("run_backtest", arguments)
        if any(word in text for word in ("strategies", "strategy list", "available strategy")):
            return OrchestrationDecision("list_strategies", {})
        if "dataset_id" in text and dataset_id:
            return OrchestrationDecision(
                "get_dataset_detail",
                {"dataset_id": dataset_id},
            )
        if any(
            word in text
            for word in ("dataset", "datasets", "catalog", "what data", "nifty data")
        ):
            return OrchestrationDecision("list_datasets", {})
        return OrchestrationDecision(
            tool_name=None,
            arguments={},
            direct_response=(
                "I can inspect datasets, list strategies, run research "
                "backtests, and retrieve stored performance, risk, or order "
                "evidence. Configure a supported LLM key for model-based "
                "routing."
            ),
        )

    def compose_response(
        self,
        message: str,
        decision: OrchestrationDecision,
        tool_result: dict[str, Any],
    ) -> str:
        return _grounded_fallback_response(
            decision.tool_name or "unknown",
            tool_result,
        )


def build_orchestrator(
    *,
    api_key: str | None = None,
    model: str = "gpt-5.5",
    provider: str = "openai",
    groq_api_key: str | None = None,
    groq_model: str = "llama-3.3-70b-versatile",
    require_real_llm: bool = False,
) -> Orchestrator:
    normalized_provider = provider.strip().lower()
    if normalized_provider == "groq":
        active_key = groq_api_key or api_key
        if active_key:
            return GroqToolOrchestrator(active_key, groq_model)
        if require_real_llm:
            raise RuntimeError(
                "GROQ_API_KEY is required when IIMC_REQUIRE_REAL_LLM=true"
            )
        return OfflineOrchestrator()
    if normalized_provider not in {"openai", "openai_responses"}:
        if require_real_llm:
            raise RuntimeError(f"Unsupported LLM provider: {provider}")
        return OfflineOrchestrator()
    if api_key:
        return OpenAIResponsesOrchestrator(api_key, model)
    if require_real_llm:
        raise RuntimeError(
            "OPENAI_API_KEY is required when IIMC_REQUIRE_REAL_LLM=true"
        )
    return OfflineOrchestrator()


_ROUTER_SYSTEM_PROMPT = (
    "You are the orchestration layer for an audited trading platform. Select "
    "only registered tools. Never invent dataset IDs, run IDs, prices, P&L, "
    "risk decisions, order IDs, broker state, news, or market data. Prefer "
    "research/backtest/read-only tools unless the user explicitly asks for "
    "paper or live execution. Live execution must remain guarded by backend "
    "configuration and approval checks. For compound questions, select at "
    "most four read-only tools. Never combine a state-changing tool with "
    "another tool."
)


def _chat_messages(
    message: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    input_items = [
        {"role": item["role"], "content": item["content"]}
        for item in history[-12:]
        if item.get("role") in {"user", "assistant"}
    ]
    input_items.append({"role": "user", "content": message})
    return input_items


def _chat_completion_tools(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in registry.openai_tools()
    ]


def _tool_arguments(raw_arguments: str | None) -> dict[str, Any]:
    """Normalize provider tool arguments while preserving strict schemas."""
    parsed = json.loads(raw_arguments or "{}")
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object or null")
    return parsed


def _extract_identifier(text: str, prefix: str) -> str | None:
    matches = re.findall(
        rf"\b{re.escape(prefix)}[A-Za-z0-9_-]+\b",
        text,
    )
    field_placeholder = f"{prefix}id".lower()
    candidates = [
        value
        for value in matches
        if value.lower() != field_placeholder
    ]
    return candidates[-1] if candidates else None


def _extract_identifiers(text: str, prefix: str) -> list[str]:
    return list(
        dict.fromkeys(
            re.findall(
                rf"\b{re.escape(prefix)}[A-Za-z0-9_-]+\b",
                text,
            )
        )
    )


def _dataset_from_text(text: str) -> str | None:
    dataset_id = _extract_identifier(text, "dataset_")
    if dataset_id:
        return _clean_identifier(dataset_id)
    match = re.search(
        r"\bdataset(?:_id| id)\s*[:=]?\s*([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    ) or re.search(
        r"\bdataset\s*[:=]\s*([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    ) or re.search(
        r"\bon\s+dataset\s+([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        candidate = _clean_identifier(match.group(1))
        if candidate.lower() not in {"id", "dataset"}:
            return candidate
    match = re.search(
        r"\bon\s+(?!dataset\b)([A-Za-z][A-Za-z0-9_.-]+)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match and "dataset" in text.lower():
        return _clean_identifier(match.group(1))
    return None


def _clean_identifier(value: str) -> str:
    return value.strip().strip(".,;:)]}")


def _strategy_from_text(text: str) -> str:
    if "rsi" in text:
        return "rsi_mean_reversion"
    if "sma" in text:
        return "sma_crossover"
    if "momentum" in text or "roc" in text:
        return "momentum_roc"
    return "ema_crossover"


def _persona_from_text(text: str) -> str | None:
    if "buffett" in text or "warren" in text or "value" in text:
        return "conservative_value"
    if "momentum" in text or "intraday" in text:
        return "intraday_momentum"
    if "risk-off" in text or "risk off" in text or "defensive" in text:
        return "risk_off_capital_preservation"
    if "balanced" in text or "systematic" in text:
        return "balanced_systematic"
    return None


def _strategy_parameters(text: str, strategy_name: str) -> dict[str, Any]:
    numbers = [int(value) for value in re.findall(r"\b\d+\b", text)]
    if strategy_name in {"ema_crossover", "sma_crossover"} and len(numbers) >= 2:
        return {
            "fast_period": numbers[0],
            "slow_period": numbers[1],
        }
    if strategy_name == "rsi_mean_reversion" and numbers:
        return {"period": numbers[0]}
    if strategy_name == "momentum_roc" and numbers:
        return {"period": numbers[0]}
    return {}


def _custom_strategy_spec_arguments(message: str) -> dict[str, Any]:
    text = message.lower()
    indicators: list[dict[str, Any]] = []
    entry_rules: list[dict[str, Any]] = []
    exit_rules: list[dict[str, Any]] = []

    if "ema" in text or not any(
        word in text
        for word in ("sma", "macd", "bollinger", "band", "vwap")
    ):
        indicators.extend(
            [
                {"type": "EMA", "period": 9, "source": "close"},
                {"type": "EMA", "period": 21, "source": "close"},
            ]
        )
        entry_rules.append(
            {
                "left": "EMA_9",
                "operator": "crosses_above",
                "right": "EMA_21",
                "joiner": "AND",
            }
        )
        exit_rules.append(
            {
                "left": "EMA_9",
                "operator": "crosses_below",
                "right": "EMA_21",
                "joiner": "OR",
            }
        )
    if "sma" in text:
        indicators.extend(
            [
                {"type": "SMA", "period": 20, "source": "close"},
                {"type": "SMA", "period": 50, "source": "close"},
            ]
        )
        entry_rules.append(
            {
                "left": "SMA_20",
                "operator": "crosses_above",
                "right": "SMA_50",
                "joiner": "AND",
            }
        )
        exit_rules.append(
            {
                "left": "SMA_20",
                "operator": "crosses_below",
                "right": "SMA_50",
                "joiner": "OR",
            }
        )
    if "macd" in text:
        indicators.extend(
            [
                {
                    "name": "MACD_LINE",
                    "type": "MACD",
                    "source": "close",
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                },
                {
                    "name": "MACD_SIGNAL",
                    "type": "MACD_SIGNAL",
                    "source": "close",
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                },
            ]
        )
        entry_rules.append(
            {
                "left": "MACD_LINE",
                "operator": "crosses_above",
                "right": "MACD_SIGNAL",
                "joiner": "AND",
            }
        )
        exit_rules.append(
            {
                "left": "MACD_LINE",
                "operator": "crosses_below",
                "right": "MACD_SIGNAL",
                "joiner": "OR",
            }
        )
    if "bollinger" in text or "band" in text:
        indicators.extend(
            [
                {"name": "BB_UPPER", "type": "BB_UPPER", "period": 20, "source": "close", "stddev": 2.0},
                {"name": "BB_MIDDLE", "type": "BB_MIDDLE", "period": 20, "source": "close", "stddev": 2.0},
                {"name": "BB_LOWER", "type": "BB_LOWER", "period": 20, "source": "close", "stddev": 2.0},
            ]
        )
        entry_rules.append(
            {
                "left": "price",
                "operator": "crosses_above",
                "right": "BB_MIDDLE",
                "joiner": "AND",
            }
        )
        exit_rules.append(
            {
                "left": "price",
                "operator": "crosses_below",
                "right": "BB_MIDDLE",
                "joiner": "OR",
            }
        )
    if "vwap" in text:
        indicators.append({"type": "VWAP", "source": "close"})
        entry_rules.append(
            {"left": "price", "operator": "crosses_above", "right": "VWAP", "joiner": "AND"}
        )
        exit_rules.append(
            {"left": "price", "operator": "crosses_below", "right": "VWAP", "joiner": "OR"}
        )
    if "rsi" in text:
        indicators.append({"type": "RSI", "period": 14, "source": "close"})
        entry_rules.append(
            {"left": "RSI_14", "operator": "<", "right": 60, "joiner": "AND"}
        )
        exit_rules.append(
            {"left": "RSI_14", "operator": ">", "right": 75, "joiner": "OR"}
        )
    if any(word in text for word in ("momentum", "roc")):
        indicators.append({"type": "ROC", "period": 10, "source": "close"})
        entry_rules.append(
            {"left": "ROC_10", "operator": ">", "right": 0, "joiner": "AND"}
        )
        exit_rules.append(
            {"left": "ROC_10", "operator": "<=", "right": 0, "joiner": "OR"}
        )
    if "atr" in text:
        indicators.append({"type": "ATR", "period": 14, "source": "close"})
        entry_rules.append(
            {"left": "ATR_14", "operator": ">", "right": 0, "joiner": "AND"}
        )
    if "iv" in text or "skew" in text:
        indicators.append({"type": "IV_SKEW", "period": 14, "source": "iv"})
        entry_rules.append(
            {"left": "IV_SKEW_14", "operator": ">", "right": 0, "joiner": "AND"}
        )

    if not exit_rules:
        first_indicator = indicators[0]
        reference = str(
            first_indicator.get("name")
            or (
                f"{str(first_indicator['type']).upper()}_"
                f"{first_indicator.get('period')}"
            )
        )
        exit_rules.append(
            {"left": "price", "operator": "<", "right": reference, "joiner": "OR"}
        )

    return {
        "name": _custom_strategy_name(message),
        "description": message[:1000],
        "symbol": _symbol_from_text(message) or "MARKET",
        "timeframe": _timeframe_from_text(text),
        "indicators": indicators,
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "risk": {"max_position_size": 1, "stop_loss_pct": 0.02},
        "position_side": "short" if re.search(r"\bshort\b", text) else "long",
        "created_by": "chat_user",
    }


def _custom_strategy_name(message: str) -> str:
    match = re.search(
        r"\b(?:called|named|name)\s+([A-Za-z][A-Za-z0-9_-]{1,60})\b",
        message,
        flags=re.IGNORECASE,
    )
    if match:
        return _clean_identifier(match.group(1)).lower()
    symbol = (_symbol_from_text(message) or "market").lower()
    features = [
        label
        for keyword, label in (
            ("ema", "ema"),
            ("sma", "sma"),
            ("macd", "macd"),
            ("bollinger", "bollinger"),
            ("band", "bollinger"),
            ("vwap", "vwap"),
            ("atr", "atr"),
            ("rsi", "rsi"),
            ("momentum", "momentum"),
            ("roc", "roc"),
            ("iv", "iv"),
            ("skew", "skew"),
        )
        if keyword in message.lower()
    ]
    suffix = "_".join(dict.fromkeys(features)) or "rules"
    return f"{symbol}_{suffix}_spec"


def _symbol_from_text(text: str) -> str | None:
    upper = text.upper()
    excluded = {
        "AND",
        "API",
        "ATM",
        "BUY",
        "CALL",
        "CE",
        "CNC",
        "DATASET",
        "EMA",
        "FOR",
        "FUTURES",
        "IV",
        "LIMIT",
        "LIVE",
        "MARKET",
        "MIS",
        "NFO",
        "NRML",
        "NSE",
        "OHLCV",
        "OPTION",
        "OPTIONS",
        "ORDER",
        "PAPER",
        "PE",
        "PREPARE",
        "PUT",
        "RISK",
        "ROC",
        "RSI",
        "SELL",
        "SL",
        "SMA",
        "SPEC",
        "STRATEGY",
    }
    for pattern in (
        r"\b(?:symbol|ticker|underlying)\s*[:=]?\s*([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
        r"\b(?:for|on|trade|backtest)\s+([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = _clean_symbol(match.group(1))
            if candidate and candidate not in excluded:
                return candidate
    matches = [
        _clean_symbol(value)
        for value in re.findall(r"\b[A-Z][A-Z0-9&.-]{1,30}\b", upper)
    ]
    candidates = [
        value for value in matches
        if value and value not in excluded and not value.startswith("CUSTOM_")
    ]
    return candidates[0] if candidates else None


def _clean_symbol(value: str) -> str:
    return _clean_identifier(value).replace("&", "").replace(".", "").upper()


def _exchange_from_text(text: str, *, default: str = "NSE") -> str:
    upper = text.upper()
    for exchange in ("NSE_INDEX", "BSE_INDEX", "NFO", "BFO", "NSE", "BSE", "MCX", "CDS", "BCD"):
        if re.search(rf"\b{exchange}\b", upper):
            return exchange
    return default


def _timeframe_from_text(text: str) -> str:
    if re.search(r"\b(day|daily|1d)\b", text):
        return "1d"
    match = re.search(
        r"\b(\d+)\s*(m|min|mins|minute|minutes|h|hr|hour|hours|d|day|days)\b",
        text,
    )
    if not match:
        return "5m"
    value, unit = match.groups()
    normalized = {
        "m": "m",
        "min": "m",
        "mins": "m",
        "minute": "m",
        "minutes": "m",
        "h": "h",
        "hr": "h",
        "hour": "h",
        "hours": "h",
        "d": "d",
        "day": "d",
        "days": "d",
    }[unit]
    return f"{value}{normalized}"


def _readiness_arguments(message: str) -> dict[str, Any]:
    text = message.lower()
    symbol = _symbol_from_text(message) or "MARKET"
    asset_class = _asset_class_from_text(text)
    exchange = _exchange_from_text(
        message,
        default=_default_exchange_for_asset(asset_class),
    )
    start_date, end_date = _date_range_from_text(text)
    return {
        "symbol": symbol,
        "exchange": exchange,
        "asset_class": asset_class,
        "interval": _timeframe_from_text(text),
        "start_date": start_date,
        "end_date": end_date,
    }


def _asset_class_from_text(text: str) -> str:
    if any(word in text for word in ("crypto", "coin", "bitcoin", "btc", "eth")):
        return "crypto"
    if any(word in text for word in ("commodity", "gold", "silver", "crude", "mcx")):
        return "commodity"
    if "future" in text:
        return "futures"
    if any(word in text for word in ("option", "ce", "pe", "call", "put")):
        return "options"
    if "index" in text:
        return "index"
    return "equity"


def _default_exchange_for_asset(asset_class: str) -> str:
    return {
        "commodity": "MCX",
        "crypto": "CRYPTO",
        "futures": "NFO",
        "options": "NFO",
        "index": "NSE_INDEX",
    }.get(asset_class, "NSE")


def _date_range_from_text(text: str) -> tuple[str, str]:
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], dates[0]
    match = re.search(r"\blast\s+(\d{1,4})\s+(day|days)\b", text)
    if match:
        days = max(1, min(int(match.group(1)), 3650))
        end = date.today()
        start = end - timedelta(days=days)
        return start.isoformat(), end.isoformat()
    end = date.today()
    start = end - timedelta(days=30)
    return start.isoformat(), end.isoformat()


def _sandbox_intent_arguments(message: str, decision_id: str) -> dict[str, Any]:
    text = message.lower()
    upper = message.upper()
    quantity_match = re.search(r"\b(?:qty|quantity)\s*[:=]?\s*(\d+)\b", text)
    if quantity_match is None:
        quantity_match = re.search(r"\b(\d+)\s+(?:share|shares|lot|lots|qty)\b", text)
    if quantity_match is None:
        quantity_match = re.search(r"\b(?:buy|sell|short)\s+(\d+)\b", text)
    order_type = "MARKET"
    for candidate in ("SL-M", "LIMIT", "MARKET", "SL"):
        if candidate.lower() in text:
            order_type = candidate
            break
    exchange = "NSE"
    for candidate in ("NFO", "NSE", "BSE", "BFO", "MCX", "CDS", "BCD"):
        if re.search(rf"\b{candidate}\b", upper):
            exchange = candidate
            break
    product = "MIS"
    for candidate in ("NRML", "CNC", "MIS"):
        if re.search(rf"\b{candidate}\b", upper):
            product = candidate
            break
    strategy_match = re.search(
        r"\b(?:strategy|strategy_name)\s*[:=]\s*([A-Za-z0-9_.-]+)",
        message,
        flags=re.IGNORECASE,
    )
    limit_match = re.search(r"\blimit(?: price)?\s*[:=]?\s*(\d+(?:\.\d+)?)\b", text)
    trigger_match = re.search(r"\btrigger(?: price)?\s*[:=]?\s*(\d+(?:\.\d+)?)\b", text)
    return {
        "decision_id": decision_id,
        "symbol": _symbol_from_text(message) or "NIFTY",
        "exchange": exchange,
        "side": "SELL" if "sell" in text or "short" in text else "BUY",
        "product": product,
        "order_type": order_type,
        "quantity": int(quantity_match.group(1)) if quantity_match else 1,
        "strategy_name": (
            strategy_match.group(1)
            if strategy_match
            else _strategy_from_text(message)
        ),
        "limit_price": float(limit_match.group(1)) if limit_match else None,
        "trigger_price": float(trigger_match.group(1)) if trigger_match else None,
        "requested_by": "chat_user",
    }


def _grounded_fallback_response(
    tool_name: str,
    result: dict[str, Any],
) -> str:
    if tool_name == "list_datasets":
        datasets = result.get("datasets", [])
        if not datasets:
            return "No governed datasets were found."
        dataset_ids = ", ".join(
            str(dataset.get("dataset_id"))
            for dataset in datasets
        )
        return (
            f"Found {len(datasets)} governed dataset(s): {dataset_ids}."
        )
    if tool_name == "list_strategies":
        return (
            f"Registered {len(result.get('strategies', []))} deterministic "
            "strategy plugins."
        )
    if tool_name == "get_platform_summary":
        counts = result.get("counts", {})
        execution_paths = result.get("execution_paths", {})
        enabled_paths = [
            name
            for name, path in execution_paths.items()
            if path.get("enabled")
        ]
        return (
            f"Platform status is {result.get('status')}. Governed datasets: "
            f"{counts.get('data_catalog', 0)}; completed strategy runs: "
            f"{counts.get('strategy_runs', 0)}. Enabled execution paths: "
            f"{', '.join(enabled_paths) or 'none'}. Live trading enabled: "
            f"{result.get('safety', {}).get('live_trading_enabled')}. "
            "No synthetic fallback is allowed."
        )
    if tool_name == "search_knowledge":
        matches = result.get("matches", [])
        if not matches:
            return "No governed knowledge chunks matched the question."
        sources = ", ".join(
            f"{item['title']} ({item['chunk_id']})"
            for item in matches
        )
        return (
            f"Retrieved {len(matches)} governed knowledge chunk(s): "
            f"{sources}."
        )
    if tool_name == "check_platform_readiness":
        blocked_reasons = []
        if not result.get("supported_by_architecture", False):
            blocked_reasons.append(result.get("unsupported_reason") or "unsupported asset or symbol")
        if not result.get("local_dataset_exists", False):
            blocked_reasons.append("local historical dataset is missing")
        if result.get("analyzer_path_status") not in {None, "ready", "available"}:
            blocked_reasons.append(f"OpenAlgo analyzer path is {result.get('analyzer_path_status')}")
        if result.get("unsupported_reason"):
            blocked_reasons.append(result["unsupported_reason"])
        unique_blockers = []
        for blocker in blocked_reasons:
            if blocker and blocker not in unique_blockers:
                unique_blockers.append(blocker)
        return (
            f"Readiness for {result['symbol']} {result['asset_class']} "
            f"completed. Local dataset: {result['local_dataset_exists']}; "
            f"rows available: {result.get('rows_available', 0)}; "
            f"provider configured: {result['provider_configured']}; "
            f"verified now: {result['verified_now']}; analyzer path: "
            f"{result.get('analyzer_path_status')}; paper path: "
            f"{result.get('paper_path_status')}; live path: "
            f"{result.get('live_path_status')}. Blockers: "
            f"{'; '.join(unique_blockers) if unique_blockers else 'none'}. "
            "No synthetic market fallback was used."
        )
    if tool_name == "get_research_context":
        readiness = result["readiness"]
        news = result["news"]
        return (
            f"Research context for {readiness['symbol']} "
            f"{readiness['asset_class']} is ready at the architecture level: "
            f"{readiness['supported_by_architecture']}. Local dataset: "
            f"{readiness['local_dataset_exists']} with "
            f"{readiness['rows_available']} row(s). Stored news articles: "
            f"{len(news.get('articles', []))}. No synthetic fallback was used."
        )
    if tool_name == "create_research_brief":
        actions = result.get("next_actions", [])
        return (
            f"Created research brief {result['brief_id']} for "
            f"{result['symbol']} {result['asset_class']}. "
            f"Evidence dataset: {result.get('evidence', {}).get('dataset_id') or 'none'}; "
            f"next action: {actions[0] if actions else 'none'}. "
            "No synthetic fallback was used."
        )
    if tool_name == "get_execution_readiness":
        stages = result.get("stages", [])
        ready = [
            stage["stage"]
            for stage in stages
            if stage.get("can_start")
        ]
        blocker = result.get("next_blocker")
        return (
            f"Execution readiness for {result['symbol']} "
            f"{result['asset_class']} checked. Ready stages: "
            f"{', '.join(ready) or 'none'}. Next blocker: "
            f"{blocker['stage'] if blocker else 'none'}. "
            "No synthetic fallback was used."
        )
    if tool_name == "get_openalgo_monitor":
        return (
            f"OpenAlgo monitor status: {result['status']}. "
            f"Configured: {result['configured']}; live trading enabled: "
            f"{result['live_trading_enabled']}."
        )
    if tool_name == "search_instruments":
        return (
            f"OpenAlgo instrument search status: {result.get('status')}. "
            f"Matches: {result.get('match_count', 0)}. "
            "No synthetic contract data was used."
        )
    if tool_name == "validate_instrument_symbol":
        instrument = result.get("instrument", {})
        return (
            f"Symbol validation status: {result.get('status')}. "
            f"Resolved: {instrument.get('symbol', result.get('symbol'))} "
            f"on {instrument.get('exchange', result.get('exchange'))}. "
            "No synthetic contract data was used."
        )
    if tool_name == "resolve_option_symbol":
        return (
            f"Option symbol resolution status: {result.get('status')}. "
            f"Resolved: {result.get('resolved_symbol')} on "
            f"{result.get('resolved_exchange')}. "
            "No synthetic contract data was used."
        )
    if tool_name == "get_market_news":
        if not result.get("ok"):
            return (
                f"Market news unavailable: {result.get('message')}. "
                "No fake news was generated."
            )
        return (
            f"Fetched {result.get('article_count', 0)} provider-backed "
            f"market news article(s). Evidence: {result.get('fetch_id')}."
        )
    if tool_name == "list_strategy_personas":
        personas = result.get("personas", [])
        if not personas:
            return "No governed strategy personas are configured."
        names = ", ".join(
            f"{item.get('persona_id')} ({item.get('name')})"
            for item in personas
        )
        return f"Found {len(personas)} governed strategy persona(s): {names}."
    if tool_name == "get_strategy_persona":
        persona = result.get("persona", {})
        return (
            f"Persona {persona.get('persona_id')} is "
            f"{persona.get('name')}. Supported asset classes: "
            f"{', '.join(persona.get('asset_classes', []))}. "
            "It guides strategy choice and explanation style but does not "
            "bypass data, risk, approval, or OpenAlgo checks."
        )
    if tool_name == "list_sandbox_intents":
        intents = result.get("intents", [])
        if not intents:
            return (
                "No OpenAlgo sandbox or paper-trading intents are stored yet."
            )
        statuses = ", ".join(
            f"{item.get('intent_id')}={item.get('status')}"
            for item in intents[:8]
        )
        return (
            f"Found {len(intents)} sandbox/paper intent(s): {statuses}."
        )
    if tool_name == "prepare_sandbox_order_intent":
        return (
            f"Prepared sandbox order intent {result['intent_id']} for "
            f"{result['symbol']} {result['side']} {result['quantity']}. "
            f"Approval {result['approval_id']} is required before OpenAlgo "
            "submission."
        )
    if tool_name == "prepare_live_order_intent":
        return (
            f"Prepared live order intent {result['intent_id']} for "
            f"{result['symbol']} {result['side']} {result['quantity']}. "
            f"Approval {result['approval_id']} is mandatory before OpenAlgo "
            "live submission."
        )
    if tool_name == "assess_dataset_freshness":
        return (
            f"Dataset {result['dataset_id']} is {result['status']} for "
            f"{result['purpose']}: {result['reason']}."
        )
    if "intent_id" in result:
        return (
            f"Sandbox order intent {result['intent_id']} is "
            f"{result['status']}."
        )
    if tool_name == "run_backtest":
        return (
            f"Backtest {result['run_id']} completed for "
            f"{result['strategy']}. Net P&L: {result['net_pnl']:.2f}; "
            f"max drawdown: {result['max_drawdown']:.2f}; "
            f"return: {result['return_pct']:.4f}%."
        )
    if tool_name == "run_custom_strategy_spec":
        return (
            f"Custom strategy spec {result['custom_strategy_spec_id']} "
            f"backtest {result['run_id']} completed through the native "
            f"{result['strategy']} runtime. Net P&L: "
            f"{result['net_pnl']:.2f}; max drawdown: "
            f"{result['max_drawdown']:.2f}; return: "
            f"{result['return_pct']:.4f}%. No generated code was executed."
        )
    if "run_id" in result:
        return f"Retrieved stored evidence for run {result['run_id']}."
    return "The requested tool completed successfully."


def grounded_multi_tool_response(
    results: list[tuple[str, dict[str, Any]]],
) -> str:
    """Compose a deterministic, evidence-backed answer for read-only checks."""
    summaries = [
        _grounded_fallback_response(tool_name, result)
        for tool_name, result in results
    ]
    return "Completed governed read-only checks:\n- " + "\n- ".join(summaries)
