# IIM-C Conversational Algo Trading Platform: Interview Ownership Prep

This document is for defending the resume bullets under intense AI/backend/SWE/quant-dev questioning. The goal is not to memorize every line of code. The goal is to own the architecture, tradeoffs, failure modes, and honest limitations.

## Resume Bullets Being Defended

- Built a conversational algorithmic trading platform with Groq LLM orchestration, RAG with BM25 retrieval, FastAPI, DuckDB, and OpenAlgo/Dhan APIs for chatbot-driven market research, backtesting, instrument discovery, and dashboard monitoring.
- Designed a governed agentic execution layer for signal generation, pre-trade risk checks, paper-trading sandbox workflows, broker-state monitoring, and performance analytics, with HITL role-gated approvals, audit trails, and live-trading safety gates.
- Validated on 66K+ options OHLCV rows, live broker/news-provider feeds, OpenAlgo quote/history/analyzer workflows, and broker-backed NSE/NFO/MCX instrument discovery through API and service-layer tests.

## Non-Negotiable Truths

- This project uses a custom LLM tool router/orchestrator, not LangGraph.
- LangGraph appears on the resume for MASP, not for the IIM-C platform.
- HITL is a backend role-gated approval layer, not a LangGraph interrupt node.
- Live trading is disabled by default through config and backend guards.
- Paper-trading sandbox workflows exist, but do not overclaim full broker-grade trade-fill reconciliation if asked.
- There is no formal measured LLM tool-routing accuracy yet; if asked, say this is a next evaluation step.
- The strongest design idea is separation of reasoning from execution: LLM routes, backend validates and executes.

## 60-Second Pitch

I built a conversational algorithmic trading platform where a user can ask natural-language questions to research markets, backtest strategies, discover instruments, monitor broker state, and run governed paper-trading workflows. The architecture uses FastAPI as the backend API layer, a Groq-powered custom tool orchestrator for intent routing, a Pydantic tool registry for typed tool calls, DuckDB for analytical persistence, BM25 retrieval for governed project/domain knowledge, and OpenAlgo/Dhan APIs for broker-backed market and instrument workflows. The key engineering decision was to keep the LLM out of direct execution: it can choose structured tools, but backend services enforce capability checks, symbol validation, risk controls, HITL approvals, audit logging, and live-trading safety gates.

## One-Line Architecture

Chat UI -> FastAPI route -> Groq/custom orchestrator -> Pydantic tool registry -> domain service -> repository/provider -> DuckDB/OpenAlgo/Dhan/News provider -> response + dashboard/audit update.

## Whiteboard Flow: User Asks A Backtest

1. User types: "Backtest EMA crossover on NIFTY options."
2. Chat route receives the message.
3. Orchestrator builds context using system policy, tool registry, and conversation state.
4. LLM selects a structured backtest tool instead of free-form text.
5. Pydantic validates tool arguments such as strategy, symbol, timeframe, quantity, and dataset.
6. CapabilityCoverageService checks whether the action-object pair is supported.
7. Backtest service loads historical OHLCV from DuckDB or a registered provider.
8. Strategy logic generates entry/exit signals.
9. Risk/performance services compute metrics such as trades, drawdown, Sharpe-like statistics, and summary.
10. Tool call, audit event, signals, risk decisions, and result metadata are persisted.
11. FastAPI returns the structured result to the dashboard/chat UI.

## Whiteboard Flow: User Asks For Trading

1. User asks for a paper or live order.
2. Orchestrator routes to a trading-intent/order-preparation tool.
3. Backend validates mode, symbol, exchange segment, quantity, order type, and configured permissions.
4. Risk service checks quantity, exposure, and allowed action.
5. HITL approval is required for sensitive actions.
6. Paper trading goes through sandbox workflow.
7. Live trading remains blocked unless explicit config and backend guard allow it.
8. Every decision is logged in audit_events and tool_calls.

## Why Not Just A ChatGPT Wrapper?

A ChatGPT wrapper can answer questions and maybe call APIs, but it does not own persistent state, trading permissions, data freshness, backtest results, order lifecycle, audit trails, broker-state monitoring, or safety gates. This project is a backend trading platform with a chatbot interface. The LLM is the language and routing layer. The backend owns execution and state.

## Why FastAPI?

FastAPI gives typed request/response models, automatic OpenAPI docs, easy Pydantic integration, async-friendly endpoints, and clean separation between routes and services. It is a good fit because the platform needs multiple machine-callable endpoints for chat, dashboard, tools, backtests, broker monitor, approvals, and market/news workflows.

Alternatives:
- Flask: simpler, but weaker built-in typing/OpenAPI ergonomics.
- Django: powerful but heavier than needed for an API-centric prototype.
- Node/Express: viable, but Python was better for trading/data/LLM libraries.

## Why DuckDB?

DuckDB is an embedded analytical database. It is strong for local analytical workloads like OHLCV data, signals, tool-call logs, audit events, backtest outputs, and performance summaries. It avoids running a separate database server while still supporting SQL analytics much better than flat files.

