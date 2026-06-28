# Resume-Ready AI Workflow: July 25-30, 2026

## Positioning

This project should be presented as an internship project currently in progress:

**LLM-Orchestrated Algo-Trading Research and Risk Platform**

The strongest story is not "I built an EMA strategy." The story is:

> I am building an AI agentic trading assistant that converts natural-language
> trading research and execution requests into grounded, typed, auditable backend
> workflows for data discovery, backtesting, risk checks, sandbox order
> preparation, OpenAlgo integration, and performance analysis.

By July 25-30, the system must be demo-ready and interview-defensible. It does
not need full live trading. It must be clear that live execution is a future
controlled phase.

## What Can Be Claimed By July 25-30

Claim only what is working and verifiable.

Target claim:

> Built an LLM-orchestrated trading research assistant that uses typed tool
> calling, audit logs, deterministic backtesting, risk checks, and grounded
> responses over real NIFTY options data.

Safe July resume wording:

- Built an AI-assisted algo-trading research platform using Python, FastAPI,
  DuckDB, OpenAlgo sandbox workflows, and LLM tool orchestration.
- Implemented governed NIFTY market-data ingestion with validation, lineage,
  duplicate detection, and catalog discovery.
- Designed typed tools and audit logs to ground chatbot responses in stored
  dataset, strategy-run, risk, order, and performance records.
- Developed a reusable backtesting workflow with persisted signals, risk
  decisions, simulated/sandbox orders, trade fills, fees, drawdown, and
  performance summaries.

Final bullets must include verified numbers only:

- tests passing
- clean rows ingested
- tool count
- API endpoint count
- strategy count
- risk rule count
- order states supported
- demo latency if measured

## Current Baseline As Of June 16, 2026

Implemented:

- installable Python package
- DuckDB schema and initialization
- 15 passing tests
- structured JSON logging
- health and clean-foundation verification commands
- typed domain models and enums
- repository/service/tool architecture
- tool-call and audit persistence
- NIFTY options ingestion
- data-quality reporting
- dataset catalog
- reference EMA workflow storing signals, risk decisions, orders, trades, and
  performance

Not yet implemented:

- FastAPI app
- LLM orchestrator
- evaluator/guardrails
- generic strategy runtime
- production risk/order services
- OpenAlgo sandbox adapter
- frontend chat/demo UI
- CI/Docker/release packaging

## July 25-30 Demo Workflow

The final demo should show this exact flow:

```text
User opens web app
  -> asks: "What NIFTY datasets are available?"
  -> LLM orchestrator calls list_datasets tool
  -> response shows real dataset quality and row counts

User asks: "Run EMA 9/21 on the latest clean dataset"
  -> orchestrator selects dataset and calls run_backtest
  -> backend runs deterministic strategy
  -> signals, trades, risk, orders, and performance are stored

User asks: "Explain the signal and risk/order timeline"
  -> orchestrator calls run_timeline tool
  -> UI shows signal -> risk decision -> order -> fill -> P&L

User asks: "Prepare a sandbox order"
  -> backend validates order intent
  -> risk service approves/rejects
  -> OpenAlgo sandbox adapter prepares/submits safely
  -> result is stored with internal and external IDs

User asks: "Summarize performance"
  -> performance tool returns P&L, fees, drawdown, trades, win/loss
  -> evaluator checks response is grounded in tool outputs
  -> UI shows final answer with dataset/run/tool/audit IDs
```

## AI-First Architecture To Build

```text
Web Chat UI
  -> FastAPI /chat endpoint
  -> LLM Orchestrator
  -> Tool Registry
  -> Typed Tools
  -> Backend Services
  -> Repositories / OpenAlgo Adapter
  -> DuckDB
  -> Evaluator / Guardrails
  -> Grounded Response with Evidence IDs
```

AI components:

- `Orchestrator`: interprets intent and selects tools.
- `ToolRegistry`: exposes allowed tools and schemas.
- `Typed Tools`: controlled capabilities, not arbitrary SQL/broker access.
- `ConversationState`: remembers selected dataset, run ID, and last outputs.
- `Evaluator`: checks whether final answers are supported by actual tool
  outputs and whether the request is safe.
- `Audit`: stores every tool call and important domain event.

Backend components:

- `CatalogService`
- `BacktestService`
- `RiskService`
- `OrderService`
- `OpenAlgoService`
- `PerformanceService`
- `ConversationService`

## 39-Day Execution Plan

### Phase 1: AI Architecture And API Baseline

Dates: June 16-20

Deliverables:

- final target architecture document
- FastAPI app
- Pydantic request/response models
- `/health`, `/datasets`, `/chat` starter endpoints
- tool registry with catalog tools
- chat endpoint that can call deterministic tools
- tool-call logging integrated with chat

