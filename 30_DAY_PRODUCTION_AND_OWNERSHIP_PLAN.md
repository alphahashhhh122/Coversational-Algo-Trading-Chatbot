# 30-Day Production And Interview Ownership Plan

## Final Target

Build and personally own a production-grade **algorithmic-trading research and
controlled-execution platform** that is credible for an IIM Calcutta review and
for software, AI, and quant-development interviews.

The 30-day target is not unrestricted live trading or internet-scale deployment.
It is a real, authenticated, auditable, reproducible research platform with a
safe OpenAlgo analyzer workflow and a documented path to multi-node production.

## What Makes The Project Resume-Strong

The project must prove four kinds of engineering together:

1. AI engineering: grounded LLM orchestration, strict tools, role-filtered
   capabilities, evaluator checks, retrieval provenance, and audit traces.
2. Trading systems: data quality, deterministic strategies, pre-trade risk,
   order state, idempotency, reconciliation, costs, and performance.
3. Backend engineering: typed APIs, authentication, persistent jobs, schema
   migrations, failure handling, observability, testing, and deployment.
4. Quant research discipline: reproducible experiments, benchmarks,
   out-of-sample evaluation, parameter sensitivity, and honest limitations.

## Scope Boundary

### Must Be Working

- authenticated multi-role workspace
- governed market-data ingestion and catalog
- generic strategy plugins
- reproducible experiment manifests
- costs, slippage, risk-adjusted metrics, and comparisons
- signal to risk to order to fill evidence
- persistent jobs and operational readiness
- grounded conversational tools
- OpenAlgo read/analyzer integration after credentials are supplied
- professor report and repeatable demo

### Must Be Designed And Defensible

- PostgreSQL transactional-store migration
- distributed worker and Redis rate-limit migration
- object storage/Parquet market-data architecture
- secrets manager, TLS, backups, tracing, and alerting
- portfolio-scale and multi-broker evolution

### Must Not Be Claimed

- profitable future performance
- unrestricted live trading
- horizontally scaled deployment before it is actually deployed
- real OpenAlgo verification before credentials are tested

## Progressive Build

### Days 1-4: Reproducible Quant Experiments

Build:

- immutable experiment manifest and hash
- source checksum, strategy version, engine version, and parameter capture
- transaction-cost and slippage assumptions
- win rate, profit factor, expectancy, Sharpe, Sortino, and recovery factor
- report and UI exposure

Own:

- why reproducibility matters
- limitations of trade-level Sharpe
- why fees and slippage change conclusions
- how identical manifests can produce independently audited runs

Exit gate:

- deterministic reruns share a manifest hash
- each run has a unique run ID and stored evidence
- metrics are tested and visible

### Days 5-9: Robustness And Out-Of-Sample Validation

Build:

- chronological train/test split
- in-sample and out-of-sample child runs
- buy-and-hold benchmark
- parameter sensitivity grid for selected strategies
- robustness verdict with explicit rules and warnings

Own:

- overfitting, leakage, look-ahead bias, and regime dependence
- why a backtest is evidence, not validation of future returns
- walk-forward validation versus one train/test split

Exit gate:

- one EMA experiment includes in-sample, out-of-sample, and benchmark evidence
- weak strategies are reported honestly rather than hidden

### Days 10-13: Portfolio And Risk Depth

Build:

- persisted positions and cash ledger for research/sandbox
- gross and net exposure
- symbol and strategy concentration limits
- portfolio drawdown and daily-loss controls
- kill-switch state and approval policy

Own:

- pre-trade versus post-trade risk
- order risk versus portfolio risk
- why exits normally bypass entry restrictions
- race conditions and atomic risk reservations

Exit gate:

- approved, resized, rejected, and kill-switch examples are demonstrable

### Days 14-17: OpenAlgo Reliability

Build after credentials:

- authenticated funds, positions, orders, and trades snapshots
- analyzer-mode proof
- one approved analyzer order
- reconciliation and mismatch report
- timeout and uncertain-submission recovery exercise

Own:

- internal versus broker source of truth
- idempotency and ambiguous network failure
- why submission is unavailable to the LLM
- semi-auto versus automatic execution

Exit gate:

- credentials never appear in code, logs, screenshots, or artifacts
- real analyzer evidence is stored and documented

### Days 18-21: AI Evaluation And Query Coverage

Build:

- representative query evaluation dataset
- intent/tool selection accuracy checks
- grounding and unsupported-number checks
- role-bypass and prompt-injection tests
- latency and tool-failure measurements
- clarification behavior for missing identifiers

Own:

- why an evaluator is not proof of correctness
- deterministic guardrails versus model judgment
- direct tools versus Agents SDK, LangGraph, and MCP
- when specialist agents become justified

Exit gate:

- measurable query coverage and failure cases
- no LLM path can approve or submit an order

### Days 22-25: Production Persistence And Operations

Build:

- PostgreSQL-ready repository contracts and migration design
- backup and restore procedure for current deployment
- retention policy for chats, tools, snapshots, and artifacts
- structured metrics dashboard and alert thresholds
- deployment smoke test and failure runbook

Own:

- DuckDB concurrency limitations
- transactional versus analytical storage
- queue semantics, retries, dead letters, and distributed locks
- recovery point and recovery time objectives

Exit gate:

- current single-node boundary is explicit
- scale-up architecture has concrete schemas and migration steps

### Days 26-28: Product And Professor Polish

Build:

- strategy experiment view
- robustness and comparison visualization
- report viewer
- OpenAlgo/reconciliation view
- accessible desktop and mobile verification
- ten-minute professor demo with failure recovery

Own:

- full request and database workflow without notes
- what is stored in every major table and why
- one failure diagnosis from logs to database state

### Days 29-30: Resume Release And Grilling

Produce:

- tagged resume release
- architecture and data-flow diagrams
- verified metrics sheet
- 30-second, two-minute, and ten-minute explanations
- AI, backend, quant, security, database, and scale question bank
- two debugging stories and one design-change exercise

Final ownership gate:

- no resume claim without a command, test, stored artifact, and explanation
- score at least 16/20 on each module defense packet
- demonstrate one live modification without AI explanation support

## Interview Study Rhythm

Use 90-120 minutes daily:

- 25 minutes: understand that day’s architecture and trade-off
- 35 minutes: inspect stored evidence and important code paths
- 20 minutes: answer interviewer questions aloud
- 20 minutes: modify, test, or debug one behavior
- 10 minutes: update the personal defense sheet

Do not memorize files line by line. Own responsibilities, data flow, invariants,
failure modes, tests, and trade-offs.

## Target Resume Positioning

**LLM-Orchestrated Algorithmic Trading Research Platform**

Target bullet shape after final verification:

- Built an authenticated, LLM-orchestrated trading research platform using
  FastAPI, Pydantic, DuckDB, and OpenAlgo analyzer workflows, with role-filtered
  typed tools, grounding checks, and persisted audit evidence.
- Designed reproducible strategy experiments over governed NIFTY options data,
  capturing source checksums, parameters, engine versions, costs, risk-adjusted
  metrics, and signal-to-fill timelines.
- Implemented versioned pre-trade risk, idempotent order state management,
  approval-gated broker submission, reconciliation, background jobs, metrics,
  CI, and containerized single-node deployment.

Numbers are added only from the final evidence tracker.