Alternatives:
- SQLite: good embedded OLTP store, but weaker for analytical columnar queries.
- PostgreSQL: better for multi-user production OLTP, but requires a server and more ops.
- Parquet-only: good storage format, but not enough for application state and transactional metadata.

## Why BM25 Retrieval?

BM25 is a lexical retrieval algorithm that ranks documents based on query-term overlap, inverse document frequency, term-frequency saturation, and document-length normalization. It was chosen because the governed knowledge corpus is domain/project-document heavy, where exact terms like "OpenAlgo", "Dhan", "risk_decisions", "dataset_freshness_policies", or "HITL" matter.

Formula:

```text
score(D, Q) = sum over query terms q:
IDF(q) * ((f(q,D) * (k1 + 1)) / (f(q,D) + k1 * (1 - b + b * |D| / avgdl)))
```

What k1 controls: how quickly term frequency saturates.

What b controls: how strongly document length is normalized.

Why not vector search first:
- The corpus is smaller and entity-heavy.
- Exact technical terms matter.
- BM25 is explainable and easy to debug.
- Vector search would help later for semantic paraphrases, but adds embedding model choice, vector storage, reranking, and evaluation complexity.

Best future upgrade: hybrid search using BM25 + embeddings + reciprocal rank fusion, followed by reranking.

## What Is RAG Here?

RAG means retrieval-augmented generation. Before the LLM answers, the system retrieves relevant governed documents or context and injects them into the prompt/tool-routing context. The retrieval layer reduces hallucination because the LLM is grounded in project-specific, versioned, freshness-aware context.

## What Is The Custom Orchestrator?

The orchestrator is the component that receives the user message, supplies tool definitions/context to the LLM, receives the selected structured tool call, validates it, executes the matching backend service, and returns a governed response. It is custom rather than LangGraph because the initial workflow is mostly controlled tool routing rather than a complex multi-node cyclic agent graph.

If asked "why not LangGraph?":

LangGraph is useful when you need explicit graph state, conditional edges, retries, interrupts, and multi-agent workflows. In this project, the immediate requirement was a controlled trading assistant with typed tools, capability checks, and approval gates. A custom orchestrator was simpler, easier to audit, and enough for the current workflow. LangGraph could be introduced later for more complex multi-step planning and approval state machines.

## What Is The Tool Registry?

The tool registry is the controlled action space exposed to the LLM. Instead of letting the LLM invent actions, the system defines allowed tools with typed inputs and outputs. The project has 28 tools without broker credentials and 32 with broker integrations available.

Tool examples:
- market/news fetch
- knowledge retrieval
- dataset lookup
- backtest strategy
- instrument discovery
- broker monitor
- paper-trading sandbox workflow
- approval preparation
- performance/dashboard query

## Why Pydantic?

Pydantic gives runtime validation and typed schemas. That matters because LLM outputs are not trustworthy by default. If the LLM emits a malformed quantity, missing symbol, unsupported mode, or wrong field type, the Pydantic model rejects or normalizes it before the service runs.

## CapabilityCoverageService

This is an action-object whitelist. It answers: "Is this category of user request supported for this kind of object?"

Why this is better than keyword blacklists:
- Blacklists are fragile and miss paraphrases.
- Whitelists define the allowed capability surface.
- It is safer for trading because unsupported actions fail closed.

Example:
- Allowed: backtest strategy on historical data.
- Allowed: monitor broker state.
- Allowed: prepare paper-trading workflow.
- Blocked or gated: direct live execution without approvals/config.

## HITL Role-Gated Approvals

HITL means human-in-the-loop. In this project it is a backend approval layer. It is not a LangGraph interrupt node. Sensitive actions require an approval state before execution. Role-gating means different roles can be allowed to approve different action categories.

Why needed:
- Trading is financially sensitive.
- LLMs can misunderstand intent.
- Users may phrase risky actions casually.
- Approval creates accountability and auditability.

## Audit Trails

Audit trails store what happened, when, why, and with which inputs. The project stores tool_calls and audit_events so that decisions can be inspected after the fact.

Why tool_calls:
- Shows which tool the LLM selected.
- Stores inputs/outputs/errors.
- Helps debug routing failures.

Why audit_events:
- Captures business/security events.
- Useful for approvals, risk decisions, execution attempts, and policy enforcement.

## ResponseEvaluator

The ResponseEvaluator is a post-execution check. It catches cases where the generated answer claims unsupported metrics or results that the tool output did not actually contain. This is important because an LLM can hallucinate after a successful tool call.

## Backtesting Concepts

Backtesting runs a strategy on historical OHLCV data.

OHLCV:
- Open: first traded price in the candle.
- High: highest price in the candle.
- Low: lowest price in the candle.
- Close: last traded price in the candle.
- Volume: traded quantity in the candle.

Common pitfalls:
- Look-ahead bias: using future data to make past decisions.
- Survivorship bias: only testing instruments that survived.
- Overfitting: tuning strategy to one historical period.
- Transaction costs/slippage: ignoring execution frictions.

