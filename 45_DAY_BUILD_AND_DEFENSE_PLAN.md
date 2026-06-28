# 45-Day Resume Build And Interview Defense Plan

## Deadline And Goal

- Reset date: June 10, 2026
- Resume-ready release: July 25, 2026
- Broader professor/production target: September-October 2026

By July 25, the project must be a working, demonstrable conversational
algorithmic-trading platform. It does not need unrestricted live trading.
Research, backtesting, risk, sandbox execution, grounded chat, auditability, and
performance visualization must work end to end.

The release is not complete unless the user can defend its important decisions
in an interview without opening the source code.

## Two Synchronized Tracks

Every day advances both tracks.

### Engineering Track

- implement a real platform capability
- store real outputs
- test success and failure paths
- produce visible demo evidence
- keep research and sandbox safe by default

### Interview Ownership Track

- explain the problem being solved
- explain the architecture and data flow
- justify the technology and design choices
- compare at least one alternative
- explain failure handling, testing, security, and scaling
- make one focused change or diagnose one failure

AI may generate code, but a module is not resume-owned until both tracks are
complete.

## Definition Of Owned

For each major module, produce a one-page defense packet containing:

1. Problem: what user or system problem does this module solve?
2. Responsibility: what does it own and what does it deliberately not own?
3. Flow: inputs, outputs, calls, tables, IDs, and side effects.
4. Choice: why this design and technology were used here.
5. Alternative: what else was considered and why it was not selected now.
6. Trade-offs: benefits, costs, and current limitations.
7. Failure modes: what can fail and how the system responds.
8. Verification: tests, commands, metrics, and demo evidence.
9. Production evolution: what changes under scale or live execution.
10. Ownership proof: one modification and one debugging exercise.

## Recruiter Question Model

Interview preparation will use five levels.

### Level 1: Product

- What problem does this platform solve?
- Who uses it?
- What can the chatbot actually do?
- What is working today?
- What is intentionally out of scope?

### Level 2: Architecture

- Draw the full architecture.
- Trace one user request end to end.
- Why separate tools, services, repositories, and infrastructure?
- Why is the LLM an orchestrator rather than the trading engine?
- Where are state and audit evidence stored?

### Level 3: Module Depth

- Why was this technology selected?
- What is the data model?
- What happens on success and failure?
- How are idempotency and reproducibility handled?
- How is the module tested?

### Level 4: Trade-Off And Scale

- What would break with 100 users or much larger data?
- Why DuckDB now, and when would PostgreSQL or another store be needed?
- What are the latency and concurrency constraints?
- What would become asynchronous?
- What did you simplify for the current release?

### Level 5: Challenge

- Change a requirement.
- Diagnose a failed request.
- Add a validation or risk rule.
- Explain an unexpected database record.
- Defend or revise an architecture decision.

## Architecture Story To Defend

```text
User / Web UI
  -> FastAPI
  -> LLM Orchestrator
  -> Typed Deterministic Tool
  -> Application Service
  -> Repository Contract
  -> DuckDB or OpenAlgo Adapter
  -> Stored Result + Tool Call + Audit Event
  -> Evaluator / Grounding Check
  -> Response with evidence IDs
```

The LLM selects and sequences capabilities. Deterministic code performs data
retrieval, calculations, risk checks, order operations, and performance
measurement.

## Daily Working Method

Recommended four-hour project session:

- 2 hours: implementation
- 45 minutes: tests, debugging, and evidence
- 45 minutes: architecture and interview defense
- 30 minutes: demo rehearsal or focused modification

For every session:

1. Define acceptance criteria.
2. Implement the smallest complete capability.
3. Run tests and inspect stored state.
4. Update evidence metrics.
5. Answer five recruiter questions aloud.
6. Record one weak answer for revision.

## Phase 1: Foundation Ownership And Baseline

### Days 1-3: June 10-12

Engineering:

- finish and understand the three current test files
- add packaging/dependency declarations
- add structured application logging
- verify fresh DB initialization and health checks
- remove documentation/status inconsistencies

Defense:

- 30-second and two-minute project pitch
- draw current architecture without notes
- explain Python, DuckDB, layered design, typed domain models, and audit tables
- complete one failure-debugging exercise

Exit evidence:

- all tests pass
- fresh initialization works
- foundation defense packet complete
- Week 1 ownership checkpoint signed off

## Phase 2: General Data Platform

### Days 4-9: June 13-18

Engineering:

- refactor ZIP-specific ingestion behind a generic ingestion contract
- strengthen schema, timestamp, OHLC, duplicate, gap, and coverage validation
- add idempotent source/dataset registration
- add catalog filters and dataset profiling
- expose ingestion and catalog through service, tool, and API-ready contracts

Defense:

- data catalog versus data storage
- source ID versus dataset ID versus run ID
- idempotency and lineage
- why bad data invalidates a backtest
- DuckDB strengths and limitations

Exit evidence:

