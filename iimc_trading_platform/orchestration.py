from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import get_close_matches
from typing import Any, Protocol

from .services.instrument_names import company_name as _company_name
from .tools.registry import ToolRegistry


logger = logging.getLogger(__name__)

_EDUCATION_PREFIX_RE = re.compile(
    r"\s*(?:what\s+(?:is|are|does)|explain|define|meaning\s+of"
    r"|tell\s+me\s+about|how\s+does|describe|teach\s+me)\b"
)


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


def _pending_order_summary(approval: dict[str, Any]) -> str:
    """Plain one-line summary of a pending order, e.g. 'BUY 10 RELIANCE'."""
    side = approval.get("side")
    quantity = approval.get("quantity")
    symbol = approval.get("symbol")
    if side and quantity and symbol:
        order_type = str(approval.get("order_type") or "MARKET").lower()
        return f"{side} {quantity} {symbol} ({order_type})"
    action = str(approval.get("requested_action") or "order").replace("_", " ")
    return action


def grounded_tool_response(tool_name: str, result: dict[str, Any]) -> str:
    return _grounded_fallback_response(tool_name, result)


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
                r"|analy[sz]e|analysis\s+of|overview\s+of|study|look\s+into)\b",
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


_ROUTER_SYSTEM_PROMPT = (
    "You are the orchestration layer for an audited trading platform. Select "
    "only registered tools. Never invent dataset IDs, run IDs, prices, P&L, "
    "risk decisions, order IDs, broker state, news, or market data. Prefer "
    "research/backtest/read-only tools unless the user explicitly asks for "
    "paper or live execution. Live execution must remain guarded by backend "
    "configuration and approval checks. For compound questions, select at "
    "most four read-only tools. Never combine a state-changing tool with "
    "another tool.\n\n"
    "Tool routing guide:\n"
    "- Price/quote questions → get_market_quote\n"
    "- Market news, sector outlook, top gainers/losers → get_market_news\n"
    "- 'What is RSI/MACD/EMA', education → search_knowledge\n"
    "- 'Create/build/make a strategy' → compile_custom_strategy_spec\n"
    "- 'Backtest EMA crossover' → run_backtest\n"
    "- 'My positions/funds/orders' → get_openalgo_snapshot\n"
    "- 'Warren Buffett/conservative' → get_strategy_persona\n"
    "- 'Compare run_X and run_Y' → compare_runs\n"
    "- 'Paper trade/readiness' → get_execution_readiness\n"
    "- 'Import data for SYMBOL' → import_openalgo_history\n"
    "- Symbol comparison ('X vs Y') → two get_market_quote calls\n"
    "- Platform capabilities → get_platform_summary\n"
    "- Dataset listing/info → list_datasets or get_dataset_detail\n"
    "- Approval/risk → list_pending_approvals or get_risk_decisions\n"
    "- Portfolio → get_portfolio_snapshot\n"
    "- OpenAlgo status → get_openalgo_monitor\n\n"
    "You help with trading, markets, investing, finance, economics, "
    "companies, and financial history — including educational, conceptual, "
    "historical, and 'who/which/compare' questions (for example famous "
    "investors, what value investing is, or what caused the 2008 crisis). "
    "When no tool fits such a question, do NOT select a tool and do NOT "
    "refuse — just answer it directly and helpfully. Do not call account, "
    "quote, or broker tools for purely conceptual or comparison questions "
    "('what is X', 'difference between X and Y'). Only decline requests that "
    "are clearly unrelated to finance and markets (weather, sports, cooking, "
    "entertainment, personal or coding help), in one short sentence."
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
        for tool in registry.openai_tools(strict=False)
    ]


def _is_groq_routing_failure(exc: Exception) -> bool:
    """Identify provider failures for which local routing is safe."""
    message = str(exc).lower()
    return "tool_use_failed" in message or "rate_limit_exceeded" in message


def _is_groq_rate_limited(exc: Exception) -> bool:
    return "rate_limit_exceeded" in str(exc).lower()


_INTENT_TERMS = frozenset(
    {
        "account",
        "balance",
        "backtest",
        "cash",
        "current",
        "dataset",
        "funds",
        "headline",
        "latest",
        "margin",
        "market",
        "monitor",
        "news",
        "openalgo",
        "order",
        "orders",
        "outlook",
        "paper",
        "performance",
        "position",
        "positions",
        "price",
        "quote",
        "quotes",
        "research",
        "risk",
        "scenario",
        "share",
        "status",
        "stock",
        "strategy",
        "trade",
        "trades",
        "trading",
    }
)


def _normalize_intent_text(message: str) -> str:
    """Correct only close matches to known intent words, never entities."""
    normalized = message.lower()
    aliases = {
        "ltp": "price",
        "quotation": "quote",
        "rate": "price",
    }

    def normalize(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in aliases:
            return aliases[token]
        if len(token) < 4 or token in _INTENT_TERMS:
            return token
        close = get_close_matches(token, _INTENT_TERMS, n=1, cutoff=0.8)
        return close[0] if close else token

    return re.sub(r"[a-z]+", normalize, normalized)


def _contains_any_word(text: str, words: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE)
        for word in words
    )


def _openalgo_snapshot_types(text: str) -> list[str]:
    categories = (
        ("holdings", ("holding", "holdings")),
        ("positionbook", ("position", "positions")),
        ("orderbook", ("orderbook", "open order", "orders", "order")),
        ("tradebook", ("tradebook", "trade book", "trades", "fills", "my trade")),
        (
            "funds",
            (
                "funds",
                "cash",
                "balance",
                "collateral",
                "margin",
                "available cash",
            ),
        ),
    )
    return [
        snapshot_type
        for snapshot_type, phrases in categories
        if any(phrase in text for phrase in phrases)
    ]


def _is_sandbox_intent_request(text: str) -> bool:
    return (
        any(word in text for word in ("prepare", "create", "draft"))
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
    )


def _parse_technical_screen(text: str) -> dict[str, Any] | None:
    """Parse a technical screen request into run_technical_screen arguments.

    Handles both orderings, e.g. 'stocks where RSI is below 30' and
    'stocks trading below their 50-day EMA'. Defaults to scanning the
    NIFTY 50 so no watchlist is required; 'watchlist' opts into that instead.
    """
    if not re.search(
        r"\b(?:stocks?|symbols?|watch\s*list|screen|scan|nifty)\b", text
    ):
        return None
    if not re.search(r"\b(rsi|ema|sma|volume)\b", text):
        return None

    args: dict[str, Any] = {}
    number_match = re.search(r"(\d+(?:\.\d+)?)", text)
    number = float(number_match.group(1)) if number_match else None
    # The threshold is the number stated after the comparator, so
    # "14-day RSI below 30" reads 30, not the period 14.
    after_comparator = re.search(
        r"\b(?:below|above|over|under|than|higher|lower)\s+(\d+(?:\.\d+)?)",
        text,
    )
    stated = float(after_comparator.group(1)) if after_comparator else None

    if re.search(r"\brsi\b", text):
        above = bool(
            re.search(r"\b(above|over|greater|more\s+than|higher)\b", text)
        )
        args["condition"] = "rsi_above" if above else "rsi_below"
        args["threshold"] = stated if stated is not None else (
            70.0 if above else 30.0
        )
    elif re.search(r"\b(ema|sma|moving\s+average)\b", text):
        below = bool(re.search(r"\b(below|under|less\s+than|lower)\b", text))
        args["condition"] = "price_below_ema" if below else "price_above_ema"
        args["threshold"] = 1.0
        # A period like "50-day" / "50 day" / "50 ema" sizes the average.
        period_match = re.search(
            r"(\d+)\s*(?:-?\s*day|-?\s*period|\s+(?:ema|sma))", text
        )
        if period_match:
            args["period"] = max(2, min(200, int(period_match.group(1))))
    elif re.search(r"\bvolume\b", text):
        args["condition"] = "volume_spike"
        args["threshold"] = stated if stated is not None else (
            number if number is not None else 2.0
        )
    else:
        return None

    args["universe"] = None if re.search(r"\bwatch\s*list\b", text) else "nifty50"
    return args


