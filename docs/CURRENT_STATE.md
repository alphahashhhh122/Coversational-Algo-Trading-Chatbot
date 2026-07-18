# Current State

Audited against the running repository (branch `main`, 249 passing tests).

## Stack

- **Backend**: Python 3.12, FastAPI, Pydantic v2, DuckDB (single-file, `data/iimc_platform.duckdb`), uvicorn.
- **Frontend**: framework-free vanilla JS/HTML/CSS served by FastAPI (`iimc_trading_platform/frontend/`), no build step, no CDN dependencies (markdown renderer and candlestick charts are local implementations).
- **LLM**: Groq (`llama-3.3-70b-versatile`) via the OpenAI SDK with a deterministic `OfflineOrchestrator` fallback; optional OpenAI Responses path.
- **Broker**: OpenAlgo over its public REST API (`infrastructure/openalgo.py`), analyzer (paper) mode default.
- **News**: EventRegistry-shaped provider adapter (configurable provider/URL/key).
- **Observability**: structured JSON logs with request IDs, optional OpenTelemetry, `/health`, `/live`, `/ready`.
- **Deployment**: `deploy/` Docker/Kubernetes references, GitHub Actions CI (`.github/workflows/`).

## Entry points

- API/UI: `python -m uvicorn iimc_trading_platform.asgi:app --host 127.0.0.1 --port 8000`
- CLI: `python -m iimc_trading_platform.cli` (`init-db`, `verify-foundation`, `doctor`, `create-user`, `openalgo-monitor`, `openalgo-readiness`, job commands)
- MCP stdio server: `python -m iimc_trading_platform.mcp_server`

## Backend structure

`iimc_trading_platform/`: `api.py` (REST + static frontend), `api_models.py`, `orchestration.py` (three-tier routing: deterministic → Groq tool-calling → plain LLM fallback, with authoritative guardrails), `evaluator.py`, `config.py` (env-driven `AppConfig`), `mcp_server.py`, `tools/registry.py` (typed tool registry, ~47 tools with input schemas, roles, side-effect declarations, capability metadata), `services/` (30+ focused services: backtest, sandbox execution, risk, portfolio, knowledge, market news, conversation, personas, jobs, tasks, alerts, backups, retention, evaluations, telemetry), `infrastructure/` (DuckDB schema ~67 tables, OpenAlgo client), `strategies/` (built-in strategies, NL compiler, governed rule-spec runtime), `evals/` (AI + retrieval eval cases).

## Implemented workflows (all covered by tests)

- Conversational chat with evidence, audit records, session persistence, markdown rendering, history restore, dark/light themes, keyboard shortcuts, export.
- Education, fundamentals questions, screeners, sector outlooks, account queries, symbol comparison, off-topic refusal, personas (incl. "What would Warren Buffett do?").
- NL strategy → compiled governed spec → editable preview → save version → backtest (confirmation-first; never auto-executes).
- Deterministic backtests (fees/slippage bps, equity/options/futures/commodity/crypto datasets, options contract selection with expiry/strike/type), performance evidence, run comparison, reports.
- Walk-forward robustness experiments (chronological train/validation split, parameter grids, verdicts) as background tasks.
- OpenAlgo history import, local OHLCV/feature import (point-in-time, `available_at`-aware), governed data catalog with freshness/quality/provenance (SHA-256).
- Paper trading: risk decision → order intent → human approval (approver role) → analyzer submission → snapshot sync; idempotency, atomic claim, stale-signal rejection, kill-switch state at portfolio level.
- Live trading: disabled by default; triple-gated (config flag + live risk decision + mandatory approval + provider readiness with analyzer-off check).
- Company documents: upload (.txt/.md/paste; optional PDF via pypdf), BM25 retrieval, analyze-document tool, audit trail.
- Interactive candlestick + volume charts with OHLCV endpoint; custom dashboard widgets (registry-driven picker, server-persisted preferences).
- MCP: HTTP (`/mcp/tools`, `/mcp/call`) and stdio server, researcher-level tools only.
- Operations: scheduled jobs (freshness sweep, knowledge sync, backup verification, retrieval eval, retention preview, alert evaluation; conditional OpenAlgo snapshots and market-news refresh), work tasks, backups, alerts, retention, AI/retrieval evaluations.

## Test status

`python -m pytest tests/ -q` → **249 passed** (~15 min, 31 test modules): unit, contract (orchestration routing, tool schemas, MCP), integration (API routes, sandbox execution with fake broker, ingestion, jobs), and safety tests (live gates, approval enforcement, stale-signal refusal, no-fabrication).

## Known technical debt

See `GAP_ANALYSIS.md` and `KNOWN_LIMITATIONS` in the README. Headlines: single-user local scope (DuckDB, no PostgreSQL/Redis), polling instead of WebSockets, no fundamentals statement provider (quotes/news/uploaded documents only), single-instrument backtests, no multi-leg options strategies, no order modification/cancel-all/square-off passthrough.