## Strategies In This Project

There are 4 deterministic strategies:
- EMA/SMA crossover
- RSI mean reversion
- momentum ROC
- trend following

Do not claim dozens of live strategies. The strength is the extensible architecture, not a huge strategy library.

## Trading Workflow Terms

Signal generation:
The strategy identifies entry/exit points from data.

Risk management:
Checks whether the proposed action fits constraints such as quantity, exposure, mode, and allowed workflow.

Order management:
Tracks an order through lifecycle states such as PENDING, SUBMITTED, FILLED, REJECTED, or CANCELLED.

Performance analytics:
Summarizes outcomes using returns, trade count, drawdown, win rate, and related metrics.

## OpenAlgo/Dhan

OpenAlgo is the broker abstraction layer. Dhan is the broker/provider. The platform talks to OpenAlgo, which bridges to Dhan for market/broker workflows. This keeps broker-specific logic separated from the rest of the app.

If asked why not call Dhan directly:

Calling Dhan directly would couple the platform to one broker's API shape. OpenAlgo provides a more uniform trading API and makes the system easier to adapt across brokers.

## Phase 11E Bug Deep Dive

Root cause:
BacktestService.run() could be called with dataset_id=None, and the database attempted to insert into dataset_freshness_policies where dataset_id had a NOT NULL constraint. This raised a DuckDB ConstraintException that was not caught by the route handler, because the handler only caught ValueError. The exception escaped as HTTP 500.

Fix explanation:
The fix was to validate dataset presence earlier and/or handle DuckDB exceptions as structured API errors, so invalid input produces a controlled response instead of an unhandled server crash.

What this shows:
- Backend validation must happen before persistence.
- Exception boundaries matter.
- Tests should include invalid input paths, not only happy paths.

## Attack Question Bank With Model Answers

### "Isn't this just a ChatGPT wrapper?"

No. The chatbot is only the interface. The platform has a typed tool registry, backend services, DuckDB persistence, OpenAlgo/Dhan integration, risk checks, approval workflows, audit logs, and dashboard APIs. The LLM routes requests, but the backend owns validation, state, and execution.

### "Did you actually use LangGraph here?"

No. LangGraph is on my resume because of another project, MASP. For this IIM-C platform I used a custom orchestrator with a Pydantic tool registry. I chose that because the initial need was controlled typed tool execution, not a complex graph of cyclic agent states. I can explain how I would migrate to LangGraph later if multi-step planning becomes more complex.

### "Did you execute real live trades?"

Live trading is intentionally disabled by default with config and backend guards. The system supports broker-backed monitoring and governed workflows, but I designed live execution to require explicit enablement, risk checks, and approval. For a university/research setting, that safety boundary is the correct default.

### "Why claim agentic if it is not autonomous?"

Agentic does not have to mean fully autonomous. Here it means the system can interpret user intent, choose from a set of tools, execute workflows, observe results, and respond. It is governed agentic execution because autonomy is bounded by tool schemas, backend validation, and approvals.

### "What is novel?"

The novelty is not inventing a new trading strategy or new LLM architecture. It is integrating conversational UX, typed LLM tool routing, broker workflows, analytical persistence, RAG grounding, risk/HITL controls, and dashboard monitoring into one platform for non-coding users.

### "What would you improve next?"

I would add formal LLM routing evaluation, hybrid retrieval with vector search plus BM25, deeper trade-fill reconciliation for paper/live workflows, better multi-user auth/RBAC, streaming market data through WebSockets, and production observability with metrics and alerting.

## System Design: Scale To 1000 Users

For 1000 concurrent users, I would split the current embedded prototype into separate services. The FastAPI app would remain the API gateway/orchestration service. LLM requests would go through a queue and rate-limited provider client. Long-running backtests would move to background workers using Celery/RQ or a managed queue. DuckDB could remain for local analytical files, but multi-user transactional state should move to PostgreSQL while large OHLCV data can live in object storage/Parquet queried by DuckDB or a warehouse. Broker API calls need per-user credentials, token refresh, strict rate limiting, and isolation. Real-time market data should use WebSockets or a streaming bus. Audit logs should be append-only. Observability should include request latency, tool-routing failures, provider errors, approval timeouts, and execution failures.

## Debugging: LLM Picks Wrong Tool

Steps:
1. Inspect stored tool_call input and selected tool.
2. Compare user prompt with tool descriptions.
3. Check whether relevant tool schema was exposed to the LLM.
4. Check prompt instructions and capability policy.
5. Reproduce with deterministic test prompt.
6. Add routing examples or tighten tool descriptions.
7. Add evaluator checks for unsupported result claims.
8. Add test case to prevent regression.

## The Best One-Line Self-Awareness Answer

The current platform is strong as a governed research/backtesting/broker-monitoring assistant, but the next production steps are formal routing evaluation, stronger multi-tenant auth, streaming market data, and complete trade-fill reconciliation before claiming institutional-grade live execution.