def _parse_direct_order(message: str, text: str) -> dict[str, Any] | None:
    """Parse a discretionary order like 'buy 10 RELIANCE' or 'sell 5 TCS'.

    Requires an explicit quantity and symbol so nothing is assumed. A risk
    decision id present means the user wants the strategy path, not a
    direct order.
    """
    if _extract_identifier(message, "risk_"):
        return None
    match = re.search(
        r"\b(buy|sell)\s+(\d+)\s+([A-Za-z][\w&-]{1,29})\b",
        message,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    side = match.group(1).upper()
    quantity = int(match.group(2))
    symbol = match.group(3).upper()
    if symbol in {"SHARE", "SHARES", "LOT", "LOTS", "QTY", "UNITS"}:
        return None
    if quantity < 1 or quantity > 100_000:
        return None
    order = {"symbol": symbol, "quantity": quantity, "side": side}
    price_match = re.search(
        r"\b(?:at|@)\s*(?:limit\s+)?(?:rs\.?\s*|₹\s*)?(\d+(?:\.\d+)?)",
        message,
        flags=re.IGNORECASE,
    )
    if price_match and "limit" in text:
        order["order_type"] = "LIMIT"
        order["limit_price"] = float(price_match.group(1))
    if "cnc" in text or "delivery" in text:
        order["product"] = "CNC"
    exchange = _exchange_from_text(message, default="NSE")
    if exchange:
        order["exchange"] = exchange
    return order


def _is_paper_trade_workflow_request(text: str) -> bool:
    if not any(phrase in text for phrase in ("paper trade", "paper trading")):
        return False
    if _is_sandbox_intent_request(text):
        return False
    return not any(
        phrase in text
        for phrase in (
            "show paper",
            "list paper",
            "paper intent",
            "paper order status",
            "openalgo intent",
        )
    )


def _is_history_import_request(text: str) -> bool:
    return (
        any(word in text for word in ("import", "fetch", "download", "ingest", "load"))
        and any(
            phrase in text
            for phrase in (
                "historical data",
                "history data",
                "price history",
                "ohlcv",
                "candles",
                "history for",
            )
        )
        and "order history" not in text
    )


def _references_unspecified_personal_strategy(text: str) -> bool:
    if not re.search(r"\b(?:my|this|the)\s+strategy\b", text):
        return False
    return not any(
        word in text
        for word in (
            "ema", "sma", "rsi", "momentum", "roc", "macd",
            "bollinger", "vwap", "plugin", "custom_", "strategy:",
            "strategy=", "strategy named",
        )
    )


_EDUCATION_MAP: dict[str, str] = {
    "rsi": (
        "**RSI (Relative Strength Index)** measures the speed and magnitude "
        "of recent price changes on a 0-100 scale. Traditionally, RSI > 70 "
        "signals overbought conditions (potential sell), RSI < 30 signals "
        "oversold (potential buy). The default period is 14. You can create "
        "a strategy using RSI — try: 'Create a strategy that buys when RSI "
        "is below 30 and exits when RSI is above 70'."
    ),
    "ema": (
        "**EMA (Exponential Moving Average)** gives more weight to recent "
        "prices, making it faster to react than SMA. Traders often compare "
        "a fast EMA (9 or 12) with a slow EMA (21 or 26) — a crossover "
        "above signals bullish momentum, below signals bearish. You can "
        "create EMA crossover strategies in plain language here."
    ),
    "sma": (
        "**SMA (Simple Moving Average)** is the arithmetic mean of prices "
        "over a period. Common periods: 20 (short-term), 50 (medium), 200 "
        "(long-term). The 50/200 crossover is the classic 'golden cross' "
        "(bullish) or 'death cross' (bearish)."
    ),
    "macd": (
        "**MACD (Moving Average Convergence Divergence)** is the difference "
        "between 12-period and 26-period EMA. The signal line is a 9-period "
        "EMA of MACD. Buy when MACD crosses above the signal line, sell "
        "when it crosses below. You can create MACD strategies here."
    ),
    "bollinger": (
        "**Bollinger Bands** are a volatility envelope: a 20-period SMA "
        "(middle band) with upper/lower bands at +/- 2 standard deviations. "
        "Price touching the upper band may signal overbought; the lower "
        "band, oversold. Bandwidth contracting signals a potential breakout."
    ),
    "vwap": (
        "**VWAP (Volume Weighted Average Price)** is the average price "
        "weighted by volume, resetting daily. Institutional traders use it "
        "as a benchmark — buying below VWAP and selling above. It's most "
        "useful for intraday trading."
    ),
    "atr": (
        "**ATR (Average True Range)** measures volatility — the average of "
        "true ranges (max of high-low, |high-prev_close|, |low-prev_close|) "
        "over a period (default 14). It's used for position sizing and "
        "setting stop losses proportional to volatility."
    ),
    "stop loss": (
        "**Stop loss** is a risk management order that closes a position "
        "when price moves against you by a specified amount. Types: fixed "
        "percentage (e.g., 2%), ATR-based (e.g., 1.5x ATR), trailing "
        "(follows price by a fixed distance). All are supported in the "
        "strategy compiler."
    ),
    "pe ratio": (
        "**PE Ratio (Price-to-Earnings)** is the stock price divided by "
        "earnings per share. A high PE (>25) may indicate overvaluation or "
        "growth expectations; a low PE (<15) may suggest undervaluation or "
        "declining earnings. Compare PE within the same sector for context."
    ),
    "market cap": (
        "**Market Capitalization** is the total market value of a company's "
        "outstanding shares (price x shares). Categories: Large-cap (>20K Cr), "
        "Mid-cap (5K-20K Cr), Small-cap (<5K Cr). It indicates company size "
        "and liquidity."
    ),
    "option": (
        "**Options** are contracts giving the right (not obligation) to buy "
        "(call) or sell (put) an asset at a strike price before expiry. Key "
        "concepts: premium, strike price, expiry, intrinsic/extrinsic value, "
        "and Greeks (Delta, Gamma, Theta, Vega). This platform supports "
        "options data import and rule-spec strategies on options OHLCV."
    ),
    "straddle": (
        "**Straddle** is an options strategy: buy both a call and put at "
        "the same strike and expiry. Profits from large price movement in "
        "either direction. Maximum loss is the total premium paid. Used "
        "before events like earnings or budget."
    ),
    "strangle": (
        "**Strangle** is similar to a straddle but uses different strikes — "
        "buy an OTM call and OTM put. Cheaper premium but needs a larger "
        "move to profit. Also used for volatility plays."
    ),
    "iron condor": (
        "**Iron Condor** combines a bull put spread and bear call spread. "
        "Profits when price stays within a range. Limited risk and reward. "
        "Used in low-volatility, range-bound markets."
    ),
    "candlestick": (
        "**Candlestick charts** show open, high, low, close prices per "
        "period. Green/white candles mean close > open (bullish); red/black "
        "mean close < open (bearish). Patterns like doji, hammer, engulfing, "
        "and morning star signal potential reversals."
    ),
    "support resistance": (
        "**Support** is a price level where buying interest prevents further "
        "decline. **Resistance** is where selling pressure prevents further "
        "rise. When support breaks, it often becomes resistance and vice "
        "versa. These levels come from historical price action."
    ),
    "intraday": (
        "**Intraday trading** means opening and closing positions within "
        "the same trading day. Common strategies use 1m, 5m, or 15m "
        "timeframes with indicators like VWAP, EMA crossovers, and RSI. "
        "This platform supports intraday backtesting and paper trading."
    ),
    "swing trading": (
        "**Swing trading** holds positions for days to weeks, capturing "
        "medium-term price moves. Uses daily or 4h timeframes with "
        "indicators like EMA, RSI, and MACD. Less time-intensive than "
        "intraday but requires overnight risk management."
    ),
    "value investing": (
        "**Value investing** (popularized by Warren Buffett and Benjamin "
        "Graham) focuses on buying undervalued stocks based on "
        "fundamentals — low PE, high ROE, strong moat, margin of safety. "
        "This platform has a 'conservative_value' persona for value-oriented "
        "research."
    ),
    "momentum": (
        "**Momentum trading** buys assets that are rising and sells those "
        "falling, based on the tendency for trends to persist. Common "
        "indicators: ROC, RSI, MACD. This platform has built-in momentum "
        "strategies and an 'intraday_momentum' persona."
    ),
    "backtesting": (
        "**Backtesting** applies a trading strategy to historical data to "
        "see how it would have performed. Key metrics: total return, max "
        "drawdown, Sharpe ratio, win rate. This platform runs deterministic "
        "backtests on governed data with full audit trails."
    ),
    "sharpe ratio": (
        "**Sharpe Ratio** measures risk-adjusted returns: (return - risk-free "
        "rate) / standard deviation. A ratio > 1 is good, > 2 is very good, "
        "> 3 is excellent. It penalizes strategies with high volatility."
    ),
    "drawdown": (
        "**Drawdown** is the peak-to-trough decline in portfolio value, "
        "expressed as a percentage. Maximum drawdown measures the worst "
        "historical loss. It's critical for evaluating risk tolerance — "
        "a 50% drawdown needs 100% gain to recover."
    ),
}


def _education_lookup(concept: str) -> str | None:
    concept_lower = concept.lower().strip()
    # Whole-word match, longest key first, so "rsi" never matches inside
    # "dive(rsi)fication" and "moving average" beats "average".
    for key in sorted(_EDUCATION_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", concept_lower):
            return _EDUCATION_MAP[key]
    return None


def _educational_response(concept: str) -> str:
    known = _education_lookup(concept)
    if known:
        return known
    return (
        f"I don't have a built-in explanation for '{concept}', but you can "
        f"try: 'search knowledge {concept}' to check your uploaded "
        f"documents, or ask about specific indicators (RSI, EMA, MACD, "
        f"Bollinger, VWAP, ATR), strategies (intraday, swing, momentum, "
        f"value investing), or concepts (stop loss, PE ratio, Sharpe ratio, "
        f"drawdown, options, candlestick patterns)."
    )


_FINANCE_ACRONYMS = {
    "ETF", "ETFS", "IPO", "SIP", "STP", "SWP", "NAV", "GDP", "PE", "EPS",
    "ROE", "ROA", "ROI", "RSI", "EMA", "SMA", "MACD", "ATR", "VWAP", "IV",
    "OI", "REIT", "REITS", "CAGR", "XIRR", "TER", "ELSS", "ULIP", "AMC",
    "NFO", "FD", "RD", "PPF", "NPS", "EBITDA", "PAT", "PBT", "DCF", "PB",
    "US", "UK", "AND", "OR", "VS", "ETC", "FAQ", "CEO", "CFO", "IT",
}


_DOMAIN_TERMS = (
    "trade", "trading", "trader", "stock", "share", "equity", "market",
    "invest", "portfolio", "strategy", "strateg", "backtest", "quote",
    "price", "ltp", "candle", "ohlcv", "dataset", "broker", "openalgo",
    "order", "position", "fund", "margin", "option", "future", "commodity",
    "crypto", "nifty", "sensex", "nse", "bse", "mcx", "rsi", "ema", "sma",
    "macd", "bollinger", "vwap", "atr", "dividend", "earnings", "pe ratio",
    "ipo", "sector", "index", "bull", "bear", "volatility", "hedge",
    "derivative", "sip", "mutual fund", "etf", "forex", "currency",
    "persona", "risk", "approval", "paper", "sandbox", "analyzer",
    "signal", "indicator", "chart", "screener", "news", "sharpe",
    "drawdown", "pnl", "p&l", "profit", "loss", "finance", "financial",
    "filing", "annual report", "balance sheet", "economy", "economic",
    "econom", "recession", "inflation", "interest rate", "gdp", "valuation",
    "fundamental", "buffett", "compound", "wealth", "capital",
)

_OFF_TOPIC_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("weather", ("weather", "temperature outside", "rain today", "forecast for today", "sunny", "humidity")),
    ("creative writing", ("poem", "poetry", "story", "joke", "song", "lyrics", "rap", "haiku", "novel", "essay about")),
    ("homework", ("homework", "assignment", "math problem", "solve this equation", "calculus", "algebra", "physics problem", "chemistry")),
    ("cooking", ("recipe", "cook", "cooking", "bake", "baking", "ingredients for", "dinner ideas")),
    ("sports", ("cricket score", "football score", "match score", "ipl score", "world cup", "who won the match")),
    ("entertainment", ("movie", "movies", "tv show", "netflix", "series to watch", "celebrity", "actor", "actress")),
    ("travel", ("travel to", "vacation", "holiday destination", "flight to", "hotel in", "tourist")),
    ("health", ("medical advice", "doctor", "medicine for", "symptoms", "diagnosis", "headache", "fever")),
    ("general knowledge", ("prime minister", "president of", "capital of", "who is the king", "photosynthesis", "world war")),
    ("programming help", ("write code for", "debug my code", "python script for", "fix my program", "javascript function")),
)


def _off_topic_category(text: str) -> str | None:
    """Detect clearly non-finance requests without touching domain queries."""
    if any(term in text for term in _DOMAIN_TERMS):
        return None
    for category, phrases in _OFF_TOPIC_CATEGORIES:
        if any(phrase in text for phrase in phrases):
            return category
    return None


def _domain_refusal_response(category: str) -> str:
    return (
        f"I'm a trading and market-research assistant, so I can't help with "
        f"{category} questions. I can help with things like:\n"
        "- **Quotes and news**: 'price of Reliance', 'news for Tata Steel'\n"
        "- **Education**: 'what is RSI?', 'explain value investing'\n"
        "- **Strategies**: describe one in plain language and I'll compile "
        "and backtest it\n"
        "- **Your account**: 'show my positions', 'my fund balance'"
    )


_ADVICE_PATTERNS = (
    r"\bwhat\s+should\s+i\s+(?:buy|invest|trade|pick)",
    r"\bwhat\s+to\s+(?:buy|invest)",
    r"\bwhich\s+(?:stock|share)s?\s+should\s+i",
    r"\bwhat\s+(?:stock|share)s?\s+should\s+i",
    r"\bwhich\s+(?:stock|share)s?\s+to\s+(?:buy|invest|trade)",
    # "which stock today is best ...", "which share is the best"
    r"\bwhich\s+(?:stock|share)\b.{0,30}\bbest\b",
    r"\bbest\s+(?:stock|share)s?\s+to\s+(?:buy|invest|trade)",
    r"\bbest\s+(?:stock|share)s?\s+(?:to|for|right\s+now|today|now)\b",
    r"\brecommend\s+(?:a\s+|some\s+|me\s+)?(?:stock|share|trade)",
    r"\bgood\s+(?:stock|share)\s+to\s+(?:buy|invest)",
    r"\bstock\s+tip",
    r"\bmultibagger",
    r"\bbest\s+investment",
)


def _is_open_ended_advice(text: str, message: str) -> bool:
    """Detect vague 'what should I buy' asks with no concrete instrument.

    These are personalised-recommendation requests the platform cannot and
    should not answer blindly. If the user already named a ticker we let the
    normal tools handle it.
    """
    if not any(re.search(pattern, text) for pattern in _ADVICE_PATTERNS):
        return False
    # If they named a concrete uppercase ticker, treat it as a specific ask.
    if re.search(r"\b[A-Z]{3,}\b", message):
        return False
    return True


def _open_ended_advice_response() -> str:
    return (
        "I'm not a licensed financial adviser, so I can't tell you which "
        "stock to buy or pick one for you. But I can help you decide with "
        "real data — tell me what you're after and I'll run it:\n"
        "- **Screen the NIFTY 50**, e.g. 'find NIFTY 50 stocks where RSI is "
        "below 30' or 'NIFTY 50 stocks trading below their 50-day EMA'\n"
        "- **Analyse a specific company**, e.g. 'analyse RELIANCE "
        "fundamentally' or 'price and news for HDFCBANK'\n"
        "- **Backtest an idea**, e.g. 'backtest an EMA crossover on "
        "INFY'\n\n"
        "Which of these would you like, and on which stock or index?"
    )


def _closest_action_response(text: str) -> str:
    """Point an unrecognized request at the nearest supported workflow."""
    suggestions: list[str] = []
    if any(word in text for word in ("predict", "forecast", "will", "target price", "tomorrow")):
        suggestions.append(
            "I can't predict prices, but I can ground a view in evidence: "
            "ask for 'news for <symbol>' or 'research <symbol>' for stored "
            "catalysts, or backtest a rule-based idea on real history."
        )
    if any(word in text for word in ("buy", "sell", "invest", "should i")):
        suggestions.append(
            "I don't give personalized investment advice or place live "
            "orders. I can compile a strategy you describe, backtest it, and "
            "prepare a human-approved paper order."
        )
    if "strateg" in text:
        suggestions.append(
            "Describe a strategy in plain language, for example: 'Create a "
            "Reliance 5 minute strategy that buys when EMA 9 crosses above "
            "EMA 21 and exits when EMA 9 crosses below EMA 21 with a 2 "
            "percent stop loss'."
        )
    if any(word in text for word in ("data", "candle", "history", "import")):
        suggestions.append(
            "To bring in market data, say: 'import historical data for "
            "RELIANCE NSE 5m from 2026-06-01 to 2026-07-16'."
        )
    if any(word in text for word in ("option", "call", "put", "strike", "expiry")):
        suggestions.append(
            "For options, I can resolve option symbols ('option symbol for "
            "NIFTY 24000 CE'), import options OHLCV data, and derive "
            "IV/OI features. Describe what you need."
        )
    if any(word in text for word in ("risk", "exposure", "var", "drawdown")):
        suggestions.append(
            "I can show risk decisions for a specific backtest run "
            "('risk for run_abc123'), or check portfolio exposure. "
            "Run a backtest first to generate risk evidence."
        )
    if any(word in text for word in ("commodity", "gold", "silver", "crude", "mcx")):
        suggestions.append(
            "Commodity data flows through OpenAlgo on MCX exchange. Try: "
            "'import historical data for GOLD MCX 5m' or 'price of GOLD MCX'."
        )
    if any(word in text for word in ("crypto", "bitcoin", "btc", "ethereum")):
        suggestions.append(
            "Crypto support depends on your broker's API. If available "
            "through OpenAlgo, you can import and backtest crypto data "
            "the same way as equities."
        )
    if not suggestions:
        suggestions.append(
            "I can help with:\n"
            "- **Quotes**: 'price of Reliance'\n"
            "- **News**: 'news for Tata Steel' or 'market outlook'\n"
            "- **Education**: 'what is RSI?' or 'explain MACD'\n"
            "- **Strategy creation**: describe in plain language\n"
            "- **Backtesting**: 'backtest EMA crossover on RELIANCE'\n"
            "- **Account**: 'my positions' or 'my funds'\n"
            "- **Paper trading**: prepare and approve sandbox orders\n"
            "- **Comparison**: 'Reliance vs TCS'\n"
            "- **Sectors**: 'how is banking sector doing?'"
        )
    return " ".join(suggestions)


def _is_strategy_creation_request(text: str) -> bool:
    """Detect natural-language strategy creation, including rule phrasing."""
    if "strateg" not in text:
        return False
    has_rule_language = bool(
        re.search(
            r"\b(?:buys?|sells?|enters?|exits?|goes?\s+(?:long|short)|"
            r"longs?|shorts?|closes?)\s+(?:[\w&.-]+\s+){0,2}?"
            r"(?:when(?:ever)?|if|once)\b",
            text,
        )
    )
    if has_rule_language:
        return True
    if re.match(r"\s*(?:how|what|which|can|could|do|does|is|are)\b", text):
        return False
    return any(
        word in text
        for word in (
            "create",
            "build",
            "make",
            "draft",
            "design",
            "compile",
            "write",
            "generate",
            "set up",
            "i want",
            "i need",
            "give me",
        )
    )


def _is_market_price_request(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "price",
            "market price",
            "share price",
            "stock price",
            "trading price",
            "current price",
            "latest price",
            "live price",
            "quote",
            "quotes",
            "ltp",
            "what price",
            "what's price",
            "whats price",
        )
    )


