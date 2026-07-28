"""Routing: deciding which tool a message means, and running it.

The three-tier orchestration lives here — the deterministic regex router
(``OfflineOrchestrator``), the LLM function-calling routers, and the message
parsing they share. Rendering the result is :mod:`.renderers`; explaining a
concept or declining a question is :mod:`.education`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..services.watch_service import parse_watch_request as _parse_watch_request
from ..tools.registry import ToolRegistry
from .education import (
    _EDUCATION_PREFIX_RE,
    _FINANCE_ACRONYMS,
    _domain_refusal_response,
    _education_lookup,
    _educational_response,
    _is_open_ended_advice,
    _off_topic_category,
    _open_ended_advice_response,
)
from .renderers import _grounded_fallback_response
from .text import (
    _ROUTER_SYSTEM_PROMPT,
    _chat_messages,
    _closest_action_response,
    _contains_any_word,
    _dataset_from_text,
    _exchange_from_text,
    _extract_identifier,
    _extract_identifiers,
    _is_groq_rate_limited,
    _is_groq_routing_failure,
    _is_history_import_request,
    _is_market_outlook_request,
    _is_market_price_request,
    _is_market_quote_follow_up,
    _is_paper_trade_workflow_request,
    _is_sandbox_intent_request,
    _is_strategy_creation_request,
    _market_outlook_query,
    _market_outlook_symbol,
    _market_query_for_request,
    _market_query_from_text,
    _normalize_intent_text,
    _openalgo_snapshot_types,
    _parse_direct_order,
    _parse_technical_screen,
    _persona_from_text,
    _readiness_arguments,
    _references_unspecified_personal_strategy,
    _sandbox_intent_arguments,
    _strategy_from_text,
    _strategy_parameters,
    _symbol_from_text,
    _symbols_from_text,
    _tool_arguments,
)


logger = logging.getLogger(__name__)


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
    # Authoritative direct responses (greetings, domain refusals, education)
    # are final even when an LLM router is configured.
    authoritative: bool = False


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








class OpenAIResponsesOrchestrator:
    mode = "openai_responses"

    def __init__(
        self,
        api_key: str,
        model: str,
        fallback_model: str | None = "llama-3.1-8b-instant",
    ) -> None:
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

    def __init__(
        self,
        api_key: str,
        model: str,
        fallback_model: str | None = "llama-3.1-8b-instant",
    ) -> None:
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
        self.fallback_model = (
            fallback_model
            if fallback_model and fallback_model != model
            else None
        )
        self._primary_rate_limited = False

    def select_tool(
        self,
        message: str,
        history: list[dict[str, str]],
        registry: ToolRegistry,
    ) -> OrchestrationDecision:
        deterministic_decision = OfflineOrchestrator().select_tool(
            message,
            history,
            registry,
        )
        if deterministic_decision.tool_name is not None or (
            deterministic_decision.direct_response
            and deterministic_decision.authoritative
        ):
            return deterministic_decision
        messages = _chat_messages(message, history)
        if self._primary_rate_limited:
            fallback_decision = self._fallback_plain_decision(message, history)
            return fallback_decision or deterministic_decision
        try:
            response = self._routing_response(self.model, messages, registry)
        except Exception as exc:
            if _is_groq_rate_limited(exc) and self.fallback_model:
                self._primary_rate_limited = True
                logger.warning(
                    "Groq primary routing is rate-limited; using fallback model",
                    extra={"error_type": type(exc).__name__},
                )
                fallback_decision = self._fallback_plain_decision(
                    message,
                    history,
                )
                return fallback_decision or deterministic_decision
            elif not _is_groq_routing_failure(exc):
                raise
            else:
                logger.warning(
                    "Groq routing unavailable; using deterministic routing",
                    extra={"error_type": type(exc).__name__},
                )
                return deterministic_decision
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

    def _routing_response(
        self,
        model: str,
        messages: list[dict[str, str]],
        registry: ToolRegistry,
    ) -> Any:
        return self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                *messages,
            ],
            tools=_chat_completion_tools(registry),
            tool_choice="auto",
            temperature=0,
        )

    def _fallback_plain_decision(
        self,
        message: str,
        history: list[dict[str, str]],
    ) -> OrchestrationDecision | None:
        if not self.fallback_model:
            return None
        try:
            response = self.client.chat.completions.create(
                model=self.fallback_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a knowledgeable assistant for a trading "
                            "and investing platform. Answer questions about "
                            "trading, markets, investing, finance, economics, "
                            "companies, financial history, and how to use this "
                            "platform — including educational, conceptual, "
                            "historical, and 'who/which/compare' questions "
                            "(e.g. famous investors, what value investing is, "
                            "what caused the 2008 crisis). Answer them directly "
                            "and helpfully. Do not invent current prices, news, "
                            "broker state, P&L, risk decisions, or trading "
                            "outcomes, and note that live orders require "
                            "explicit human approval. Only decline requests "
                            "clearly unrelated to finance and markets (weather, "
                            "sports, cooking, entertainment, personal or coding "
                            "help), in one short sentence."
                        ),
                    },
                    *_chat_messages(message, history),
                ],
                temperature=0,
                max_tokens=400,
            )
        except Exception as exc:
            logger.warning(
                "Groq fallback conversational model unavailable",
                extra={"error_type": type(exc).__name__},
            )
            return None
        answer = response.choices[0].message.content
        if not answer or not answer.strip():
            return None
        return OrchestrationDecision(
            tool_name=None,
            arguments={},
            direct_response=answer.strip(),
        )

    def compose_response(
        self,
        message: str,
        decision: OrchestrationDecision,
        tool_result: dict[str, Any],
    ) -> str:
        system_prompt = (
            "You are a trading research assistant. Write a clear, "
            "well-structured markdown answer to the user's question using "
            "ONLY the values present in the provided tool result JSON. "
            "Use short paragraphs, bold key figures, and bullet lists where "
            "they help. Be genuinely informative: explain what the numbers "
            "mean for the user's question, not just what they are. Never "
            "invent prices, P&L, broker state, news, or any value missing "
            "from the JSON — if something is unavailable, say so plainly "
            "and suggest what the user can do next. Speak directly to the "
            "user; never mention tools, JSON, field names, or internal "
            "identifiers. Do not give buy/sell recommendations. NEVER state "
            "or imply that an order was placed, submitted, confirmed, "
            "approved, cancelled, or executed — this response performs no "
            "actions; orders happen only through the platform's separate "
            "approval workflow."
        )
        if decision.call_id is None:
            tool_messages = [
                {
                    "role": "user",
                    "content": (
                        f"Question: {message}\n\n"
                        f"Tool `{decision.tool_name}` returned this JSON:\n"
                        f"{json.dumps(tool_result, default=str)}"
                    ),
                },
            ]
        else:
            tool_messages = [
                {"role": "user", "content": message},
                *decision.provider_items,
                {
                    "role": "tool",
                    "tool_call_id": decision.call_id,
                    "content": json.dumps(tool_result, default=str),
                },
            ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *tool_messages,
                ],
                temperature=0,
            )
            answer = (response.choices[0].message.content or "").strip()
            if answer:
                return answer
        except Exception as exc:
            logger.warning(
                "Groq response composition failed; using grounded tool response",
                extra={"error_type": type(exc).__name__},
            )
        return _grounded_fallback_response(
            decision.tool_name or "unknown",
            tool_result,
        )


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
        text = _normalize_intent_text(message)
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

        if re.match(
            r"\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)"
            r"|greetings|howdy|sup)\b",
            text,
        ) and len(text.split()) <= 6:
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Hello! I can help with live quotes and market news, "
                    "company research, building and backtesting a strategy in "
                    "plain language, and placing or tracking orders. "
                    "What would you like to do?"
                ),
                authoritative=True,
            )
        if re.search(
            r"(?:\b(?:ignore|bypass|skip|disable|override|remove)\b"
            r".{0,40}\b(?:risk|approval|confirmation|safety|limit|rule)s?\b)"
            r"|\bpretend\b"
            r"|without\s+(?:approval|confirmation|asking)"
            r"|don'?t\s+show\s+the\s+confirmation",
            text,
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "I can't do that. Risk checks, order previews, and "
                    "human approval are enforced by the platform itself — "
                    "they cannot be bypassed, skipped, or simulated through "
                    "conversation. No order has been placed. If you want to "
                    "trade, I can prepare an order for you to approve "
                    "explicitly."
                ),
                authoritative=True,
            )
        off_topic_category = _off_topic_category(text)
        if off_topic_category:
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=_domain_refusal_response(off_topic_category),
                authoritative=True,
            )
        if _is_open_ended_advice(text, message):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=_open_ended_advice_response(),
                authoritative=True,
            )
        # Conceptual comparisons ("difference between a mutual fund and an
        # ETF", "value vs growth investing") are educational, not a broker
        # lookup. Answer directly when no real ticker is present, so the router
        # never mistakes them for account/quote calls. Runs before any tool
        # routing so "fund"/"index" don't trigger an account snapshot. Finance
        # acronyms (ETF, IPO, …) are not treated as tickers.
        _upper_tokens = re.findall(r"\b[A-Z]{2,}\b", message)
        _real_tickers = [
            token for token in _upper_tokens if token not in _FINANCE_ACRONYMS
        ]
        if (
            re.search(
                r"\b(?:difference between|compare|versus)\b|\bvs\.?\b", text
            )
            and not _real_tickers
            and not re.search(r"\bmy\s+", text)
            and any(
                word in text
                for word in (
                    "fund", "etf", "stock", "share", "bond", "option",
                    "future", "index", "strateg", "invest", "market",
                    "ratio", "dividend", "growth", "value", "cap", "asset",
                )
            )
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=_educational_response(
                    message.strip().rstrip("?")
                ),
            )
        # A *qualitative* comparison of two or more REAL instruments ("which is
        # stronger, INFY or TCS", "compare RELIANCE and TCS fundamentally") → the
        # plan-and-execute research agent. A bare "compare A vs B" is left to the
        # faster side-by-side quote route below, so this only fires when the ask
        # is clearly about strength/quality, not just price.
        if (
            "compare_investments" in tool_names
            and (
                re.search(r"\bstronger\b|\bwhich is (?:a\s+)?better\b", text)
                or (
                    re.search(r"\bcompare\b|\bversus\b|\bvs\.?\b", text)
                    and re.search(r"fundamental|financ|\binvest", text)
                )
            )
            and not re.search(r"\bstrateg|\bbacktest|\brun_\w+", text)
        ):
            compare_symbols = _symbols_from_text(message)
            if len(compare_symbols) >= 2:
                return OrchestrationDecision(
                    "compare_investments",
                    {
                        "symbols": compare_symbols,
                        "exchange": _exchange_from_text(message, default="NSE"),
                    },
                )
        if (
            "approve_pending_order" in tool_names
            and re.search(r"\bapprove\b", text)
            and re.search(
                r"\b(?:order|intent|trade|pending|it)\b"
                r"|intent_\w+|^\s*approve\s*[?!.]*$",
                text,
            )
        ):
            return OrchestrationDecision(
                "approve_pending_order",
                {"intent_id": _extract_identifier(message, "intent_")},
            )
        if (
            "square_off_all" in tool_names
            and re.search(
                r"\b(?:square[\s-]*off|exit\s+all|close\s+(?:all|everything|"
                r"my\s+positions?))\b",
                text,
            )
        ):
            return OrchestrationDecision("square_off_all", {})
        if (
            "cancel_all_orders" in tool_names
            and re.search(
                r"\bcancel\s+(?:all|my|pending|every)\b.*\border",
                text,
            )
        ):
            return OrchestrationDecision("cancel_all_orders", {})
        if re.match(
            r"\s*(?:help(?:\s+me)?|please\s+help)\s*[?!.]*$",
            text,
        ) or re.match(
            r"\s*(?:what can you do|what do you do"
            r"|how do i use|how does this work|capabilities)\b",
            text,
        ):
            if "get_platform_summary" in tool_names:
                return OrchestrationDecision(
                    "get_platform_summary", {}
                )
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=_closest_action_response(text),
            )

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
        sandbox_intent_request = _is_sandbox_intent_request(text)
        if (
            "get_execution_readiness" in tool_names
            and _is_paper_trade_workflow_request(text)
        ):
            return OrchestrationDecision(
                "get_execution_readiness",
                _readiness_arguments(message),
            )
        # "walk-forward / out-of-sample / is that strategy robust for SYMBOL" →
        # the validation agent. Checked before the optimiser route so the more
        # specific validation phrasing wins.
        if (
            "validate_strategy_walk_forward" in tool_names
            and re.search(
                r"\b(?:walk[\s-]*forward|out[\s-]*of[\s-]*sample|overfit"
                r"|robust(?:ness)?|hold[\s-]*up|validate)\b",
                text,
            )
            and re.search(r"\bstrateg|\bema\b|\bsma\b", text)
        ):
            wf_symbol = _symbol_from_text(message)
            if wf_symbol:
                wf_strategy = (
                    "sma_crossover" if re.search(r"\bsma\b", text)
                    else "ema_crossover"
                )
                return OrchestrationDecision(
                    "validate_strategy_walk_forward",
                    {
                        "symbol": wf_symbol,
                        "exchange": _exchange_from_text(message, default="NSE"),
                        "strategy_name": wf_strategy,
                    },
                )
        # "find/optimise/tune/best strategy for SYMBOL" → the optimizer agent
        # (searches a parameter grid). Runs before the compile route so it wins
        # over "create a strategy". Needs a concrete symbol.
        if (
            "run_strategy_optimization" in tool_names
            and re.search(
                r"\b(?:optimi[sz]e|optimi[sz]ation|tune|discover|find|search"
                r"\s+for|best|profitable|winning)\b",
                text,
            )
            and re.search(r"\bstrateg", text)
        ):
            opt_symbol = _symbol_from_text(message)
            if opt_symbol:
                opt_strategy = (
                    "sma_crossover" if re.search(r"\bsma\b", text)
                    else "ema_crossover"
                )
                return OrchestrationDecision(
                    "run_strategy_optimization",
                    {
                        "symbol": opt_symbol,
                        "exchange": _exchange_from_text(message, default="NSE"),
                        "strategy_name": opt_strategy,
                    },
                )
        if (
            "compile_custom_strategy_spec" in tool_names
            and _is_strategy_creation_request(text)
        ):
            return OrchestrationDecision(
                "compile_custom_strategy_spec",
                {"text": message},
            )
        # Long-term memory is checked before the broker/account routes: an
        # explicit "remember that I like swing trades" must not be mistaken for a
        # tradebook lookup just because it contains the word "trades". Recall (a
        # question) is checked before store so "do you remember ...?" isn't saved.
        if "recall_memory" in tool_names and (
            re.search(
                r"\b(?:do you remember|what do you remember|"
                r"what do you know about me|my (?:saved )?notes|"
                r"what (?:did|have) (?:we|you) (?:find|found|research))\b",
                text,
            )
            or re.search(
                r"\b(?:what|which)\b.{0,40}\b(?:remember|noted|on file|"
                r"find(?:ing)?s?\s+(?:on|about)|"
                r"research(?:ed)?\s+(?:on|about))\b",
                text,
            )
        ):
            return OrchestrationDecision("recall_memory", {"query": message})
        remember_match = re.match(
            r"\s*(?:please\s+|can you\s+|could you\s+|pls\s+)?"
            r"(?:remember|note|keep in mind|make a note of|don'?t forget)\b\s*"
            r"(?:that\s+|to\s+|about\s+)?(.+)",
            message,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if remember_match and "remember" in tool_names:
            note = remember_match.group(1).strip(" .\t\n")
            if note:
                return OrchestrationDecision("remember", {"note": note})
        if (
            "import_openalgo_history" in tool_names
            and _is_history_import_request(text)
        ):
            return OrchestrationDecision(
                "import_openalgo_history",
                _readiness_arguments(message),
            )
        snapshot_types = _openalgo_snapshot_types(text)
        if "get_openalgo_monitor" in tool_names and (
            len(snapshot_types) > 1
            or any(
                phrase in text
                for phrase in (
                    "my account",
                    "account status",
                    "trading account",
                    "broker status",
                )
            )
        ):
            return OrchestrationDecision("get_openalgo_monitor", {})
        if (
            "get_openalgo_snapshot" in tool_names
            and not sandbox_intent_request
            and len(snapshot_types) == 1
        ):
            return OrchestrationDecision(
                "get_openalgo_snapshot",
                {"snapshot_type": snapshot_types[0]},
            )
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
            "get_market_quote" in tool_names
            and "market status" in text
        ):
            return OrchestrationDecision(
                "get_market_quote",
                {
                    "query": _market_query_for_request(message, history),
                    "exchange": _exchange_from_text(message, default="NSE"),
                },
            )
        if (
            "get_market_quote" in tool_names
            and (
                _is_market_price_request(text)
                or _is_market_quote_follow_up(text, history)
            )
        ):
            return OrchestrationDecision(
                "get_market_quote",
                {
                    "query": _market_query_for_request(message, history),
                    "exchange": _exchange_from_text(message, default="NSE"),
                },
            )
        if (
            "get_market_news" in tool_names
            and _is_market_outlook_request(text)
        ):
            outlook_symbol = _market_outlook_symbol(message)
            return OrchestrationDecision(
                "get_market_news",
                {
                    "query": _market_outlook_query(message, outlook_symbol),
                    "symbol": outlook_symbol,
                },
            )
        if (
            "get_market_news" in tool_names
            and any(
                phrase in text
                for phrase in (
                    "news",
                    "headline",
                    "research update",
                    "market update",
                    "market status",
                    "market scenario",
                    "market view",
                    "current scenario",
                    "current view",
                    "outlook",
                )
            )
            and not _is_strategy_creation_request(text)
        ):
            symbol = _symbol_from_text(message)
            return OrchestrationDecision(
                "get_market_news",
                {
                    "query": _market_query_from_text(message),
                    "symbol": symbol,
                },
            )
        strong_persona_words = (
            "persona",
            "personas",
            "profile",
            "buffett",
            "warren",
            "risk-off",
            "risk off",
        )
        weak_persona_words = ("style", "conservative", "momentum")
        if (
            "list_strategy_personas" in tool_names
            and not ("custom" in text and "strateg" in text)
            and (
                any(word in text for word in strong_persona_words)
                or (
                    any(word in text for word in weak_persona_words)
                    and not _EDUCATION_PREFIX_RE.match(text)
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
            "mark_portfolio_to_market" in tool_names
            and re.search(
                r"\bmark\b.{0,30}\bmarket\b"
                r"|\bportfolio\b.{0,25}\blive\s+(?:p&?l|pnl|value|equity)\b",
                text,
            )
        ):
            if portfolio_id:
                return OrchestrationDecision(
                    "mark_portfolio_to_market",
                    {"portfolio_id": portfolio_id},
                )
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Which portfolio? Provide its id (portfolio_...), or "
                    "say 'list portfolios' to see them."
                ),
            )
        watchlist_add = re.search(
            r"\b(?:add|put)\s+([A-Za-z][\w&]{1,19})\s+"
            r"(?:to|on)\s+(?:my\s+|the\s+)?watch\s*list\b",
            message,
            flags=re.IGNORECASE,
        )
        if watchlist_add and "add_watchlist_symbol" in tool_names:
            return OrchestrationDecision(
                "add_watchlist_symbol",
                {"symbol": watchlist_add.group(1).upper()},
            )
        watchlist_remove = re.search(
            r"\b(?:remove|delete|drop)\s+([A-Za-z][\w&]{1,19})\s+from\s+"
            r"(?:my\s+|the\s+)?watch\s*list\b",
            message,
            flags=re.IGNORECASE,
        )
        if watchlist_remove and "remove_watchlist_symbol" in tool_names:
            return OrchestrationDecision(
                "remove_watchlist_symbol",
                {"symbol": watchlist_remove.group(1).upper()},
            )
        if (
            "list_watchlist" in tool_names
            and re.search(r"\b(?:show|list|my)\b.{0,15}\bwatch\s*list\b", text)
        ):
            return OrchestrationDecision("list_watchlist", {})
        # Technical watch/monitor agent — distinct from the watchlist above.
        # Check / list / stop are handled before "watch X for <condition>".
        _is_watchlist = re.search(r"watch\s*list", text)
        if (
            not _is_watchlist
            and "check_watches" in tool_names
            and re.search(
                r"\b(?:check|run|evaluate)\b.{0,20}\bwatch(?:es)?\b"
                r"|\bwatch(?:es)?\b.{0,20}\b(?:fired|triggered|hit)\b",
                text,
            )
        ):
            return OrchestrationDecision("check_watches", {})
        if (
            not _is_watchlist
            and "list_watches" in tool_names
            and re.search(r"\b(?:show|list|my)\b.{0,12}\bwatch(?:es)?\b", text)
        ):
            return OrchestrationDecision("list_watches", {})
        watch_stop = re.search(
            r"\b(?:stop\s+watching|unwatch|remove\s+(?:the\s+)?watch"
            r"\s+(?:on|for)?)\s+([A-Za-z][\w&.-]{1,19})",
            message,
            flags=re.IGNORECASE,
        )
        if watch_stop and "remove_watch" in tool_names:
            return OrchestrationDecision(
                "remove_watch", {"symbol": watch_stop.group(1).upper()}
            )
        if (
            not _is_watchlist
            and "create_watch" in tool_names
            and re.search(r"\bwatch\b|\bmonitor\b|\balert\s+me\b", text)
        ):
            parsed = _parse_watch_request(message)
            watch_symbol = _symbol_from_text(message) if parsed else None
            if parsed and watch_symbol:
                return OrchestrationDecision(
                    "create_watch",
                    {
                        "symbol": watch_symbol,
                        "condition": parsed["condition"],
                        "threshold": parsed["threshold"],
                        "exchange": _exchange_from_text(message, default="NSE"),
                    },
                )
        screen_args = _parse_technical_screen(text)
        if screen_args and "run_technical_screen" in tool_names:
            return OrchestrationDecision(
                "run_technical_screen",
                screen_args,
            )
        alert_match = (
            re.search(
                r"([A-Za-z][\w&]{1,19})\s+(?:goes?\s+|is\s+|price\s+|falls?\s+"
                r"|rises?\s+|drops?\s+|crosses?\s+)?"
                r"(above|below|over|under|falls|rises|drops|crosses)\s+"
                r"(?:rs\.?\s*|₹\s*)?(\d+(?:\.\d+)?)",
                message,
                flags=re.IGNORECASE,
            )
            if re.search(r"\b(?:alert|notify)\b", text)
            else None
        )
        if alert_match and "create_price_alert" in tool_names:
            direction_word = alert_match.group(2).lower()
            direction = (
                "below"
                if direction_word in {"below", "under", "falls", "drops"}
                else "above"
            )
            return OrchestrationDecision(
                "create_price_alert",
                {
                    "symbol": alert_match.group(1).upper(),
                    "direction": direction,
                    "threshold": float(alert_match.group(3)),
                },
            )
        if (
            "list_price_alerts" in tool_names
            and re.search(r"\b(?:my|list|show|active)\b.{0,20}\balerts?\b", text)
        ):
            return OrchestrationDecision("list_price_alerts", {})
        if (
            "get_option_chain" in tool_names
            and re.search(
                r"\boption[\s-]*chain\b|\bput[\s-]*call\s+ratio\b|\bpcr\b"
                r"|\bhighest\s+(?:call\s+|put\s+)?open\s+interest\b"
                r"|\batm\s+(?:strike|straddle|call|put)\b"
                r"|\bstraddle\s+(?:cost|premium)\b",
                text,
            )
        ):
            chain_symbol = None
            named = re.search(
                r"\b(banknifty|bank\s*nifty|finnifty|nifty|sensex)\b", text,
            )
            if named:
                chain_symbol = named.group(1).replace(" ", "").upper()
            chain_symbol = chain_symbol or _symbol_from_text(message) or "NIFTY"
            exchange = (
                "BSE_INDEX"
                if chain_symbol == "SENSEX"
                else "NSE_INDEX"
                if chain_symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY"}
                else "NSE"
            )
            return OrchestrationDecision(
                "get_option_chain",
                {"underlying": chain_symbol, "exchange": exchange},
            )
        url_match = re.search(r"https?://[^\s\"'<>]+", message)
        if (
            url_match
            and "fetch_web_document" in tool_names
            and re.search(
                r"\b(?:fetch|read|store|save|index|import|get|pull"
                r"|download|summari[sz]e|analy[sz]e)\b",
                text,
            )
        ):
            return OrchestrationDecision(
                "fetch_web_document",
                {"url": url_match.group(0).rstrip(".,)")},
            )
        doc_type_words = (
            r"annual\s+report|quarterly\s+report|earnings\s+(?:call|report)|"
            r"report|filing|transcript|10-?k|10-?q|prospectus|whitepaper|"
            r"white\s+paper|press\s+release|document|doc"
        )
        if (
            "find_and_analyze_document" in tool_names
            and re.search(
                r"\b(?:analy[sz]e|summari[sz]e|review|read|explain)\b", text
            )
            and re.search(rf"\b(?:{doc_type_words})\b", text)
            and not _is_strategy_creation_request(text)
            and not re.search(r"\bfundamental", text)
        ):
            quoted = re.search(r"['\"]([^'\"]{2,120})['\"]", message)
            if quoted:
                subject = quoted.group(1).strip()
            else:
                subject = re.sub(
                    r"(?is)^.*?\b"
                    r"(?:analy[sz]e|summari[sz]e|review|read|explain)\b\s*",
                    "",
                    message,
                )
                subject = re.sub(
                    r"(?i)^(?:(?:the|this|that|a|an|my|our|uploaded|latest)"
                    r"\s+)+",
                    "",
                    subject,
                )
                subject = re.sub(
                    r"(?i)^(?:document|doc|report|filing|transcript)\s+"
                    r"(?:called\s+|named\s+|titled\s+|for\s+|of\s+|on\s+"
                    r"|about\s+)?",
                    "",
                    subject,
                )
                subject = subject.strip(" \"'?.!")
            meaningful = re.sub(
                r"(?i)\b(?:document|doc|report|filing|transcript|annual"
                r"|quarterly|earnings|call|prospectus|whitepaper|paper"
                r"|the|a|an|uploaded|latest)\b",
                "",
                subject,
            ).strip(" \"'?.!")
            if not subject or not meaningful:
                return OrchestrationDecision(
                    tool_name=None,
                    arguments={},
                    direct_response=(
                        "Which document should I analyze? Name a report — e.g. "
                        "'analyze the Tata Motors annual report' — paste a URL, "
                        "or upload it in the Data tab."
                    ),
                )
            return OrchestrationDecision(
                "find_and_analyze_document",
                {"query": subject},
            )
        screen_match = re.search(
            r"(?:run\s+(?:the\s+)?([\w-]+)\s+screen"
            r"|screen\s+for\s+([\w-]+))\b",
            text,
        )
        if screen_match and "run_screen" in tool_names:
            screen_name = (
                screen_match.group(1) or screen_match.group(2)
            ).replace("-", "_")
            return OrchestrationDecision(
                "run_screen",
                {"name": screen_name},
            )
        if (
            "analyze_fundamentals" in tool_names
            and re.search(
                r"\b(?:fundamentally|fundamental\s+analysis"
                r"|fundamentals\s+of|analy[sz]e\s+fundamentals)\b",
                text,
            )
        ):
            symbol_match = re.search(
                r"([A-Za-z][\w&]{1,19})\s+fundamentally\b",
                message,
                flags=re.IGNORECASE,
            ) or re.search(
                r"fundamentals?\s+(?:analysis\s+)?(?:of|for)\s+"
                r"([A-Za-z][\w&]{1,19})",
                message,
                flags=re.IGNORECASE,
            )
            symbol = (
                symbol_match.group(1).upper()
                if symbol_match
                else _symbol_from_text(message)
            )
            if symbol:
                return OrchestrationDecision(
                    "analyze_fundamentals",
                    {"symbol": symbol},
                )
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "Which company should I analyze fundamentally? Say "
                    "'analyze TCS fundamentally'. You'll need to add its "
                    "financial statements in the Data tab first."
                ),
            )
        # Broad "research / deep dive / analyse SYMBOL" → the multi-analyst
        # research agent. Runs after the fundamentals and document routes so
        # "analyse X fundamentally" and "analyse the X report" keep their
        # dedicated handlers.
        # A "deep dive / full research report" gets the iterative, self-critiquing
        # loop (plan → gather → critique → refine → cited report). Checked before
        # the one-shot deep_research fan-out below so the deeper phrasings win.
        if (
            "deep_research_report" in tool_names
            and re.search(
                r"\b(?:deep[\s-]*dive|in[\s-]*depth|full\s+research"
                r"|research\s+report|detailed\s+research|thorough\s+research"
                r"|comprehensive\s+research|deep\s+research)\b",
                text,
            )
            and not re.search(r"\bfundamental", text)
            # "research brief / market brief / brief for" is the separate
            # create_research_brief feature — don't hijack it ("briefing" is
            # fine; \bbrief\b won't match it).
            and not re.search(r"\bbrief\b", text)
            and not _is_strategy_creation_request(text)
        ):
            report_symbol = _symbol_from_text(message)
            if report_symbol:
                return OrchestrationDecision(
                    "deep_research_report",
                    {
                        "symbol": report_symbol,
                        "exchange": _exchange_from_text(message, default="NSE"),
                    },
                )
        if (
            "deep_research" in tool_names
            and re.search(
                r"\b(?:research|deep[\s-]*dive|full\s+analysis|briefing"
                r"|analy[sz]e|analysis\s+of|overview\s+(?:of|on)|study"
                r"|look\s+into|run[\s-]*down|tell\s+me\s+about"
                r"|everything\s+(?:about|worth\s+knowing)|dig\s+into|lowdown"
                r"|full\s+(?:picture|rundown|breakdown|profile)"
                r"|breakdown\s+of|profile\s+of)\b",
                text,
            )
            and not re.search(r"\bfundamental", text)
            and not re.search(
                r"\b(?:document|doc|report|filing|transcript)\b", text
            )
            # Defer "research brief / market brief / brief for" to the separate
            # create_research_brief feature ("briefing" is unaffected).
            and not re.search(r"\bbrief\b", text)
            # Defer the "market research context / research context" readiness+news
            # flow to its dedicated get_research_context tool.
            and not re.search(r"\b(?:research\s+context|market\s+research)\b", text)
            and not _is_strategy_creation_request(text)
        ):
            research_symbol = _symbol_from_text(message)
            if research_symbol:
                return OrchestrationDecision(
                    "deep_research",
                    {
                        "symbol": research_symbol,
                        "exchange": _exchange_from_text(message, default="NSE"),
                    },
                )
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
        direct_order = _parse_direct_order(message, text)
        if direct_order and "prepare_direct_order" in tool_names:
            return OrchestrationDecision(
                "prepare_direct_order",
                direct_order,
            )
        if (
            "prepare_sandbox_order_intent" in tool_names
            and sandbox_intent_request
        ):
            decision_id = _extract_identifier(message, "risk_")
            if not decision_id:
                return OrchestrationDecision(
                    tool_name=None,
                    arguments={},
                    direct_response=(
                        "To place a paper order, just tell me the order — "
                        "e.g. 'buy 10 RELIANCE at market' — and I'll prepare "
                        "it for your approval."
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
        if (
            ("fresh" in text or "stale" in text)
            and not intent_id
        ):
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
                    "Which instrument's data freshness should I check? "
                    "For example: 'is my RELIANCE data fresh?'"
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
            "mark_portfolio_to_market" in tool_names
            and portfolio_id
            and re.search(
                r"\bmark(?:et)?\b.{0,40}\bmarket\b"
                r"|\blive\s+(?:p&?l|pnl|value|equity)\b",
                text,
            )
        ):
            return OrchestrationDecision(
                "mark_portfolio_to_market",
                {"portfolio_id": portfolio_id},
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
                authoritative=True,
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
        _perf_words = ("performance", "drawdown", "equity", "p&l", "pnl")
        if (
            _contains_any_word(text, _perf_words)
            and run_id
            and not re.search(r"\bmy\s+", text)
        ):
            return OrchestrationDecision(
                "get_performance",
                {"run_id": run_id},
            )
        if (
            _contains_any_word(text, _perf_words)
            and not run_id
            and not re.search(r"\bmy\s+", text)
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
                        "Tell me which instrument to backtest this saved "
                        "strategy on, e.g. 'backtest my strategy on RELIANCE'."
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
            if _references_unspecified_personal_strategy(text):
                return OrchestrationDecision(
                    tool_name=None,
                    arguments={},
                    direct_response=(
                        "Which strategy should I backtest? Name one (like "
                        "'EMA crossover') or describe it, e.g. 'backtest EMA "
                        "9/21 crossover on RELIANCE'."
                    ),
                )
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
            else:
                requested_symbol = _symbol_from_text(message)
                if requested_symbol:
                    readiness = _readiness_arguments(message)
                    arguments.update(
                        {
                            "symbol": requested_symbol,
                            "exchange": readiness["exchange"],
                            "asset_class": readiness["asset_class"],
                            "interval": readiness["interval"],
                        }
                    )
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

        if re.match(
            r"\s*(?:thank|thanks|thx|bye|goodbye|good\s*bye|see\s+you"
            r"|cheers|that'?s?\s+all|no\s+more)\b",
            text,
        ):
            return OrchestrationDecision(
                tool_name=None,
                arguments={},
                direct_response=(
                    "You're welcome! Feel free to come back any time for "
                    "quotes, news, strategy creation, backtests, or account "
                    "monitoring."
                ),
                authoritative=True,
            )

        if (
            _EDUCATION_PREFIX_RE.match(text)
            and not re.search(r"\bmy\s+", text)
            and not re.search(
                r"\b(?:pe ratio|p/e ratio|eps|market cap|dividend|book value"
                r"|fundamental|top\s+gainer|top\s+loser|52[\s-]*week"
                r"|sector)\b",
                text,
            )
        ):
            concept = re.sub(
                r"^\s*(?:what\s+(?:is|are|does)|explain|define|meaning\s+of"
                r"|tell\s+me\s+about|how\s+does|describe|teach\s+me)\s+",
                "",
                text,
            ).strip().rstrip("?")
            if concept:
                known = _education_lookup(concept)
                if known:
                    return OrchestrationDecision(
                        tool_name=None,
                        arguments={},
                        direct_response=known,
                        authoritative=True,
                    )
                # A general finance concept: prefer a direct answer. The
                # response is non-authoritative, so when an LLM is configured
                # it answers from its own knowledge; offline it falls back to
                # the deterministic educational reply. (Stored-document search
                # is reserved for explicit "search my documents" requests.)
                return OrchestrationDecision(
                    tool_name=None,
                    arguments={},
                    direct_response=_educational_response(concept),
                )

        if not re.search(r"\bdocuments?\b", text) and any(
            phrase in text
            for phrase in (
                "pe ratio",
                "p/e ratio",
                "eps",
                "earnings per share",
                "market cap",
                "market capitalization",
                "dividend yield",
                "dividend",
                "book value",
                "face value",
                "roe",
                "return on equity",
                "debt to equity",
                "fundamental",
            )
        ):
            symbol = _symbol_from_text(message)
            if symbol and "get_market_quote" in tool_names:
                return OrchestrationDecision(
                    "get_market_quote",
                    {
                        "query": symbol,
                        "exchange": _exchange_from_text(message, default="NSE"),
                    },
                )
            if "get_market_news" in tool_names:
                return OrchestrationDecision(
                    "get_market_news",
                    {
                        "query": _market_query_from_text(message),
                        "symbol": symbol,
                    },
                )

        if re.search(
            r"\b(?:top\s+(?:gainer|loser|performer|mover)s?"
            r"|most\s+(?:active|traded)"
            r"|52[\s-]*week\s+(?:high|low)"
            r"|all[\s-]*time\s+(?:high|low)"
            r"|best\s+(?:performing|stock)"
            r"|worst\s+(?:performing|stock)"
            r"|market\s+(?:movers?|leaders?)"
            r"|nifty\s+(?:top|best|worst))\b",
            text,
        ):
            if "get_market_news" in tool_names:
                return OrchestrationDecision(
                    "get_market_news",
                    {
                        "query": _market_query_from_text(message),
                        "symbol": None,
                    },
                )

        if "sector" in text and any(
            word in text
            for word in (
                "performance",
                "outlook",
                "trend",
                "doing",
                "bullish",
                "bearish",
                "analysis",
                "rotation",
                "best",
                "worst",
                "how",
            )
        ):
            if "get_market_news" in tool_names:
                return OrchestrationDecision(
                    "get_market_news",
                    {
                        "query": _market_query_from_text(message),
                        "symbol": None,
                    },
                )

        if re.search(
            r"\bmy\s+(?:p&?l|pnl|performance|profit|loss|return|position|holding|"
            r"portfolio|account|fund|balance|margin)\b",
            text,
        ):
            if "get_openalgo_snapshot" in tool_names:
                snapshot_type = "positionbook"
                if "holding" in text:
                    snapshot_type = "holdings"
                if any(
                    word in text
                    for word in ("fund", "balance", "margin", "cash")
                ):
                    snapshot_type = "funds"
                if any(
                    word in text
                    for word in ("order", "pending")
                ):
                    snapshot_type = "orderbook"
                if any(
                    word in text
                    for word in ("trade", "fill", "executed")
                ):
                    snapshot_type = "tradebook"
                return OrchestrationDecision(
                    "get_openalgo_snapshot",
                    {"snapshot_type": snapshot_type},
                )

        comparison_match = re.search(
            r"\b(\w+)\s+(?:vs\.?|versus|compared?\s+(?:to|with))\s+(\w+)\b",
            text,
        )
        if comparison_match and "get_market_quote" in tool_names:
            sym_a = comparison_match.group(1).upper()
            sym_b = comparison_match.group(2).upper()
            exchange = _exchange_from_text(message, default="NSE")
            return OrchestrationDecision(
                tool_name="get_market_quote",
                arguments={"query": sym_a, "exchange": exchange},
                tool_calls=[
                    ToolInvocation(
                        "get_market_quote",
                        {"query": sym_a, "exchange": exchange},
                    ),
                    ToolInvocation(
                        "get_market_quote",
                        {"query": sym_b, "exchange": exchange},
                    ),
                ],
            )

        return OrchestrationDecision(
            tool_name=None,
            arguments={},
            direct_response=_closest_action_response(text),
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
    groq_fallback_model: str | None = "llama-3.1-8b-instant",
    require_real_llm: bool = False,
) -> Orchestrator:
    normalized_provider = provider.strip().lower()
    if normalized_provider == "groq":
        active_key = groq_api_key or api_key
        if active_key:
            return GroqToolOrchestrator(
                active_key,
                groq_model,
                groq_fallback_model,
            )
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
        for tool in registry.openai_tools(strict=False)
    ]
