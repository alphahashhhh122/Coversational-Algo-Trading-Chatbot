# 55-Day Resume-Ready Project Plan

> Superseded for day-to-day execution on June 10, 2026 by
> `45_DAY_BUILD_AND_DEFENSE_PLAN.md`. This file remains the original roadmap and
> broader feature reference.

## Deadline

- Start: June 7, 2026
- Resume-ready checkpoint: August 1, 2026
- Available time: approximately 55 days
- Broader project timeline: four months

The 55-day checkpoint does not replace the full 16-week plan. Its purpose is to
produce a substantial, demonstrable project early enough for internship resumes.

## Integrity And Ownership Rule

The project can be developed with AI assistance, but resume claims must remain
truthful. You are not expected to manually type every line. The goal is to make
you the effective technical owner of the system by ensuring that you:

- understand every major module
- make and record architecture decisions
- review AI-generated changes before accepting them
- can explain trade-offs
- can trace data end to end
- can debug common failures
- can make focused modifications with or without AI assistance
- can demonstrate working outputs

Every major module therefore has two deliverables:

1. Engineering deliverable
2. Interview ownership deliverable

## Resume-Ready Product Definition

By August 1, the repository should demonstrate a production-minded
conversational trading platform with:

- typed Python backend
- market-data ingestion and catalog
- generic strategy-run lifecycle
- signal persistence
- risk-management engine
- order-management lifecycle
- OpenAlgo read-only inspection
- OpenAlgo sandbox integration
- FastAPI endpoints
- LLM tool orchestration
- evaluator/guardrail step
- web dashboard
- performance analytics
- audit logging
- automated tests
- Docker/local setup
- architecture and demo documentation

Live trading is not required for the resume checkpoint. A safe and well-designed
sandbox/semi-auto architecture is more defensible than rushed live execution.

## What Makes The Project Strong For SWE Roles

- layered architecture
- typed domain models
- clean APIs
- database design
- testing and idempotence
- logging and auditability
- failure handling
- configuration and secrets handling
- deployment setup
- measurable performance
- clear documentation

## What Makes The Project Strong For Quant-Dev Roles

- market-data quality controls
- reproducible strategy runs
- prevention of look-ahead bias
- transaction costs and slippage
- risk sizing and exposure limits
- order-state transitions
- positions and P&L reconciliation
- drawdown and performance metrics
- OpenAlgo/broker integration
- deterministic calculations outside the LLM

## Daily Time Allocation

Recommended minimum on project days:

- 2.5-3 hours: implementation
- 45 minutes: testing/debugging/documentation
- 45 minutes: learn and explain that day's module
- 30 minutes: architecture and production trade-offs
- 30 minutes: finance or trading-system fundamentals

On busy academic days, preserve at least:

- 90 minutes implementation
- 30 minutes project explanation
- 20 minutes architecture or trading-system review

## Phase 1: Production Foundation

### Days 1-7

Engineering goals:

- finalize package structure
- finalize configuration
- finalize domain entities and enums
- establish schema versioning
- establish audit/event tables
- define service and tool boundaries
- add testing conventions
- add architecture decision records

Required proof:

- one-command database initialization
- schema inspection command
- tests for idempotent initialization
- documented package structure
- no live-trading path

Interview ownership:

- explain layered architecture
- explain domain versus infrastructure
- explain why tool contracts precede the LLM
- explain research/sandbox/semi-auto/live separation
- draw the architecture from memory

Current progress:

- package structure started
- typed models and enums started
- database initialization works
- core app-state tables exist
- three smoke tests pass

## Phase 2: Data Platform

### Days 8-14

Engineering goals:

- make ingestion generic rather than ZIP-specific
- add dataset/source schemas
- validate timestamp, OHLC, duplicates, gaps, schema, and coverage
- support catalog filtering by symbol/domain/interval/date
- add data lineage
- add dataset API/service tests
- create data-quality summary

Required proof:

- ingest at least one real dataset
- rerun ingestion without duplicate corruption
- retrieve dataset metadata through service/tool
- demonstrate data-quality warnings
- benchmark ingestion speed

Interview ownership:

- explain data catalog versus storage table
- explain source ID, dataset ID, and run ID
- explain idempotence
- explain why poor data invalidates a backtest
- explain and make a focused change to one validation rule

