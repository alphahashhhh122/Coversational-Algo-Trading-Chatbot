# Gap Analysis

Classification of every capability in the master specification against the actual code. Statuses: **complete-verified** (implemented + automated tests), **complete-needs-credentials** (implemented + tested against fixtures; live verification needs provider keys), **partial**, **missing**.

| Capability | Status | Evidence / root cause | Fix & phase |
|---|---|---|---|
| Market & company research (news, quotes, briefs) | complete-needs-credentials | `market_news_service`, `get_market_quote`, research briefs; safe failure without keys | Configure `MARKET_NEWS_API_KEY`, `OPENALGO_API_KEY` |
| Fundamental analysis (statement-level) | complete-verified | `fundamentals_service.py`: imported statements + deterministic ratio engine (growth, margins, ROE/ROA, leverage, liquidity, FCF, EPS, P/E) with formulas and inputs; chat + API + Data-view form; `test_fundamentals.py` | Statement data is user-imported (no free Indian statements API); external provider adapter remains optional |
| Technical analysis | complete-verified | 11 deterministic indicators (EMA/SMA/RSI/MACD/BB/ATR/VWAP/ROC + features) in rule-spec runtime, tested; candlestick charts | ADX/stochastic/OBV additions optional |
| NL strategy creation + versions | complete-verified | `strategies/nl_compiler.py` → validated spec → preview → saved versioned spec; no code generation | — |
| Backtesting (deterministic, costs, no lookahead) | complete-verified | `backtest_service` (fees/slippage bps, signal-then-fill ordering, tests incl. options lot/expiry) | — |
| Optimization / walk-forward | complete-verified | Robustness experiments: chronological split, parameter grid, out-of-sample verdicts, background tasks | Random search optional |
| Paper trading with HITL approval | complete-needs-credentials | Full state machine (`sandbox_execution_service`), atomic claim, idempotency, approval bound to risk scope; tested with fake broker | Live check needs OpenAlgo running |
| Live trading gates | complete-verified | Off by default; config + live risk decision + mandatory approval + readiness (analyzer must be off); rejection paths tested | — |
| Monitoring (orders/positions/funds/P&L) | complete-needs-credentials | Snapshot sync (funds/positions/orderbook/tradebook), persisted history, monitor view | Holdings endpoint not wrapped |
| Custom dashboard | complete-verified | Widget registry + picker, preferences persisted server-side | Grid drag-drop not implemented |
| Multi-asset (equity/futures/options/commodity/crypto) | complete-verified | Dataset ingestion + backtests per asset class; options contract metadata (expiry/strike/type/lot) | Multi-leg options missing |
| Conversational + synchronized GUI | complete-verified | Chat routes to same governed tools as UI; evidence panel; view refresh on account intents | — |
| OpenAlgo integration | complete-needs-credentials | Adapter normalizes quotes/history/funds/books/analyzer; errors normalized; no internal-DB coupling | WebSocket depth/live quotes missing (polling) |
| Off-topic refusal / honest chat | complete-verified | Authoritative domain refusals enforced in offline AND Groq modes | — |
| Company document storage & analysis | complete-verified | Upload endpoint + UI, BM25 corpus, analyze tool, audit | Vector embeddings optional |
| Capability registry | complete-verified | Tool capability metadata + `/platform/summary` asset coverage + execution readiness consumed by frontend | — |
| Risk engine | complete-verified | Persisted `risk_decisions` (checks, policy version), env-tunable `RiskPolicy`, quantity/value/loss/stop caps | Sector-exposure and trading-hours controls missing |
| Auth/roles/secrets | complete-verified | 4 roles, PBKDF2 (310k), HMAC sessions, rate limits, size limits, secrets env-only, redaction | Per-permission granularity coarser than spec |
| Observability & health | complete-verified | JSON logs + request IDs, OTel optional, `/health` `/live` `/ready` | — |
| Background jobs & tasks | complete-verified | Persistent scheduled jobs + retries + disable-on-failure; work tasks for long runs | — |
| PostgreSQL / Redis / React / SSE-WebSocket | missing (deliberate) | Single-user local research platform; DuckDB is authoritative store; spec §3 permits preserving a sound stack | Documented in KNOWN_LIMITATIONS; migration path in TARGET_ARCHITECTURE |
| Live-to-historical tick aggregation | missing | No streaming quotes source wired | Requires WebSocket provider first |
| Cancel-all / square-off / holdings | complete-verified | `POST /openalgo/emergency/{action}` (approver role, audited, typed-CONFIRM UI), holdings snapshot type; `test_openalgo_emergency.py` | Per-order modification passthrough still optional (intent cancel exists) |
| Screening (persisted screen definitions) | complete-verified | `screen_service.py`: versioned `screen_definitions` (quality/growth/low_leverage defaults + user versions) evaluated over deterministic ratios; chat + API; `test_screens.py` | Universe = symbols with imported statements |

Acceptance criteria for any "partial → complete" move: typed schema, deterministic service, API + frontend slice, tests, honest degraded state without credentials.