- real NIFTY dataset ingested repeatably
- quality report and lineage visible
- ingestion benchmark recorded
- data-platform defense packet complete

## Phase 3: Generic Strategy Runtime

### Days 10-16: June 19-25

Engineering:

- replace the one-file EMA demo with a reusable strategy interface and registry
- add parameter validation and run lifecycle
- add deterministic data access and clock boundaries
- model transaction costs and slippage
- prevent look-ahead bias
- run EMA crossover plus one second strategy through the same engine

Defense:

- vectorized versus event-driven backtesting
- reproducibility
- look-ahead and survivorship bias
- strategy plugins and parameter schemas
- why calculations stay outside the LLM

Exit evidence:

- two strategies use one runtime
- deterministic rerun test
- failed run stored safely
- strategy-runtime defense packet complete

## Phase 4: Risk And Order Lifecycle

### Days 17-23: June 26-July 2

Engineering:

- separate signal, order intent, risk decision, order, fill, position, and funds
- add versioned risk policies
- implement size, notional, exposure, per-trade loss, and daily-loss checks
- implement order state machine and invalid-transition protection
- add duplicate-submission protection
- reconcile trades, positions, funds, and P&L
- add semi-auto approval records

Defense:

- pre-trade versus post-trade risk
- why a signal cannot directly place an order
- order versus trade versus position
- partial fills, retries, and idempotency
- state-machine design

Exit evidence:

- approved and rejected examples
- order timeline and P&L update
- invalid transition test
- risk/order defense packet complete

## Phase 5: OpenAlgo Sandbox Integration

### Days 24-29: July 3-8

Engineering:

- inspect and document relevant OpenAlgo APIs and storage
- add read-only snapshot adapter
- map platform order intent to validated OpenAlgo JSON
- add sandbox submission adapter
- persist external/internal ID mapping
- add timeout, retry, unavailable-service, and reconciliation handling
- keep credentials outside source control

Defense:

- OpenAlgo authentication and request flow
- who creates order JSON
- sandbox orders, trades, positions, and funds
- auto versus semi-auto
- source of truth and reconciliation

Exit evidence:

- read-only snapshot persisted
- sandbox order lifecycle demonstrated
- OpenAlgo-offline failure demonstrated
- OpenAlgo defense packet complete

## Phase 6: API And Grounded Agent Orchestration

### Days 30-35: July 9-14

Engineering:

- build FastAPI endpoints with typed request/response models
- implement one orchestrator using structured tool calling
- add tools for catalog, strategy, risk, orders, performance, and OpenAlgo
- persist sessions and tool calls
- add evaluator/grounding checks
- return evidence IDs and safe clarification responses

Defense:

- FastAPI choice and alternatives
- orchestrator versus specialist agents
- when MCP helps and when it adds unnecessary complexity
- hallucination prevention
- tracing versus persistent audit
- evaluator responsibilities and limitations

Exit evidence:

- natural-language request invokes real tools
- unsupported answer is blocked
- API documentation works
- agent/API defense packet complete

## Phase 7: Web Demo And Performance Analytics

### Days 36-40: July 15-19

Engineering:

- build chat workspace
- build catalog and run views
- build signal, risk, order, and audit timeline
- build performance charts and summary
- build OpenAlgo status/sandbox view
- handle loading, empty, failure, and retry states

Defense:

- frontend/backend contract
- UI state management
- chart derivation from stored facts
- error display and retry behavior
- accessibility and responsive behavior

Exit evidence:

- complete workflow visible without terminal inspection
- desktop/mobile screenshots
- frontend defense packet complete

## Phase 8: Release And Interview Readiness

### Days 41-45: July 20-25

Engineering:

- add integration tests and CI
- add Docker or reproducible local setup
- perform credentials/security review
- measure test count, ingestion time, tool count, endpoints, and latency
- finalize README, architecture diagram, schema dictionary, demo script, and video
- tag the resume release

Defense:

- rehearse 30-second, two-minute, and ten-minute explanations
- run architecture, backend, quant, LLM, failure, and scale mock rounds
- practice one focused change and one debugging task
- prepare honest limitations and next steps

Exit evidence:

- fresh setup and end-to-end demo pass
- resume bullets contain only verified numbers
- no important recruiter question lacks a defensible answer

## Weekly Review Gate

At the end of each phase, score every category from 0-2:

- `0`: cannot explain
- `1`: can explain with notes
- `2`: can explain clearly without notes and answer follow-ups

Categories:

- product purpose
- architecture
- data flow and storage
- technology choice
- alternatives and trade-offs
- failure handling
- tests and evidence
- security and safety
- scaling
- live modification/debugging

A phase is owned only with no zeroes and a minimum score of 16/20.

## Immediate Next Step

Do not begin the next large feature yet. First close Foundation Ownership:

1. Understand what the three test files prove.
2. Run and interpret the full test suite.
3. Produce the current architecture diagram.
4. Complete the foundation recruiter question set.
5. Perform one focused modification and one failure-debugging exercise.

