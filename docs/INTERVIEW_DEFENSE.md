# Interview Defense

## One-Minute Pitch

This is a local conversational algo-trading research platform. The LLM-style
orchestrator does not calculate trades or invent facts. It chooses typed backend
tools. The backend owns data cataloging, strategy execution, risk checks, order
state, performance, OpenAlgo readiness, retrieval, and reports. Every important
result is stored with evidence IDs.

## Architecture

- Frontend: local workspace for chat, operator console, runs, data, OpenAlgo,
  approvals, and operations.
- FastAPI: typed HTTP boundary and OpenAPI contract.
- Tool registry: the only functions the orchestrator can call.
- Services: business rules such as backtesting, risk, order state, readiness,
  news, RAG, and reports.
- DuckDB: local evidence store for operator workflows and review evidence.
- OpenAlgo client: external broker/analyzer boundary, never used for fake local
  backtest reflection.

## Why Not Let The LLM Query The DB Directly?

Because trading workflows need validation, auditability, and role control. A
typed tool can validate arguments, log tool calls, apply risk rules, and return
evidence. Direct SQL from an LLM would be harder to secure, test, and defend.

## OpenAlgo vs IIMC Backtest

IIMC backtests are local historical research artifacts. They store signals,
risk, orders, fills, and performance in the platform database and show
`visible_in_openalgo=false`.

OpenAlgo reflects only OpenAlgo-routed analyzer/paper/live activity such as
account snapshots, analyzer mode, orderbook, tradebook, positionbook, funds, and
approved sandbox submissions.

## Safety Model

- live trading disabled by default
- no synthetic market-data fallback
- no fake news
- missing keys return structured safe failures
- analyzer submission requires OpenAlgo analyzer mode
- human approval is separate from chat orchestration
- failed backtests return `no_synthetic_fallback`

## Generic Asset Readiness

The platform does not claim all assets are verified. It supports multi-asset
readiness validation. For each request it checks symbol, exchange, asset class,
local dataset coverage, provider configuration, OpenAlgo availability, analyzer
path, paper/live path, and unsupported reasons.

## What I Would Improve Next

- add a real Groq adapter only if we decide to use Groq instead of OpenAI
- add screenshot QA artifacts
- configure real OpenAlgo and news provider checks
- add semantic embeddings beside the current BM25 retrieval
- move from DuckDB to PostgreSQL only for multi-user concurrent deployment

## Questions To Practice

- What happens when OpenAlgo is down?
- Why are risk decisions separate from orders?
- How do you prevent fake market data?
- Why is DuckDB acceptable locally?
- How does the evaluator prevent unsupported metrics?
- Why is `visible_in_openalgo=false` important for IIMC backtests?
- How would you onboard a new provider or asset class?