Interview focus:

- why FastAPI
- why Pydantic
- why typed tools
- why LLM is orchestrator, not trading engine
- how responses are grounded

### Phase 2: Generic Strategy Runtime

Dates: June 21-27

Deliverables:

- strategy interface and registry
- parameter validation
- generic run lifecycle
- EMA strategy migrated from reference file
- second simple strategy for extensibility proof
- deterministic rerun test
- performance summary retrieval tool

Interview focus:

- look-ahead bias
- reproducibility
- transaction costs and slippage
- strategy plugin design
- why calculations are deterministic

### Phase 3: Risk And Order Lifecycle

Dates: June 28-July 4

Deliverables:

- order intent model
- versioned risk policy
- max quantity, notional, exposure, stop-loss, daily-loss checks
- order state machine
- approved and rejected examples
- order timeline tool
- position/P&L reconciliation baseline

Interview focus:

- signal vs order intent vs order vs trade vs position
- pre-trade and post-trade risk
- duplicate-order prevention
- invalid order transitions
- reconciliation

### Phase 4: OpenAlgo Sandbox Adapter

Dates: July 5-10

Deliverables:

- read-only OpenAlgo status/snapshot adapter
- sandbox order JSON mapping
- internal/external ID mapping
- timeout and unavailable-service handling
- mocked tests for success/failure
- no live-trading path by default

Interview focus:

- who converts order to JSON
- sandbox vs semi-auto vs live
- retries and idempotency
- broker state reconciliation
- credential safety

### Phase 5: LLM Orchestrator And Evaluator

Dates: July 11-16

Deliverables:

- real LLM tool orchestration or deterministic fallback for demos
- tool schemas for dataset, backtest, risk/order, OpenAlgo, performance
- evaluator that checks tool evidence
- unsafe/unsupported request handling
- conversation memory with selected dataset/run ID
- audit panel data available

Interview focus:

- hallucination prevention
- evaluator responsibilities
- Agents SDK vs simple orchestrator
- when MCP is useful
- prompt/tool schema design

### Phase 6: Frontend Demo Workspace

Dates: July 17-21

Deliverables:

- web chat interface
- dataset card/view
- strategy run detail
- signal/risk/order timeline
- performance dashboard
- tool/audit trace panel
- failure/empty/loading states

Interview focus:

- frontend/backend contract
- how charts are derived
- UI state handling
- showing evidence IDs

### Phase 7: Release Hardening

Dates: July 22-25

Deliverables:

- integration tests
- README demo script
- architecture diagram
- schema dictionary
- resume metrics
- screenshots/video
- limitations and future work
- mock grilling pack

Interview focus:

- 30-second pitch
- 2-minute architecture
- 10-minute deep dive
- failure/scaling/security questions
- one live modification/debugging story

### Buffer

Dates: July 26-30

Use only for:

- fixing demo blockers
- tightening resume bullets
- rehearsing interviews
- improving evaluator/frontend polish
- recording final demo

## Grilling Pack Required Before Resume Submission

For every feature on the resume, prepare:

- what it does
- why it exists
- tech choice
- alternative considered
- tables/IDs involved
- failure modes
- tests proving it
- scaling limitation
- one debugging story

Minimum question categories:

- AI/LLM orchestration
- FastAPI and Pydantic
- DuckDB vs PostgreSQL
- data validation and lineage
- backtesting correctness
- look-ahead bias
- transaction costs and slippage
- risk/order lifecycle
- OpenAlgo sandbox integration
- audit logs and tool traces
- security/secrets
- scaling

## Three-To-Four-Month Full Production Path

After July release:

1. Replace local single-user assumptions with authentication and role-based
   authorization.
2. Move transactional state to PostgreSQL.
3. Add durable background jobs and queue-backed workers.
4. Add portfolio-level risk and richer reconciliation.
5. Add broader data domains: fundamentals, news, filings, macro, alternative
   data.
6. Add more strategy plugins and benchmark comparison.
7. Harden OpenAlgo semi-auto execution with approvals and kill switch.
8. Add monitoring, metrics, alerting, CI/CD, Docker, and deployment.
9. Add richer evaluator and policy guardrails.
10. Prepare professor-grade production architecture report.

## Non-Negotiables

- Do not claim live trading is complete.
- Do not claim multi-user production deployment is complete.
- Do not let the LLM invent trading results.
- Do not put credentials in source code, logs, screenshots, or audit payloads.
- Do not add resume numbers unless verified by commands or tests.
- Do not overbuild agent complexity before the typed-tool workflow works.