## Phase 3: Generic Strategy Runtime

### Days 15-21

Engineering goals:

- strategy interface/registry
- parameter schemas
- run lifecycle
- signal-event schema
- deterministic clock/data access
- transaction-cost and slippage hooks
- prevention of look-ahead bias
- at least two strategy plugins for proof of extensibility

Required proof:

- both strategies use the same runtime
- parameters validated before execution
- run can fail safely with stored error
- signals and metrics retrievable by run ID
- deterministic rerun gives the same output

Interview ownership:

- explain why strategy code is a plugin
- explain look-ahead bias
- explain reproducibility
- explain vectorized versus event-driven backtesting
- implement or modify one strategy without assistance

## Phase 4: Risk And Order Management

### Days 22-28

Engineering goals:

- risk-policy model and versioning
- order-intent model
- quantity sizing
- max order value
- max position/exposure
- max loss per trade
- max daily loss
- duplicate-order protection
- order state machine
- trades, positions, and funds reconciliation
- approval request for semi-auto mode

Required proof:

- approved-order example
- rejected-order example with reason
- order-state timeline
- position and P&L update
- test invalid state transitions
- audit record for each decision

Interview ownership:

- explain signal versus order intent
- explain pre-trade versus post-trade risk
- explain order, trade, position, and funds
- draw the order state machine
- diagnose why an order was rejected

## Phase 5: OpenAlgo Integration

### Days 29-35

Engineering goals:

- read-only OpenAlgo inspector
- document OpenAlgo services and tables
- read sandbox orders, trades, positions, and funds
- map platform order intent to OpenAlgo JSON
- sandbox submission adapter
- reconciliation snapshots
- timeout/retry/error handling
- keep credentials outside source control

Required proof:

- health/status check
- read-only snapshot persisted locally
- sandbox order lifecycle demonstrated
- platform/OpenAlgo IDs mapped
- safe failure when OpenAlgo is unavailable

Interview ownership:

- explain how JSON is created
- explain API/service/database flow
- explain auto versus semi-auto
- explain OpenAlgo sandbox tables
- explain reconciliation and idempotency

## Phase 6: API And Agentic Orchestration

### Days 36-42

Engineering goals:

- FastAPI application
- typed request/response models
- dataset, run, risk, order, performance, and OpenAlgo endpoints
- single orchestrator with structured tool calling
- intent routing
- tool-call logging
- evaluator/grounding check
- session storage
- safe clarification/error responses

Required proof:

- OpenAPI documentation
- natural-language request calls real tools
- final response cites run/tool IDs
- unsupported request is handled safely
- evaluator catches a deliberately unsupported answer

Interview ownership:

- explain why the LLM is not the trading engine
- explain orchestrator versus specialized tools
- explain when MCP is useful
- explain evaluator/guardrail responsibilities
- trace one chat request end to end

## Phase 7: Frontend, Analytics, And Demonstration

### Days 43-49

Engineering goals:

- usable web workspace
- chat view
- dataset catalog view
- run detail/timeline
- risk and order views
- performance dashboard
- OpenAlgo status view
- loading, empty, failure, and retry states
- responsive layout

Required proof:

- user can complete an end-to-end workflow from UI
- performance charts render from stored results
- no important data is visible only in console output
- desktop and mobile screenshots

Interview ownership:

- explain frontend/backend contract
- explain UI state and error handling
- explain how charts are derived
- demonstrate a workflow without notes

## Phase 8: Resume Release

### Days 50-55

Engineering goals:

- integration tests
- Docker/local setup
- CI checks
- structured logging
- performance measurements
- security/credential review
- final README
- architecture diagram
- database dictionary
- demo script and video
- tagged resume release

Required proof:

- clean setup on a fresh environment
- tests pass
- no credentials in repository
- demo works from documented commands
- known limitations documented
- measurable project statistics collected

Interview ownership:

- 30-second project pitch
- two-minute architecture explanation
- ten-minute deep dive
- answer failure, scale, safety, and trade-off questions
- implement or modify one small change during a mock interview, with documentation
  access allowed but without blindly pasting generated code

## Weekly Resume Checkpoints

### End Of Week 1

Claimable:

- production-style Python foundation
- typed domain models
- DuckDB schema initialization
- audit-first design

Do not yet claim:

- production-ready platform
- OpenAlgo integration
- agentic chatbot

### End Of Week 2

Claimable:

- data ingestion, catalog, quality, and lineage

### End Of Week 3

Claimable:

- extensible strategy runtime with reproducible runs

### End Of Week 4

Claimable:

- risk and order lifecycle with audit trail

### End Of Week 5

Claimable:

- OpenAlgo sandbox integration and reconciliation

### End Of Week 6

Claimable:

- FastAPI and grounded tool-calling orchestrator

### End Of Week 7

Claimable:

- complete web workflow and analytics

### Days 50-55

Claimable:

- tested, documented, deployable resume release

## Evidence Required Before Writing Resume Bullets

Never invent metrics. Record them from the repository and test output:

- number of API endpoints
- number of automated tests
- ingestion row count and runtime
- supported query/tool categories
- strategy count
- risk checks implemented
- order states supported
- response latency
- test coverage if measured
- number of database tables/domain entities
- Docker startup time

## Draft Resume Positioning

Project title:

**Conversational Algorithmic Trading and Risk Platform**

Draft bullets to finalize only after evidence exists:

- Built a typed Python/FastAPI trading platform that converts natural-language
  research and execution requests into audited data, strategy, risk, order, and
  analytics tool workflows.
- Designed deterministic strategy, pre-trade risk, and order-state engines with
  reproducible run IDs, transaction-cost modelling, position/P&L reconciliation,
  and persisted decision explanations.
- Integrated OpenAlgo sandbox APIs and state reconciliation for orders, trades,
  positions, and funds while isolating research, semi-auto, and live execution
  modes through explicit guardrails.
- Developed a web workspace for dataset discovery, run timelines, risk/order
  inspection, and performance visualization, supported by automated tests,
  structured logging, and reproducible local deployment.

These bullets must be edited with measured numbers by Day 55.

## Interview Question Bank

You must be able to answer:

### Architecture

- Why did you use layered architecture?
- Why FastAPI?
- Why DuckDB now and PostgreSQL later?
- Why direct tool orchestration before a heavy multi-agent framework?
- Where would MCP fit?

### Data

- How do you prevent duplicate ingestion?
- How do you identify missing candles?
- What is data lineage?
- Why does the catalog store metadata rather than all data?

### Strategy And Quant

- How do you prevent look-ahead bias?
- How are transaction costs and slippage modelled?
- How do you ensure reproducibility?
- How would you compare strategies fairly?

### Risk And Orders

- What is the difference between signal, order, trade, and position?
- How does risk modify or reject an order?
- How do you handle partial fills and duplicate submissions?
- How do you reconcile platform state with broker state?

### LLM And Agents

- Why should the LLM not calculate trading results?
- How are tool calls validated and logged?
- What does the evaluator check?
- How do you prevent hallucinated answers?

### Reliability

- What happens when OpenAlgo is unavailable?
- How would you scale the system?
- How are secrets stored?
- What tests give you confidence?
- What are the current limitations?

## AI-Assisted Technical Ownership Protocol

For every completed module:

1. State the requirement and acceptance criteria before generation.
2. Review the generated diff file by file.
3. Identify the public interfaces, database effects, failure modes, and tests.
4. Draw the data flow and explain it aloud without notes.
5. Answer at least five likely interview questions.
6. Make one focused modification after understanding the code.
7. Debug one deliberately introduced or naturally occurring failure.
8. Run the relevant tests and interpret their output.
9. Write a short design note in your own words.

You do not need to memorize syntax or type every line. You must understand why
the code exists, how it behaves, and how to verify or change it.

A module is not considered personally owned until all nine steps are complete.

## Project Interview Preparation

This roadmap covers only preparation directly related to defending this project:

- architecture whiteboarding
- database and data-flow explanation
- live debugging
- trade-off questions
- failure and scaling scenarios
- trading-system concepts used in the project
- professor demo rehearsal

## Immediate Next Milestone

Finish Phase 1 before expanding further:

- add architecture decision records
- improve schema idempotence tests
- define repository/service interfaces
- add audit service
- add tool-call logging service
- document configuration and secrets policy
- complete the Week 1 ownership review
