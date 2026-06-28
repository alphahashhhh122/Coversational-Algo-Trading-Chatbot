# Resume Evidence Tracker

Verified on June 27, 2026.

## Build Evidence

| Area | Current proof | Verification |
|---|---:|---|
| Automated tests | 93 passing in 459.966s | `python -m unittest discover -s tests -v` |
| API | 77 OpenAPI paths, 83 unique FastAPI routes including static/workspace routes | FastAPI OpenAPI inspection |
| Professor dashboard | `/platform/summary`, `/platform/dashboard`, `/platform/dashboard/summary`, `/platform/professor-demo`, `/platform/status`, `/platform/openalgo/monitor` | `tests/test_platform_api_routes.py` |
| Frontend | Workspace, Professor Dashboard, strategy runs, experiments, portfolios, approvals, data catalog, OpenAlgo monitor, operations | `node --check iimc_trading_platform\frontend\app.js` |
| Strategy runtime | EMA, SMA, RSI, momentum | `GET /strategies` |
| Reproducibility | Source checksum, strategy/engine version, parameters, manifest hash | `experiment_manifests` |
| Governed market data | 66,080 rows from 69,262 source rows | `python scripts\verify_real_workflow.py` |
| Data quality | 3,182 duplicates and 0 invalid rows | catalog and quality report |
| Real strategy evidence | EMA run `run_9f83c1c9ab65` | `python scripts\professor_demo.py --create-report` |
| Signal/risk/order/fill workflow | 56 signals, 56 risk decisions, 56 orders, 56 fills | Professor demo script and `/runs/{run_id}/timeline` |
| Performance evidence | 28 closed trades, net P&L 475.22, max drawdown 342.45, return 0.0475% | `performance_summaries` |
| Risk engine | mode, symbol, confidence, quantity, notional, trade-loss, daily-loss checks | runtime tests and `risk_decisions` |
| Order manager | idempotency and append-only transitions | runtime tests and order timeline |
| Orchestration | OpenAI Responses API strict tools with offline fallback when key absent | orchestration contract tests |
| Evaluator | evidence, metric, guarantee, and simulation checks | evaluator contract tests |
| Durable research tasks | persisted claims, retries, results, and stale-worker terminal recovery | `tests/test_tasks.py` |
| Portfolio risk | append-only ledger, position projection, atomic reservations, and kill switch | `tests/test_portfolio.py` |
| Backup and recovery | checksummed DuckDB export plus temporary restore verification | `tests/test_backups.py` |
| Freshness | purpose-aware policies and assessments | governance tests and freshness API |
| Governed retrieval | indexed project docs with retrieval audit events | `python scripts\smoke_real_api.py` |
| Generic readiness | multi-asset symbol readiness with local-data and provider-status checks | `python -m iimc_trading_platform.cli platform-status --symbol RELIANCE --exchange NSE --asset-class equity --interval 5m --start-date 2026-04-23 --end-date 2026-05-23` |
| Market news boundary | unconfigured provider returns safe failure; configured mocked provider persists raw artifacts and normalized articles | `tests/test_readiness_and_news.py` |
| Retrieval quality | versioned corpus benchmark with Recall@K, MRR, and nDCG | retrieval evaluation tests |
| Storage migration | generated PostgreSQL JSONB DDL and verified relationships | `tests/test_storage_migration.py` |
| Analytical storage | partitioned Parquet export with checksums and read-back verification | storage migration tests |
| Operations alerts | persisted active/acknowledged/resolved lifecycle with runbooks | `tests/test_alerts.py` |
| OpenAlgo read path | sanitized snapshot adapter plus persisted snapshot history endpoint | mocked integration tests and `/openalgo/snapshots` |
| Sandbox bridge | approval, analyzer proof, submission, reconciliation | sandbox service/API tests |
| Authentication | signed revocable sessions and four roles | authentication tests |
| Operations | persistent jobs, retries, metrics, readiness | jobs and security tests |
| OpenAlgo monitor | analyzer/funds/orderbook/tradebook/positionbook readiness with redacted credentials and no synthetic success | `openalgo-check`, `openalgo-monitor`, `openalgo-readiness` |
| Reports | persisted Markdown run evidence | `artifacts/reports/report_afb2773d7e05.md` |
| Safety | live trading disabled by default; failed backtests return `no_synthetic_fallback` | config, health, and platform API tests |

## Verified Real Backtest

- strategy: EMA crossover 9/21
- dataset: `NIFTY_MONTH_E1_5m_options`
- run: `run_9f83c1c9ab65`
- candles: 1,575
- signals: 56
- risk decisions: 56
- orders: 56
- fills: 56
- closed trades: 28
- net P&L: 475.22
- max drawdown: 342.45
- return: 0.0475%
- fee assumption: 1 basis point
- slippage assumption: 0.5 basis points
- latest generated report: `artifacts/reports/report_afb2773d7e05.md`

## Verified Provider Boundary

The latest local OpenAlgo checks were run without credentials configured. The
expected result is a structured safe failure, not success:

- `openalgo-check`: `credential_required`, `credentials_redacted=true`
- `openalgo-monitor`: analyzer, funds, orderbook, tradebook, and positionbook
  all report `credential_required`
- `openalgo-readiness`: `provider_configured=false`, `verified_now=false`,
  `live_path_status=disabled`, `no_synthetic_fallback=true`
- RELIANCE readiness: architecture supports the asset request, but no local
  dataset or provider verification exists without credentials

The market-news provider is also unconfigured locally. The UI/API therefore
returns `news_provider_not_configured` and does not invent articles.

These are historical simulation results and must not be presented as live
returns or predictive performance.

## Remaining Before Final Resume Release

- browser screenshot verification for desktop/mobile UI; attempted on
  June 27, 2026, but the in-app browser webview did not attach
- real OpenAlgo analyzer snapshot with configured credentials
- real OpenAI Responses API orchestration with configured key
- real market-news provider call with configured credentials
- optional hybrid retrieval upgrade after current lexical retrieval is owned
- optional PostgreSQL migration for multi-user deployment
- final interview Q&A pack based only on verified claims

## Resume Bullet Rule

A claim may appear on the resume only when it has:

- a repeatable verification command
- current stored evidence
- a test or failure-path check
- an explanation the owner can defend

Synthetic or unsupported performance claims are excluded.
