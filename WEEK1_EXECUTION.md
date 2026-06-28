# Week 1 Execution Plan

## Current Status

Week 1 is in progress. Rapid AI generation does not count as completed ownership.

### Generated Scaffold

- initial backend package structure
- initial configuration and secret redaction
- initial domain models and enums
- initial schema and version marker
- initial repository interfaces and DuckDB adapters
- initial audit and tool-call services
- foundation health command
- draft architecture decision records
- twelve initial automated tests

### Technically Verified So Far

- modules compile
- initial tests pass
- database initialization is repeatable for the current schema
- tool success and failure paths are persisted
- configuration output does not reveal secret values
- live trading is disabled by default

### Not Yet Production-Complete

- repository coverage is incomplete
- configuration validation is incomplete
- structured operational logging is missing
- project packaging and dependency locking are missing
- Git and CI baselines are missing
- broader failure, concurrency, and migration tests are missing
- architecture decisions require review against implementation

### Not Yet Personally Owned

- service/repository/infrastructure concepts are still being learned
- complete code walkthrough has not happened
- focused modification has not happened
- deliberate debugging exercise has not happened
- interview questions have not been answered without notes

Week 1 closes only when the engineering, verification, and personal-ownership
tracks are all complete.

## Seven-Session Learning Cadence

Each session is approximately 1-2 hours. A session is not complete merely because
code was generated.

### Session 1: System Goal And Layered Architecture

- understand chatbot, tools, services, repositories, and infrastructure
- trace one catalog request end to end
- inspect the corresponding files
- explain the flow without notes

### Session 2: Domain Models And State

- understand entities versus database rows
- review execution, run, order, and tool statuses
- inspect model validation gaps
- make one small enum/model change and test it

### Session 3: Database And Repositories

- understand schema initialization and idempotence
- inspect SQL tables and repository adapters
- review completed `CatalogService` to `DatasetRepository` refactor
- test with DuckDB and a fake repository

### Session 4: Services, Tools, And Audit

- inspect `ToolExecutionService` line by line
- distinguish `tool_calls` from `audit_events`
- run success and failure paths
- debug one deliberately failing tool

### Session 5: Configuration, Security, And Logging

- inspect environment configuration
- verify secrets are redacted
- add validation and structured operational logging
- explain live-trading safety boundaries

### Session 6: Packaging, Testing, And Developer Experience

- add project packaging and dependency declarations
- review the centralized schema initialization
- expand tests for failures and replacement adapters
- add clean local setup commands

### Session 7: Review And Ownership Sign-Off

- initialize and inspect the project from scratch
- draw the architecture and request flow
- complete the focused modification
- answer project-defense questions
- identify Week 2 dependencies
- record verified resume evidence

## Mandatory Planning Gate

Before starting any week, sprint, module, or major feature, run this check.

### 1. Product Completeness

Questions:

- Does this plan move the real production platform forward?
- Is it reusable beyond one demo?
- Does it avoid hardcoded chatbot behavior?
- Does it support future data, strategy, broker, and reporting extensions?

Pass condition:

- The work improves the core platform, not only a temporary proof-of-concept.

### 2. Architecture Completeness

Questions:

- Which layer does this work belong to?
- Does it keep domain logic separate from infrastructure?
- Are tool contracts explicit?
- Are storage responsibilities clear?
- Can the orchestrator call this later without knowing internal implementation?

Pass condition:

- The module fits cleanly into the architecture and does not create hidden coupling.

### 3. Data And Audit Completeness

Questions:

- What data is created, read, updated, or deleted?
- Which table stores it?
- Is there a run ID, source ID, session ID, or audit ID?
- Can we explain where the result came from?
- Can the professor inspect it later?

Pass condition:

- Important actions are traceable and reproducible.

### 4. Safety And Risk Completeness

Questions:

- Could this accidentally affect live trading?
- Are sandbox, semi-auto, and live paths clearly separated?
- Are approvals needed before execution?
- Are failures stored and explainable?

Pass condition:

- Research and sandbox are safe by default; live execution cannot happen casually.

### 5. Testing Completeness

Questions:

- What is the minimum smoke test?
- What edge cases can break this?
- Is there at least one automated check?
- Can we rerun the module without corrupting state?

Pass condition:

- The feature has verification, even if small at first.

### 6. Interview Understanding Completeness

Questions:

- Can I explain why this module exists?
- Can I explain how data flows through it?
- Can I explain what would change in production?
- Can I answer where things are stored?
- Can I explain trade-offs and alternatives?

Pass condition:

- The user can defend the design in an interview or professor review.

### 7. Demo Value Completeness

Questions:

- What can we show after this work?
- Is the demo grounded in real stored outputs?
- Can the explanation be generated from backend facts?

Pass condition:

- The work creates something visible, inspectable, or explainable.

### 8. No-Compromise Final Check

Do not start implementation until these are true:

- scope is clear
- deliverable is clear
- storage impact is clear
- tests are clear
- teaching points are clear
- production relevance is clear

## Goal

Create the production foundation for the conversational algo-trading platform.

By the end of Week 1, the project should have a clean backend structure, typed
domain models, database initialization, configuration, and a basic smoke test.

## Day 1: Repo And Backend Skeleton

Planning gate focus:

- architecture completeness
- production relevance
- interview explanation of package structure

Tasks:

- create production backend package structure
- separate app/domain/infrastructure/tooling concerns
- add configuration module
- add app entrypoint placeholder
- update README run instructions

Done when:

- imports work
- package structure is clear
- no old demo module is required for app startup

## Day 2: Domain Models

Planning gate focus:

- product completeness
- data and audit completeness
- interview explanation of core entities

Tasks:

- define dataset models
- define strategy models
- define signal models
- define risk models
- define order/trade models
- define chat/tool-call/report models

Done when:

- core platform objects have typed Python representations
- models are independent of DuckDB/OpenAlgo implementation details

## Day 3: Database Baseline

Planning gate focus:

- storage clarity
- auditability
- repeatable initialization

Tasks:

- create database schema initializer
- create app-state tables
- create research-data tables
- create audit tables
- add command to initialize DB

Done when:

- one command creates the local development database
- schema can be re-run safely

## Day 4: Data Catalog Service

Planning gate focus:

- data discovery
- reusable tool contract
- professor-friendly explanation of data availability

Tasks:

- create catalog service
- list datasets
- fetch dataset detail
- expose quality status structure

Done when:

- backend can answer "what data do we have?"

## Day 5: Tool Contracts

Planning gate focus:

- orchestrator readiness
- typed inputs and outputs
- no hidden LLM-only behavior

Tasks:

- define tool request/response schemas
- create first direct tools:
  - list datasets
  - get dataset detail
  - create report placeholder
- log tool calls

Done when:

- tool layer exists without LLM dependency

## Day 6: Tests And Smoke Checks

Planning gate focus:

- minimum automated proof
- idempotence
- failure visibility

Tasks:

- add test runner
- add schema initialization test
- add catalog service test
- add model serialization test

Done when:

- smoke test passes locally

## Day 7: Documentation And Review

Planning gate focus:

- interview readiness
- demo readiness
- Week 2 dependency clarity

Tasks:

- update README
- write architecture decision record
- list Week 2 tasks
- review gaps

Done when:

- project can be explained from repo structure alone

## Week 1 Deliverables

- production-style backend folder structure
- typed domain models
- database initializer
- configuration module
- basic catalog service
- direct tool contracts
- initial smoke tests
- updated README

## Week 1 Interview Understanding

By the end of Week 1, you should be able to explain:

- why we use layered architecture
- why domain models are separate from database code
- why tool contracts exist before the chatbot
- why audit tables matter in trading systems
- why OpenAlgo integration should be introduced through safe stages
- how a natural language request will eventually become tool calls
- why we start with deterministic backend capabilities before LLM orchestration

## Week 1 No-Compromise Review

Before marking Week 1 complete, verify:

- database initializes from one command
- smoke tests pass
- README run commands work
- all core entities have typed models
- tables exist for chat, tools, reports, datasets, strategy workflow, and OpenAlgo snapshots
- no live trading path exists
- every planned Week 2 task has a dependency identified
