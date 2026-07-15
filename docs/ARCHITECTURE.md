# Architecture

## Purpose

The platform is a local-first conversational workspace for market research,
governed data ingestion, deterministic strategy backtesting, broker readiness,
and approval-gated execution workflows. It is designed so that natural-language
input improves usability without becoming an authority over financial data,
risk, or order state.

## Request Path

```text
Dashboard or API client
  -> FastAPI route and authentication middleware
  -> Groq/OpenAI-compatible orchestrator or explicit offline fallback
  -> Typed Pydantic tool contract
  -> Deterministic domain service
  -> DuckDB repository and optional provider adapter
  -> Persisted audit evidence and grounded response
```

The LLM can select from registered tools and explain their persisted results. It
cannot execute arbitrary Python, write SQL, bypass role checks, approve an
order, or submit an order directly.

## Core Components

- **FastAPI API and dashboard:** typed HTTP contracts and the local operator UI.
- **Orchestrator and tool registry:** capability-aware natural-language routing,
  strict input validation, role gates, side-effect metadata, and tool-call
  lifecycle records.
- **Data services:** governed OHLCV, options-chain, and point-in-time feature
  ingestion with quality checks, provenance hashes, and catalog entries.
- **Strategy runtime:** deterministic built-in and rule-spec strategies,
  point-in-time feature alignment, transaction costs, risk decisions, order
  events, fills, and historical performance evidence.
- **Knowledge retrieval:** BM25 retrieval over a curated technical corpus with
  document/chunk provenance and persisted evaluation metrics.
- **Execution controls:** risk validation, human approvals, idempotent order
  intent claims, OpenAlgo analyzer proof, reconciliation, and provider
  readiness gates.
- **Operations:** scheduled jobs, durable tasks, backups, retention, alerts,
  traces, health/readiness probes, and a documented PostgreSQL migration path.

## Data Boundaries

Structured market, portfolio, execution, and performance facts are accessed
only through services and repositories. Retrieval is reserved for unstructured
technical documents. Every external provider failure is exposed as a structured
safe failure; the platform does not fabricate prices, news, account state, or
backtest results.

## Custom Strategy Model

Custom strategies are declarative JSON specifications rather than generated
source code. A specification declares supported indicators, rules, risk limits,
and optional governed feature inputs. Validation rejects unsupported primitives,
invalid identifiers, inconsistent feature timing, and unsafe references before
a backtest can run. Feature observations are aligned strictly as-of their
`available_at` timestamp, preventing look-ahead bias.

## Execution Model

Historical backtests are local research artifacts and are never represented as
broker executions. Paper and live order intents are distinct explicit workflows.
An LLM may prepare an intent only after a persisted risk decision; human
approval, provider readiness, and execution-mode checks are independently
enforced by backend services. Live orders remain disabled unless deliberately
enabled in local configuration.

## Local and Deployment Scope

DuckDB is the intentional single-node local store. The repository also includes
container, Compose, Kubernetes, observability, backup, and storage-migration
contracts, but multi-user deployment requires the controls described in
[Production Readiness](PRODUCTION_READINESS.md).
