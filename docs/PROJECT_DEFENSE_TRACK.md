# Project Defense And Professor Preparation

This preparation is limited to understanding and defending the project. General
DSA or unrelated placement preparation is outside this plan.

## Completion Rule

Interview preparation is not postponed until the final week. Each major module
must have a completed `RECRUITER_DEFENSE_TEMPLATE.md` packet before it is treated
as personally owned or used in a resume claim.

The expected interview depth is:

- explain the product problem without code
- draw and trace the architecture
- justify technology and design choices
- discuss alternatives and trade-offs
- explain storage, IDs, and failure behavior
- cite tests and measured evidence
- handle a changed requirement or debugging follow-up

## Weekly Minimum

- one architecture rehearsal
- one database/data-flow explanation
- one live debugging or focused modification exercise
- one trading-system concept review
- one professor-style demonstration rehearsal

## Foundation

You should explain:

- layered architecture
- domain versus infrastructure
- configuration and secrets
- database schema and audit trail
- why deterministic tools come before the LLM

## Data Platform

You should explain:

- ingestion and validation
- data catalog
- lineage and source IDs
- idempotence
- missing, duplicate, and invalid data

## Strategy Runtime

You should explain:

- generic strategy interfaces
- parameter validation
- deterministic runs
- look-ahead bias
- transaction costs and slippage
- stored signals and metrics

## Risk And Orders

You should explain:

- signal versus order intent
- pre-trade risk checks
- approval and rejection reasons
- order state machine
- trades, positions, funds, and P&L
- reconciliation and duplicate protection

## OpenAlgo

You should explain:

- authentication and API flow
- order JSON creation
- analyzer and sandbox modes
- sandbox orders, trades, positions, and funds
- auto versus semi-auto
- platform/OpenAlgo state reconciliation

## Agentic Layer

You should explain:

- why the LLM is an orchestrator
- deterministic function tools
- Agents SDK manager pattern
- specialist agents versus handoffs
- guardrails
- evaluator
- tracing versus persistent audit logs
- optional MCP exposure

## Production Readiness

You should explain:

- error handling
- retries and timeouts
- idempotency
- logging and monitoring
- testing strategy
- credentials and safety boundaries
- scaling from DuckDB to PostgreSQL/analytical storage

## Required Presentations

Prepare:

- 30-second project pitch
- 2-minute product summary
- 10-minute architecture walkthrough
- database schema walkthrough
- one code module deep dive
- one debugging story
- one design trade-off
- current limitations and future work
