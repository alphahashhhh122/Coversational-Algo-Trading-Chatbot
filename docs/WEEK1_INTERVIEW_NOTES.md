# Week 1 Interview Notes

## What We Built

Week 1 establishes the production foundation of the conversational algo-trading
platform.

We built:

- configuration module
- domain models
- stable enums for statuses and execution modes
- database initialization
- core app-state tables
- data catalog service
- callable catalog tools
- CLI commands
- smoke tests

The important idea is that we are not starting from the chatbot. We are first
building reliable backend capabilities that a chatbot can safely orchestrate.

## Why Domain Models Are Separate From Database Code

Domain models describe the business concepts:

- dataset
- strategy run
- signal
- risk decision
- order event
- trade fill
- performance summary
- chat message
- tool call
- OpenAlgo snapshot
- audit event

They should not depend on DuckDB, OpenAlgo, FastAPI, or the frontend. This keeps
the core design stable even if we later change the database or API framework.

Interview answer:

> I separated domain models from infrastructure so that trading concepts remain
> stable and testable. The database is only one persistence mechanism. The
> orchestrator, services, and frontend can all use the same domain language
> without depending on database details.

## Why We Added Status Enums

Trading systems need stable state transitions.

Examples:

- a run can be pending, running, completed, failed, or cancelled
- an order can be created, submitted, filled, rejected, or pending approval
- execution can be research, sandbox, semi-auto, or live

Enums prevent random strings from spreading through the codebase.

Interview answer:

> I used explicit status enums because order and run states must be predictable.
> This helps testing, UI display, audit logs, and safety checks.

## Why We Added Audit Tables

In a trading platform, it is not enough to know the final result. We must know:

- who asked for it
- which tool ran
- what input was used
- what output came back
- what decision was made
- whether risk approved it
- what order was created

This is why the foundation includes:

- `chat_sessions`
- `chat_messages`
- `tool_calls`
- `audit_events`
- `approval_requests`
- `report_artifacts`

Interview answer:

> The platform is designed around auditability. Every important action should
> have an ID and a stored record, so that any answer from the chatbot can be
> traced back to backend facts.

## Why We Separate Execution Modes

The execution modes are:

- research
- sandbox
- semi-auto
- live

This prevents a research command from accidentally becoming a live trade.

Interview answer:

> I model execution mode explicitly because trading systems need strong safety
> boundaries. Research and sandbox are safe by default, semi-auto needs human
> approval, and live execution is isolated behind stricter guardrails.

## Why Tool Contracts Come Before The LLM

The LLM should not directly manipulate the database or broker. It should call
well-defined tools.

Example tools:

- list datasets
- get dataset detail
- run strategy
- evaluate risk
- create order intent
- inspect OpenAlgo sandbox
- generate report

Interview answer:

> We first build typed backend tools, then allow the LLM to orchestrate them.
> This makes the chatbot flexible without making it unsafe or hallucination-prone.

## Current Week 1 Tables

Production foundation tables:

- `schema_versions`
- `audit_events`
- `chat_sessions`
- `chat_messages`
- `tool_calls`
- `report_artifacts`
- `openalgo_snapshots`
- `strategy_definitions`
- `risk_limits`
- `approval_requests`
- `order_intents`

Reference workflow tables already available:

- `raw_file_registry`
- `data_catalog`
- `data_quality_reports`
- `options_ohlcv`
- `strategy_runs`
- `strategy_signals`
- `risk_decisions`
- `order_events`
- `trade_fills`
- `performance_summaries`

Older EMA-specific rows were migrated into explicitly named `legacy_ema_*`
tables so reference-demo schemas cannot be confused with production schemas.

## How To Explain Week 1 In One Minute

> In Week 1, we created the foundation for a production-grade conversational
> algo-trading platform. Instead of building a hardcoded chatbot, we defined the
> core backend entities, database initialization, app-state tables, audit tables,
> and initial tool contracts. This makes future LLM orchestration safe because
> the model will call deterministic backend tools and every result can be traced
> through stored data, tool calls, risk decisions, orders, and reports.