def _is_market_outlook_request(text: str) -> bool:
    """Recognize forward-looking research requests before model routing."""
    return any(
        phrase in text
        for phrase in (
            "expected to rise",
            "expected to fall",
            "likely to rise",
            "likely to fall",
            "stocks to buy",
            "stocks to sell",
            "stock picks",
            "next week",
            "next month",
            "weekly outlook",
        )
    )


def _market_outlook_symbol(message: str) -> str | None:
    symbol = _symbol_from_text(message)
    if symbol in {
        "EXPECTED",
        "LIKELY",
        "MONTH",
        "NEXT",
        "RISE",
        "SHOULD",
        "STOCK",
        "STOCKS",
        "WEEK",
        "WHICH",
    }:
        return None
    return symbol


def _market_outlook_query(message: str, symbol: str | None) -> str:
    if symbol:
        return f"{symbol} stock outlook"
    # This query maps to Event Registry's India-market handling and avoids
    # turning a request for a forward prediction into a synthetic stock pick.
    return "NIFTY Indian stock market outlook"


def _is_market_quote_follow_up(
    text: str,
    history: list[dict[str, str]],
) -> bool:
    if not any(
        phrase in text
        for phrase in ("and ", "what about", "how about", "then ")
    ):
        return False
    previous = _last_user_message(history)
    return previous is not None and _is_market_price_request(
        _normalize_intent_text(previous)
    )


