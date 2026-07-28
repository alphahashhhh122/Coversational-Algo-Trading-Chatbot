"""Orchestration: turning a message into a tool call, and the result into words.

This was one 4,567-line module. It is now four, split along seams that were
already there rather than invented:

- :mod:`.education` — concepts, domain refusals, advice deflection. Depends on
  nothing else here, so the router can use it without a cycle.
- :mod:`.text` — reading a message: symbols, dates, parameters, intent. Pure
  functions over strings, testable without building an orchestrator. Every
  routing bug this project has had came from a phrase being read wrong, which
  is why this is worth isolating.
- :mod:`.renderers` — tool payload in, plain English out. Never invents a
  number; absent data is reported as absent.
- :mod:`.core` — the three-tier router (deterministic regex → LLM function
  calling → plain LLM) and the orchestrator contracts.

Everything the module previously exposed is re-exported here, including the
underscore-prefixed helpers the tests pin behaviour on. No import site had to
change, which is what makes the refactor checkable: the suite proves behaviour
is identical rather than merely still passing something else.
"""

from __future__ import annotations


from .education import (  # noqa: F401
    _ADVICE_PATTERNS,
    _DOMAIN_TERMS,
    _EDUCATION_MAP,
    _EDUCATION_PREFIX_RE,
    _FINANCE_ACRONYMS,
    _OFF_TOPIC_CATEGORIES,
    _domain_refusal_response,
    _education_lookup,
    _educational_response,
    _is_open_ended_advice,
    _off_topic_category,
    _open_ended_advice_response,
)

from .text import (  # noqa: F401
    _INTENT_TERMS,
    _ROUTER_SYSTEM_PROMPT,
    _asset_class_from_text,
    _chat_messages,
    _clean_identifier,
    _clean_symbol,
    _closest_action_response,
    _contains_any_word,
    _dataset_from_text,
    _date_range_from_text,
    _default_exchange_for_asset,
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
    _last_user_message,
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
    _timeframe_from_text,
    _tool_arguments,
)

from .renderers import (  # noqa: F401
    _WALK_FORWARD_LABELS,
    _compiled_strategy_response,
    _describe_rules,
    _grounded_fallback_response,
    _missing_capability_guidance,
    _name_suffix,
    _num,
    _pending_order_summary,
    _pick,
    _render_account_snapshot,
    _render_comparison_result,
    _render_optimization_result,
    _render_recall_result,
    _render_remember_result,
    _render_research_briefing,
    _render_research_report,
    _render_walk_forward_result,
    _render_watch_check,
    _render_watch_list,
    _short_date,
    _watch_condition_text,
    grounded_multi_tool_response,
    grounded_tool_response,
)

from .core import (  # noqa: F401
    GroqToolOrchestrator,
    OfflineOrchestrator,
    OpenAIResponsesOrchestrator,
    OrchestrationDecision,
    Orchestrator,
    ToolInvocation,
    _chat_completion_tools,
    build_orchestrator,
)

__all__ = [
    "GroqToolOrchestrator",
    "OfflineOrchestrator",
    "OpenAIResponsesOrchestrator",
    "OrchestrationDecision",
    "Orchestrator",
    "ToolInvocation",
    "build_orchestrator",
    "grounded_multi_tool_response",
    "grounded_tool_response",
]