def _market_query_for_request(
    message: str,
    history: list[dict[str, str]],
) -> str:
    query = _market_query_from_text(message)
    if query not in {"it", "its", "that", "this"}:
        return query
    previous = _last_user_message(history)
    return _market_query_from_text(previous) if previous else query


def _last_user_message(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history):
        if item.get("role") == "user" and item.get("content"):
            return str(item["content"])
    return None


def _market_query_from_text(message: str) -> str:
    normalized_message = _normalize_intent_text(message)
    without_request_words = re.sub(
        r"\b(what|is|the|market|status|scenario|price|share|stock|current|"
        r"latest|live|quote|quotes|ltp|rate|of|for|please|tell|me|whats|what's|"
        r"and|about|how|then|news|headline|headlines|update|updates|outlook|"
        r"research|today|recent|recently|give|show|get|any|week|next|trend|"
        r"trending|happening|going|on)\b",
        " ",
        normalized_message,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\s+", " ", without_request_words).strip(" ?,.!:")
    # A bare "latest market news" leaves nothing — fall back to a clean,
    # relevant default rather than echoing the request words back.
    return query[:200] or "Indian stock market"


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
    explicit = re.search(
        r"\b(?:strategy(?:[_\s-]*name)?|plugin)\s*(?:[:=]|named)?\s*"
        r"([a-z][a-z0-9_.-]{1,100})\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        candidate = explicit.group(1).lower()
        if candidate not in {"backtest", "run", "test", "strategy"}:
            return candidate
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
    parameter_block = re.search(
        r"\bparameters?\s*[:=]?\s*(\{.*\})",
        text,
        flags=re.IGNORECASE,
    )
    if parameter_block:
        try:
            parsed = json.loads(parameter_block.group(1))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
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


def _symbol_from_text(text: str) -> str | None:
    upper = text.upper()
    excluded = {
        # Short English stopwords that otherwise get grabbed as tickers,
        # e.g. "backtest AN EMA crossover ON WIPRO".
        "A", "AN", "AS", "AT", "BE", "BY", "DO", "IF", "IN", "IS", "IT",
        "OF", "ON", "OR", "SO", "TO", "UP", "WE", "AM", "US",
        # Question / auxiliary / common function words. The whole message is
        # upper-cased before matching, so every ordinary word looks like a
        # ticker — these must be filtered or "what is the market outlook" picks
        # up "WHAT" as the symbol.
        "WHAT", "WHATS", "WHEN", "WHENS", "WHERE", "WHY", "HOW", "HOWS",
        "WHO", "WHOM", "WHOSE", "WHICH", "WILL", "SHALL", "SHOULD", "COULD",
        "WOULD", "CAN", "CANT", "CANNOT", "DOES", "DID", "DOING", "ARE",
        "AINT", "WAS", "WERE", "HAS", "HAVE", "HAD", "THIS", "THAT", "THESE",
        "THOSE", "THERE", "THEIR", "THEY", "THEM", "HERE", "THEN", "THAN",
        "WITH", "FROM", "OVER", "UNDER", "WHILE", "ALSO", "JUST", "VERY",
        "MUCH", "MORE", "MOST", "SOME", "ALL", "BOTH", "EACH", "PLEASE",
        "THANKS", "YES", "NOW", "SOON", "YEAR", "MONTH", "DAY", "DAYS",
        "TIME", "GOING", "GONNA", "LIKE", "GOOD", "BAD", "OKAY",
        "ME", "YOU", "HIM", "HER", "HERS", "OUR", "OURS", "MINE", "ITS",
        "I", "HELLO", "HI", "HEY", "PLS",
        # Research/analysis verbs, e.g. "ANALYZE RELIANCE", "RESEARCH TCS".
        "ANALYZE", "ANALYSE", "ANALYSIS", "RESEARCH", "STUDY", "REVIEW",
        "SUMMARIZE", "SUMMARISE", "EXPLAIN", "TELL", "SHOW", "DEEP", "DIVE",
        "FULL", "OVERVIEW", "BRIEFING", "LOOK", "INTO", "STORY",
        "ABOUT",
        "AND",
        "ANY",
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
        "GET",
        "GIVE",
        "HAPPENING",
        "HEADLINE",
        "HEADLINES",
        "IV",
        "LATEST",
        "LIMIT",
        "LIVE",
        "MARKET",
        "MIS",
        "MY",
        "NEWS",
        "NEXT",
        "NFO",
        "NRML",
        "NSE",
        "OHLCV",
        "OPTION",
        "OPTIONS",
        "ORDER",
        "OUTLOOK",
        "PAPER",
        "PE",
        "PREPARE",
        "PUT",
        "RECENT",
        "RESEARCH",
        "RISK",
        "ROC",
        "RSI",
        "SCENARIO",
        "SELL",
        "SHOW",
        "SL",
        "SMA",
        "SPEC",
        "STRATEGY",
        "THE",
        "TODAY",
        "TREND",
        "TRENDING",
        "UPDATE",
        "UPDATES",
        "VIEW",
        "WANT",
        "WEEK",
        "YOUR",
    }
    for pattern in (
        r"\b(?:symbol|ticker|underlying)\s*[:=]?\s*([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
        r"\b(?:for|on|trade|backtest)\s+([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
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
    for exchange in ("NFO", "BFO", "NSE", "BSE", "MCX", "CDS", "BCD"):
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
    if re.search(r"\b(?:crypto|coin|bitcoin|btc|eth)\b", text):
        return "crypto"
    if re.search(r"\b(?:commodity|gold|silver|crude|mcx)\b", text):
        return "commodity"
    if re.search(r"\bfuture(?:s)?\b", text):
        return "futures"
    if re.search(r"\b(?:option|options|ce|pe|call|put)\b", text):
        return "options"
    if re.search(r"\bindex\b", text):
        return "index"
    return "equity"


def _default_exchange_for_asset(asset_class: str) -> str:
    return {
        "commodity": "MCX",
        "crypto": "CRYPTO",
        "futures": "NFO",
        "options": "NFO",
        "index": "NSE",
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


def _pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _name_suffix(symbol: Any, row: dict[str, Any]) -> str:
    """' (Reliance Industries)' for a ticker, or '' when unknown."""
    name = row.get("company_name") or _company_name(
        str(symbol) if symbol else None,
        row.get("exchange") or "NSE",
    )
    return f" ({name})" if name else ""


def _render_optimization_result(result: dict[str, Any]) -> str:
    """Deterministic leaderboard for the strategy-optimizer agent."""
    strategy = str(result.get("strategy", "")).replace("_", " ")
    rows = [r for r in result.get("results", []) if r.get("return_pct") is not None]
    best = result.get("best")
    if not rows:
        errs = [r.get("error") for r in result.get("results", []) if r.get("error")]
        reason = errs[0] if errs else "no usable backtests"
        return (
            f"I couldn't optimise the {strategy} strategy — {reason}."
        )
    lines = [
        f"I backtested {result.get('candidates_tried', 0)} {strategy} "
        f"configurations over your stored history. Ranked by return:",
        "",
    ]
    for row in rows[:8]:
        params = row.get("parameters", {})
        param_str = ", ".join(f"{k}={v}" for k, v in params.items())
        flag = "" if row.get("reliable") else " _(too few trades)_"
        lines.append(
            f"- **{row.get('return_pct')}%** · P&L ₹{row.get('net_pnl')} · "
            f"drawdown ₹{row.get('max_drawdown')} · "
            f"{row.get('total_trades')} trades — {param_str}{flag}"
        )
    if best:
        bp = ", ".join(f"{k}={v}" for k, v in best.get("parameters", {}).items())
        lines.append("")
        note = (
            " (best available, but it had few trades — treat as weak evidence)"
            if result.get("used_unreliable_best")
            else ""
        )
        lines.append(
            f"**Best configuration{note}:** {bp} → {best.get('return_pct')}% "
            f"return, {best.get('total_trades')} trades."
        )
    lines.append("")
    lines.append(
        "_Historical backtest results only — past performance doesn't predict "
        "future returns, and this isn't investment advice. Ask me to save the "
        "best one as a strategy to trade it (with your approval)._"
    )
    return "\n".join(lines)


def _render_remember_result(result: dict[str, Any]) -> str:
    content = result.get("content", "")
    return (
        f"Got it — I'll remember that: “{content}”.\n\n"
        "Ask “what do you remember” any time to see everything on file."
    )


def _render_recall_result(result: dict[str, Any]) -> str:
    notes = result.get("notes", [])
    research = result.get("research")
    lines: list[str] = []
    if notes:
        lines.append("**What I remember:**")
        for note in notes:
            when = _short_date(note.get("created_at"))
            suffix = f" — noted {when}" if when else ""
            lines.append(f"- {note.get('content')}{suffix}")
    else:
        lines.append(
            "I don't have any saved notes yet. Tell me “remember that ...” "
            "and I'll keep it."
        )
    if research:
        when = _short_date(research.get("updated_at"))
        lines.append("")
        lines.append(
            f"**Last research on {research.get('symbol')}"
            f"{f' ({when})' if when else ''}:** {research.get('content')}"
        )
    return "\n".join(lines)


def _short_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def _render_research_briefing(result: dict[str, Any]) -> str:
    """Deterministic multi-analyst briefing from gathered findings.

    Used when no LLM is configured; otherwise the LLM composes a thesis from
    the same structured findings. Only reports real data — unavailable
    specialists are stated plainly.
    """
    symbol = result.get("symbol", "?")
    name = result.get("company_name", symbol)
    lines = [f"# {name} ({symbol}) — research briefing", ""]

    valuation = result.get("valuation", {})
    if valuation.get("available"):
        ltp = valuation.get("ltp")
        rng = []
        if valuation.get("open") is not None:
            rng.append(f"open ₹{valuation['open']}")
        if valuation.get("high") is not None and valuation.get("low") is not None:
            rng.append(f"day ₹{valuation['low']}–₹{valuation['high']}")
        lines.append(
            f"**Price:** ₹{ltp}" + (f" · {' · '.join(rng)}" if rng else "")
        )
    else:
        lines.append(
            f"**Price:** live quote unavailable ({valuation.get('reason', 'n/a')})."
        )

    technicals = result.get("technicals", {})
    if technicals.get("available"):
        lines.append(
            f"**Technicals:** {technicals.get('trend')} · RSI "
            f"{technicals.get('rsi')} ({technicals.get('momentum')}) · "
            f"EMA20 ₹{technicals.get('ema20')} vs EMA50 ₹{technicals.get('ema50')}."
        )
    else:
        lines.append(
            f"**Technicals:** unavailable ({technicals.get('reason', 'n/a')})."
        )

    fundamentals = result.get("fundamentals", {})
    if fundamentals.get("available"):
        ratios = fundamentals.get("ratios", {})
        shown = ", ".join(
            f"{key.replace('_', ' ')} {value}"
            for key, value in list(ratios.items())[:6]
        )
        period = fundamentals.get("period")
        lines.append(
            f"**Fundamentals**"
            + (f" ({period})" if period else "")
            + f": {shown}."
        )
    else:
        lines.append(
            "**Fundamentals:** none imported yet — add statements in the Data "
            "tab to include them."
        )

    news = result.get("news", {})
    if news.get("available"):
        lines.append("**Recent news:**")
        for headline in news.get("headlines", [])[:5]:
            source = headline.get("source") or "unknown"
            lines.append(f"- {headline.get('title')} ({source})")
    else:
        lines.append(
            f"**Recent news:** unavailable ({news.get('reason', 'n/a')})."
        )

    gaps = result.get("gaps", [])
    if gaps:
        lines.append("")
        lines.append(
            "_This briefing summarises the data available now; missing pieces: "
            + "; ".join(gap.split(":")[0] for gap in gaps)
            + "._"
        )
    lines.append("")
    lines.append(
        "_Balanced summary of real data, not investment advice._"
    )
    return "\n".join(lines)


def _render_research_report(result: dict[str, Any]) -> str:
    """Deterministic cited report from the iterative deep-research loop.

    Reuses the briefing for the core findings, then shows the agent's own
    coverage self-critique, any deepening reading it pulled, and a sources list
    so every claim is traceable. LLM composition (when configured) works from
    the same structured bundle; nothing here is fabricated.
    """
    findings = result.get("findings", {})
    briefing = _render_research_briefing(
        {
            "symbol": result.get("symbol"),
            "company_name": result.get("company_name"),
            **findings,
            "gaps": result.get("gaps", []),
        }
    )
    # Retitle from "briefing" to "research report".
    briefing = briefing.replace("— research briefing", "— research report", 1)
    lines = [briefing, ""]

    critique = result.get("self_critique", [])
    if critique:
        lines.append(
            f"**How I researched this** ({result.get('iterations', 1)} pass"
            f"{'es' if result.get('iterations', 1) != 1 else ''}):"
        )
        for note in critique:
            lines.append(f"- {note}")
        lines.append("")

    web = result.get("web_research", [])
    if web:
        lines.append("**Further reading pulled in:**")
        for doc in web:
            title = doc.get("title") or "web document"
            url = doc.get("url")
            lines.append(f"- {title}" + (f" — {url}" if url else ""))
        lines.append("")

    citations = result.get("citations", [])
    if citations:
        lines.append("**Sources:**")
        for cite in citations:
            ref = cite.get("ref")
            url = cite.get("url")
            tail = url or ref
            lines.append(f"- {cite.get('source')}" + (f" ({tail})" if tail else ""))
    return "\n".join(lines).rstrip()


def _render_account_snapshot(result: dict[str, Any]) -> str:
    snapshot_type = result.get("snapshot_type", "account")
    data = result.get("data")

    if snapshot_type == "funds" and isinstance(data, dict):
        cash = _num(_pick(data, "availablecash", "available_cash", "cash"))
        unrealized = _num(_pick(data, "m2munrealized", "unrealized_pnl"))
        realized = _num(_pick(data, "m2mrealized", "realized_pnl"))
        used = _num(_pick(data, "utiliseddebits", "utilised_margin", "used_margin"))
        lines = []
        if cash is not None:
            lines.append(f"- **Available cash**: ₹{cash:,.2f}")
        if used is not None:
            lines.append(f"- **Used margin**: ₹{used:,.2f}")
        if unrealized is not None:
            lines.append(f"- **Unrealized P&L**: ₹{unrealized:,.2f}")
        if realized is not None:
            lines.append(f"- **Realized P&L**: ₹{realized:,.2f}")
        return "**Your account**\n" + ("\n".join(lines) or "- No balance details returned.")

    rows = data if isinstance(data, list) else []

    if snapshot_type in {"positionbook", "holdings"}:
        if not rows:
            return "You have no open positions right now."
        lines = []
        total_pnl = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = _pick(row, "symbol", "tradingsymbol", "tsym")
            qty = _num(_pick(row, "quantity", "netqty", "qty", "netQuantity"))
            avg = _num(_pick(row, "average_price", "averageprice", "avgprice", "buyavgprice"))
            ltp = _num(_pick(row, "ltp", "lastprice", "last_price"))
            pnl = _num(_pick(row, "pnl", "unrealized_pnl", "m2m", "profitandloss"))
            if pnl is None and None not in (qty, avg, ltp):
                pnl = (ltp - avg) * qty
            if pnl is not None:
                total_pnl += pnl
            parts = [f"**{sym}**{_name_suffix(sym, row)}"]
            if qty is not None:
                parts.append(f"qty {qty:g}")
            if avg is not None:
                parts.append(f"avg ₹{avg:,.2f}")
            if ltp is not None:
                parts.append(f"LTP ₹{ltp:,.2f}")
            if pnl is not None:
                parts.append(f"P&L ₹{pnl:,.2f}")
            lines.append("- " + " · ".join(parts))
        header = "**Your holdings**" if snapshot_type == "holdings" else "**Your open positions**"
        return (
            f"{header}\n" + "\n".join(lines)
            + f"\n\n**Total P&L: ₹{total_pnl:,.2f}**"
        )

    if snapshot_type == "orderbook":
        if not rows:
            return "You have no orders today."
        lines = []
        for row in rows[:15]:
            if not isinstance(row, dict):
                continue
            sym = _pick(row, "symbol", "tradingsymbol", "tsym")
            side = _pick(row, "action", "transaction_type", "side")
            qty = _num(_pick(row, "quantity", "qty"))
            price = _num(_pick(row, "price", "average_price", "averageprice"))
            status = _pick(row, "order_status", "status", "orderstatus")
            parts = [f"**{sym}**{_name_suffix(sym, row)}", str(side or "")]
            if qty is not None:
                parts.append(f"qty {qty:g}")
            if price is not None:
                parts.append(f"₹{price:,.2f}")
            if status:
                parts.append(str(status))
            lines.append("- " + " · ".join(p for p in parts if p))
        return "**Your orders**\n" + "\n".join(lines)

    if snapshot_type == "tradebook":
        if not rows:
            return "You have no trades today."
        lines = []
        for row in rows[:15]:
            if not isinstance(row, dict):
                continue
            sym = _pick(row, "symbol", "tradingsymbol", "tsym")
            side = _pick(row, "action", "transaction_type", "side")
            qty = _num(_pick(row, "quantity", "qty", "fillsize"))
            price = _num(_pick(row, "average_price", "averageprice", "price", "fillprice"))
            parts = [f"**{sym}**{_name_suffix(sym, row)}", str(side or "")]
            if qty is not None:
                parts.append(f"qty {qty:g}")
            if price is not None:
                parts.append(f"₹{price:,.2f}")
            lines.append("- " + " · ".join(p for p in parts if p))
        return "**Your trades today**\n" + "\n".join(lines)

    if isinstance(data, list):
        return "No records returned." if not data else (
            "**Account snapshot**\n"
            + "\n".join(f"- {row}" for row in data[:10] if isinstance(row, (str, int, float)))
        )
    return "Account snapshot retrieved."


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
            ""
        )
    if tool_name == "run_screen":
        matches = result.get("matches", [])
        excluded = result.get("excluded", [])
        criteria_text = "; ".join(
            f"{item['metric']} {item['op']} {item['value']}"
            for item in result.get("criteria", [])
        )
        match_lines = [
            f"- **{item['symbol']}**: "
            + ", ".join(
                f"{metric}={value:.4f}" if value is not None else f"{metric}=n/a"
                for metric, value in item["values"].items()
            )
            for item in matches
        ] or ["- (no symbols passed)"]
        excluded_note = (
            f"\n{len(excluded)} symbol(s) excluded (failed criteria or "
            "missing metrics)."
            if excluded
            else ""
        )
        return (
            f"Screen **{result.get('screen')}** v{result.get('version')} "
            f"({criteria_text}) over {result.get('universe_size', 0)} "
            f"symbol(s) with imported statements:\n"
            + "\n".join(match_lines)
            + excluded_note
            + "\nScreens evaluate deterministic ratios from imported "
            "statements only; import more statements to widen the universe."
        )
    if tool_name == "analyze_fundamentals":
        lines = []
        for item in result.get("ratios", []):
            value = item.get("value")
            rendered = f"{value:,.4f}".rstrip("0").rstrip(".") if value is not None else "n/a"
            lines.append(
                f"- **{item['name']}** = {rendered} "
                f"({item['formula']})"
            )
        warnings = result.get("warnings", [])
        warning_text = (
            "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)
            if warnings
            else ""
        )
        return (
            f"Fundamental analysis for **{result.get('symbol')}** "
            f"(latest period {result.get('latest_period')}, "
            f"{result.get('currency')}, source: {result.get('source')}; "
            f"periods stored: {', '.join(result.get('periods_available', []))}):\n"
            + "\n".join(lines)
            + warning_text
        )
    if tool_name == "mark_portfolio_to_market":
        marked = result.get("positions_marked", [])
        lines = [
            f"- **{item['symbol']}**: {item['quantity']:g} @ avg "
            f"{item['average_price']:g}, live {item['live_price']:g} → "
            f"unrealized {item['unrealized_pnl']:+,.2f}"
            for item in marked
        ] or ["- (no open positions)"]
        error_note = (
            f"\n{len(result.get('quote_errors', []))} position(s) could not "
            "be quoted."
            if result.get("quote_errors")
            else ""
        )
        return (
            f"**{result.get('name')}** ({result.get('portfolio_id')}) "
            "marked to live quotes:\n"
            + "\n".join(lines)
            + f"\n\nCash: {result.get('cash_balance'):,.2f} · Market value: "
            f"{result.get('market_value'):,.2f} · Total equity: "
            f"{result.get('total_equity'):,.2f} · Unrealized P&L: "
            f"{result.get('total_unrealized_pnl'):+,.2f}"
            + error_note
        )
    if tool_name in {"add_watchlist_symbol", "remove_watchlist_symbol"}:
        return (
            f"Watchlist updated: **{result.get('symbol')}** "
            f"{result.get('status')}."
        )
    if tool_name == "list_watchlist":
        symbols = result.get("symbols", [])
        if not symbols:
            return (
                "The watchlist is empty. Say 'add RELIANCE to watchlist' "
                "to start building the screening universe."
            )
        listed = ", ".join(
            f"{item['symbol']} ({item['exchange']})" for item in symbols
        )
        return f"Watchlist ({len(symbols)}): {listed}."
    if tool_name == "run_technical_screen":
        matches = result.get("matches", [])
        skipped = result.get("skipped", [])
        condition = str(result.get("condition", "")).replace("_", " ")
        universe = result.get("universe", "watchlist")
        universe_label = (
            "NIFTY 50" if universe == "nifty50" else "your watchlist"
        )
        scanned = result.get("universe_size", result.get("watchlist_size", 0))
        match_lines = [
            f"- **{item['symbol']}**"
            + (f" ({item['company_name']})" if item.get("company_name") else "")
            + f": close ₹{item.get('last_close')}"
            + (f" · RSI {item['rsi']}" if "rsi" in item else "")
            + (f" · EMA {item['ema']}" if "ema" in item else "")
            + (f" · volume {item['volume']}" if "volume" in item else "")
            for item in matches
        ]
        skipped_note = (
            f"\n\n_{len(skipped)} of {scanned} couldn't be checked "
            "(no data returned)._"
            if skipped
            else ""
        )
        if not matches:
            return (
                f"I scanned {scanned} {universe_label} stock(s) for "
                f"**{condition} {result.get('threshold')}** on "
                f"{result.get('interval')} candles — **none matched** right "
                f"now.{skipped_note}"
            )
        return (
            f"Scanned {scanned} {universe_label} stock(s) for "
            f"**{condition} {result.get('threshold')}** "
            f"({result.get('interval')} candles). "
            f"**{len(matches)} match(es):**\n"
            + "\n".join(match_lines)
            + skipped_note
        )
    if tool_name == "create_price_alert":
        return (
            f"Price alert created: **{result.get('symbol')}** "
            f"{result.get('direction')} {result.get('threshold')} "
            f"({result.get('exchange')}). It is checked against live "
            "quotes every minute while the broker connection is up; say "
            "'show my alerts' to review it."
        )
    if tool_name == "list_price_alerts":
        alerts = result.get("alerts", [])
        if not alerts:
            return (
                "No price alerts yet. Say 'alert me when RELIANCE goes "
                "above 1500' to create one."
            )
        lines = [
            f"- **{item['symbol']}** {item['direction']} {item['threshold']} "
            f"— {item['status']}"
            + (
                f" (last price {item['last_price']})"
                if item.get("last_price") is not None
                else ""
            )
            for item in alerts[:15]
        ]
        return f"Your price alerts:\n" + "\n".join(lines)
    if tool_name == "get_option_chain":
        analytics = result.get("analytics", {})
        pcr = analytics.get("put_call_oi_ratio")
        return (
            f"**{result.get('underlying')} option chain** "
            f"(expiry {result.get('expiry_date')}, "
            f"spot {result.get('underlying_ltp')}):\n"
            f"- ATM strike: {analytics.get('atm_strike')} "
            f"(call {analytics.get('atm_call_ltp')}, "
            f"put {analytics.get('atm_put_ltp')})\n"
            f"- ATM straddle cost: {analytics.get('atm_straddle_cost')}\n"
            f"- Put-call OI ratio: {pcr}\n"
            f"- Max call OI at {analytics.get('max_call_oi_strike')}; "
            f"max put OI at {analytics.get('max_put_oi_strike')}\n"
            f"- Strikes returned: {len(result.get('strike_rows', []))}"
        )
    if tool_name == "fetch_web_document":
        return (
            f"Saved **{result.get('title')}** from {result.get('source_url')} "
            f"into the document corpus ({result.get('chunk_count', 0)} "
            "chunk(s)).\n\n"
            f"You can now ask: 'analyze document {result.get('title')}' or "
            "'search knowledge <topic>'."
        )
    if tool_name == "run_strategy_optimization":
        return _render_optimization_result(result)
    if tool_name == "deep_research":
        return _render_research_briefing(result)
    if tool_name == "deep_research_report":
        return _render_research_report(result)
    if tool_name == "remember":
        return _render_remember_result(result)
    if tool_name == "recall_memory":
        return _render_recall_result(result)
    if tool_name == "find_and_analyze_document":
        chunks = result.get("chunks", [])
        excerpt_lines = []
        for chunk in chunks:
            content = " ".join(str(chunk.get("content", "")).split())
            if len(content) > 320:
                content = content[:320].rstrip() + "…"
            excerpt_lines.append(f"{chunk.get('chunk_index', 0) + 1}. {content}")
        excerpts = "\n".join(excerpt_lines) or "(no readable text)"
        if result.get("source") == "web_fetched":
            source = (
                f"I fetched **{result.get('title')}** from "
                f"{result.get('source_url')} and indexed it"
            )
        else:
            source = f"From your stored document **{result.get('title')}**"
        return (
            f"{source} ({result.get('chunk_count', 0)} section(s), "
            f"{result.get('total_words', 0)} words).\n\n"
            f"Here is what it covers:\n{excerpts}\n\n"
            "Ask a follow-up and I'll answer from this document."
        )
    if tool_name == "analyze_knowledge_document":
        chunks = result.get("chunks", [])
        excerpt_lines = []
        for chunk in chunks:
            content = " ".join(str(chunk.get("content", "")).split())
            if len(content) > 320:
                content = content[:320].rstrip() + "…"
            excerpt_lines.append(f"{chunk.get('chunk_index', 0) + 1}. {content}")
        excerpts = "\n".join(excerpt_lines) or "(no stored text)"
        return (
            f"**{result.get('title')}** ({result.get('document_id')}, "
            f"{result.get('document_type')}) — "
            f"{result.get('chunk_count', 0)} stored chunk(s), "
            f"{result.get('total_words', 0)} words.\n\n"
            f"Key excerpts in document order:\n{excerpts}\n\n"
            "Ask 'search knowledge <topic>' to pull specific passages."
        )
    if tool_name == "search_knowledge":
        matches = result.get("matches", [])
        if not matches:
            return (
                "I couldn't find anything about that in your stored "
                "documents. You can upload reports in the Data tab or say "
                "'fetch <url> and store it'."
            )
        excerpts = []
        for item in matches[:3]:
            content = " ".join(str(item.get("content", "")).split())
            if len(content) > 260:
                content = content[:260].rstrip() + "…"
            excerpts.append(f"- **{item['title']}**: {content}")
        return (
            "Here's what your documents say:\n" + "\n".join(excerpts)
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
            ""
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
            f"{len(news.get('articles', []))}."
        )
    if tool_name == "create_research_brief":
        actions = result.get("next_actions", [])
        return (
            f"Created research brief {result['brief_id']} for "
            f"{result['symbol']} {result['asset_class']}. "
            f"Evidence dataset: {result.get('evidence', {}).get('dataset_id') or 'none'}; "
            f"next action: {actions[0] if actions else 'none'}. "
            ""
        )
    if tool_name == "get_execution_readiness":
        stages = result.get("stages", [])
        ready = [
            stage["stage"]
            for stage in stages
            if stage.get("can_start")
        ]
        blocker = result.get("next_blocker")
        summary = (
            f"Execution readiness for {result['symbol']} "
            f"{result['asset_class']} checked. Ready stages: "
            f"{', '.join(ready) or 'none'}. Next blocker: "
            f"{blocker['stage'] if blocker else 'none'}. "
            ""
        )
        data = result.get("data_readiness", {})
        paper_signal = result.get("paper_signal", {})
        if not paper_signal.get("eligible"):
            return (
                f"{summary} A current risk-approved paper signal is still "
                "required before any OpenAlgo analyzer order can be prepared. "
                f"{paper_signal.get('next_action', 'Refresh data and run the named strategy again.')}"
            )
        if (
            not data.get("local_dataset_exists")
            and data.get("historical_available")
        ):
            return (
                f"{summary} OpenAlgo has verified historical data for this "
                "instrument, but it has not been imported into the governed "
                "catalog yet. Ask me to import its historical data, then name "
                "the strategy for a semi-auto backtest."
            )
        return summary
    if tool_name == "get_openalgo_monitor":
        return (
            f"OpenAlgo monitor status: {result['status']}. "
            f"Configured: {result['configured']}; live trading enabled: "
            f"{result['live_trading_enabled']}."
        )
    if tool_name == "get_openalgo_snapshot":
        return _render_account_snapshot(result)
    if tool_name == "search_instruments":
        return (
            f"OpenAlgo instrument search status: {result.get('status')}. "
            f"Matches: {result.get('match_count', 0)}. "
            ""
        )
    if tool_name == "validate_instrument_symbol":
        instrument = result.get("instrument", {})
        return (
            f"Symbol validation status: {result.get('status')}. "
            f"Resolved: {instrument.get('symbol', result.get('symbol'))} "
            f"on {instrument.get('exchange', result.get('exchange'))}. "
            ""
        )
    if tool_name == "resolve_option_symbol":
        return (
            f"Option symbol resolution status: {result.get('status')}. "
            f"Resolved: {result.get('resolved_symbol')} on "
            f"{result.get('resolved_exchange')}. "
            ""
        )
    if tool_name == "get_market_news":
        if not result.get("ok"):
            return (
                f"Market news unavailable: {result.get('message')}. "
                ""
            )
        articles = result.get("articles", [])
        if not articles:
            subject = result.get("symbol") or result.get("query") or "this request"
            return (
                f"The configured news provider returned no matching articles "
                f"for {subject}. No market outlook was inferred from missing "
                "news."
            )
        unique_headlines: list[str] = []
        seen_titles: set[str] = set()
        for item in articles:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Untitled").strip()
            normalized_title = re.sub(r"\s+", " ", title.lower())
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            unique_headlines.append(
                f"- **{title}** — {item.get('source', 'source unavailable')}"
            )
            if len(unique_headlines) == 5:
                break
        subject = result.get("symbol") or result.get("query") or "the market"
        return (
            f"**Latest headlines for {subject}**\n"
            + "\n".join(unique_headlines)
        )
    if tool_name == "get_market_quote":
        if not result.get("ok"):
            return (
                f"Market quote unavailable: "
                f"{str(result.get('message') or '').rstrip('.')}. "
                ""
            )
        quote = result.get("quote", {})
        fields = [
            f"{field}={quote[field]}"
            for field in (
                "ltp",
                "last_price",
                "close",
                "open",
                "high",
                "low",
                "volume",
                "timestamp",
            )
            if field in quote
        ]
        return (
            f"Provider-backed quote for {result.get('resolved_symbol')} on "
            f"{result.get('resolved_exchange')}: "
            f"{', '.join(fields) or 'no quote fields returned'}."
            + (
                " Resolved from the local OpenAlgo master contract."
                if result.get("instrument_resolution")
                == "local_contract_fuzzy_match"
                else ""
            )
        )
    if tool_name == "list_strategy_personas":
        personas = result.get("personas", [])
        if not personas:
            return "No governed strategy personas are configured."
        lines = []
        for item in personas:
            preferred = ", ".join(
                item.get("strategy_bias", {}).get("preferred_strategies", [])
            ) or "none"
            lines.append(
                f"- **{item.get('name')}** ({item.get('persona_id')}): "
                f"{item.get('description')} Preferred strategies: "
                f"{preferred}."
            )
        return (
            f"Found {len(personas)} governed strategy persona(s):\n"
            + "\n".join(lines)
            + "\n\nAsk about one by name, for example 'show the "
            "conservative value persona'."
        )
    if tool_name == "get_strategy_persona":
        persona = result.get("persona", {})
        bias = persona.get("strategy_bias", {})
        risk_rules = persona.get("risk_rules", {})
        preferred = ", ".join(bias.get("preferred_strategies", [])) or "none"
        risk_lines = []
        if risk_rules.get("max_order_value") is not None:
            risk_lines.append(
                f"max order value {float(risk_rules['max_order_value']):,.0f}"
            )
        if risk_rules.get("stop_loss_pct") is not None:
            risk_lines.append(
                f"stop loss {float(risk_rules['stop_loss_pct']) * 100:g}%"
            )
        if risk_rules.get("requires_approval_for_paper"):
            risk_lines.append("paper orders need human approval")
        if risk_rules.get("requires_approval_for_live"):
            risk_lines.append("live orders need human approval")
        focus = ", ".join(persona.get("dashboard_focus", [])) or "none"
        first_strategy = (
            bias.get("preferred_strategies", ["sma_crossover"]) or
            ["sma_crossover"]
        )[0]
        sections = [
            f"**{persona.get('name')}** ({persona.get('persona_id')}): "
            f"{persona.get('description')}",
            f"- **Asset classes**: "
            f"{', '.join(persona.get('asset_classes', []))}",
            f"- **Preferred strategies**: {preferred}",
        ]
        if bias.get("selection_style"):
            sections.append(
                f"- **Selection style**: {bias['selection_style']}"
            )
        if risk_lines:
            sections.append(f"- **Risk rules**: {'; '.join(risk_lines)}")
        sections.append(f"- **Dashboard focus**: {focus}")
        if persona.get("prompt_guidance"):
            sections.append(
                f"- **Guidance**: {persona['prompt_guidance']}"
            )
        sections.append(
            f"\nTry: 'backtest {first_strategy} on RELIANCE'. This persona "
            "guides strategy choice and explanation style but does not "
            "bypass data, risk, approval, or OpenAlgo checks."
        )
        return "\n".join(sections)
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
    if tool_name == "square_off_all":
        return (
            "Square-off sent — all open positions are being closed at "
            "market. Check the Account tab or your broker orderbook to confirm."
        )
    if tool_name == "cancel_all_orders":
        return (
            "Cancel-all sent — your pending orders are being cancelled. "
            "Check the Account tab to confirm."
        )
    if tool_name == "list_pending_approvals":
        approvals = result.get("approvals", [])
        if not approvals:
            return (
                "You have no orders waiting for approval. Tell me an order "
                "like 'buy 10 RELIANCE at market' to prepare one."
            )
        lines = [f"- {_pending_order_summary(a)}" for a in approvals[:10]]
        if len(approvals) == 1:
            return (
                "You have **1 order waiting for your approval:**\n"
                + "\n".join(lines)
                + "\n\nReply **approve** to send it to your broker."
            )
        return (
            f"You have **{len(approvals)} orders waiting for your "
            "approval:**\n" + "\n".join(lines)
            + "\n\nReply **approve** to send the most recent, or name the one "
            "you want."
        )
    if tool_name == "approve_pending_order":
        status = result.get("status")
        if status == "nothing_pending":
            return (
                "There are no orders waiting for approval right now."
            )
        if status == "not_found":
            return (
                f"I couldn't find that pending order. "
                "Say 'show pending orders' to see what's waiting."
            )
        if status == "multiple_pending":
            lines = [
                f"- {_pending_order_summary(a)}"
                for a in result.get("approvals", [])[:10]
            ]
            return (
                "You have several orders waiting — which one?\n"
                + "\n".join(lines)
            )
        broker = result.get("broker_order_id")
        return (
            f"Approved and submitted. Order status: "
            f"**{result.get('order_status')}**"
            + (f" · broker order {broker}" if broker else "")
            + ". You can see it in the Account tab and in your broker's orderbook."
        )
    if tool_name == "prepare_direct_order":
        approval = result.get("approval_id")
        approval_line = (
            "Reply **approve** to send it to your broker."
            if approval
            else "It's approved and ready — reply **approve** to submit it."
        )
        return (
            f"**Order ready for your approval**\n"
            f"- {result['side']} {result['quantity']} {result['symbol']} "
            f"({result.get('exchange', 'NSE')})\n"
            f"- Type: {result.get('order_type', 'MARKET')} · "
            f"Product: {result.get('product', 'MIS')}\n\n"
            f"{approval_line} Nothing has been placed yet."
        )
    if tool_name == "import_openalgo_history":
        return (
            f"Imported {result['row_count']} verified OpenAlgo candle(s) for "
            f"{result['resolved_symbol']} {result['resolved_exchange']} into "
            f"dataset {result['dataset_id']}. You can now run a research or "
            "semi-auto backtest against this exact dataset."
        )
    if tool_name == "prepare_live_order_intent":
        return (
            f"⚠ **LIVE order ready for your approval**\n"
            f"- {result['side']} {result['quantity']} {result['symbol']} "
            f"({result.get('exchange', 'NSE')})\n\n"
            "This will place a **real** order with real money. Reply "
            "**approve** to send it to your broker, or ignore it to cancel. "
            "Nothing has been placed yet."
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
        summary = (
            f"Backtest {result['run_id']} completed for "
            f"{result['strategy']}. Net P&L: {result['net_pnl']:.2f}; "
            f"max drawdown: {result['max_drawdown']:.2f}; "
            f"return: {result['return_pct']:.4f}%."
        )
        if result.get("execution_mode") == "semi_auto":
            return (
                f"{summary} This is a historical simulation stored in "
                "Strategy Runs. Its risk decisions are available in Execution "
                "Controls to prepare a human-approved OpenAlgo analyzer order; "
                "only the submitted analyzer order will appear in OpenAlgo."
            )
        return summary
    if tool_name == "run_custom_strategy_spec":
        return (
            f"Custom strategy spec {result['custom_strategy_spec_id']} "
            f"backtest {result['run_id']} completed through the native "
            f"{result['strategy']} runtime. Net P&L: "
            f"{result['net_pnl']:.2f}; max drawdown: "
            f"{result['max_drawdown']:.2f}; return: "
            f"{result['return_pct']:.4f}%. No generated code was executed."
        )
    if tool_name == "get_custom_strategy_capabilities":
        contracts = result.get("data_contracts", {})
        rule_data = contracts.get("rule_backtesting", {})
        return (
            "Native custom-rule strategies support "
            f"{', '.join(result.get('supported_indicators', []))}; "
            f"position sides: {', '.join(result.get('supported_position_sides', []))}; "
            "and OHLCV backtests for "
            f"{', '.join(rule_data.get('supported_asset_classes', []))}. "
            "Numeric external data can be imported as governed point-in-time "
            "feature series and declared in feature_inputs before it is used "
            "in a rule."
        )
    if tool_name == "compile_custom_strategy_spec":
        return _compiled_strategy_response(result)
    if tool_name == "update_custom_strategy_spec":
        missing = result.get("missing_capabilities", [])
        state = (
            "executable by the native rule runtime"
            if not missing
            else "stored for review and is not executable yet"
        )
        return (
            f"Updated custom strategy {result['spec_id']} "
            f"(status: {result['status']}). The revised spec is {state}."
        )
    if tool_name == "create_custom_strategy_spec":
        missing = result.get("missing_capabilities", [])
        if missing:
            missing_values = ", ".join(
                str(item.get("value") or item.get("kind"))
                for item in missing
                if isinstance(item, dict)
            )
            guidance = _missing_capability_guidance(missing)
            return (
                f"Stored custom strategy draft {result['spec_id']} for review. "
                "It is not executable by the native rule runtime because it "
                f"requires: {missing_values or 'unsupported primitives'}. "
                f"Next governed data step: {guidance}"
            )
        return (
            f"Stored executable custom strategy draft {result['spec_id']}. "
            "It can be backtested through the native deterministic rule runtime; "
            "no generated code was executed."
        )
    if "run_id" in result:
        return f"Retrieved stored evidence for run {result['run_id']}."
    return "The requested tool completed successfully."


def _compiled_strategy_response(result: dict[str, Any]) -> str:
    spec = result.get("spec", {})
    risk = spec.get("risk", {}) or {}
    session = spec.get("session")
    lines = [
        "Here is the compiled strategy specification for your review. "
        "It has NOT been saved or executed.",
        f"- Instrument: {spec.get('symbol')} | timeframe: "
        f"{spec.get('timeframe')} | side: {spec.get('position_side')}",
        f"- Entry: {_describe_rules(spec.get('entry_rules'))}",
        f"- Exit: {_describe_rules(spec.get('exit_rules'))}",
    ]
    risk_parts = [
        f"{key.replace('_pct', '').replace('_', ' ')} "
        f"{value * 100:g}%" if key.endswith("_pct") else f"{key} {value}"
        for key, value in risk.items()
    ]
    if risk_parts:
        lines.append(f"- Risk: {', '.join(risk_parts)}")
    if session:
        lines.append(
            f"- Entries limited to {session.get('start')}-{session.get('end')}"
        )
    unparsed = result.get("unparsed_clauses") or []
    if unparsed:
        lines.append(
            "- I could not interpret: "
            + "; ".join(f"“{clause}”" for clause in unparsed)
        )
    for warning in result.get("warnings") or []:
        lines.append(f"- Note: {warning}")
    missing = result.get("missing_capabilities") or []
    if missing:
        reasons = "; ".join(
            str(item.get("reason") or item.get("value"))
            for item in missing
            if isinstance(item, dict)
        )
        lines.append(f"- Blocking issues before it can run: {reasons}")
    else:
        lines.append(
            "- Validation passed: this spec can run on the native "
            "deterministic rule runtime."
        )
    lines.append(
        "Review and edit it in the Strategies panel, then save it "
        "explicitly to create a governed draft."
    )
    return "\n".join(lines)


def _describe_rules(rules: Any) -> str:
    if not rules:
        return "none recognized"
    parts = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        prefix = (
            ""
            if index == 0
            else f"{str(rule.get('joiner', 'AND')).upper()} "
        )
        operator = str(rule.get("operator", "")).replace("_", " ")
        parts.append(
            f"{prefix}{rule.get('left')} {operator} {rule.get('right')}"
        )
    return "; ".join(parts) or "none recognized"


def _missing_capability_guidance(
    missing_capabilities: list[dict[str, Any]],
) -> str:
    values = " ".join(
        str(item.get("value", "")).lower()
        for item in missing_capabilities
        if isinstance(item, dict)
    )
    guidance: list[str] = []
    if any(token in values for token in ("iv", "skew", "oi", "open_interest")):
        guidance.append(
            "import a point-in-time numeric feature series through "
            "/datasets/features, then declare it in feature_inputs with an "
            "asof freshness limit"
        )
    if any(
        token in values
        for token in ("earnings", "fundamental", "edgar", "quandl")
    ):
        guidance.append(
            "import a point-in-time fundamentals feature series with source "
            "timestamps and revision metadata, then declare it in feature_inputs"
        )
    if any(token in values for token in ("news", "sentiment", "newsapi")):
        guidance.append(
            "import an archived news/sentiment numeric feature series with "
            "availability timestamps, then declare it in feature_inputs"
        )
    if not guidance:
        guidance.append(
            "import a governed numeric feature series with provenance and "
            "availability timestamps, then declare it in feature_inputs"
        )
    return "; ".join(guidance)


def grounded_multi_tool_response(
    results: list[tuple[str, dict[str, Any]]],
) -> str:
    """Compose a deterministic, evidence-backed answer for read-only checks."""
    summaries = [
        _grounded_fallback_response(tool_name, result)
        for tool_name, result in results
    ]
    return "Completed governed read-only checks:\n- " + "\n- ".join(summaries)
