# Implementation Status

Living log. Evidence = automated tests plus in-browser verification via the running dev server. Full suite: **249 passed** (`python -m pytest tests/ -q`).

| Feature | Status | Key files | Tests | Evidence / limitations |
|---|---|---|---|---|
| Chat intelligence (education, fundamentals Qs, screeners, sectors, account, comparison, personas) | done | `orchestration.py` | `test_orchestration_contracts.py` (74) | Verified live incl. "What would Warren Buffett do?"; screeners answer via news, not a fundamentals screener |
| Off-topic refusal + authoritative guardrails | done | `orchestration.py` | 7 contract tests | Enforced in offline AND Groq modes (fake-client test proves no provider call) |
| Markdown chat, history restore, dark mode, shortcuts, export | done | `frontend/app.js`, `styles.css` | browser-verified | Screenshots unavailable on this machine; DOM-verified |
| Candlestick charts + OHLCV endpoint | done | `api.py`, `frontend/app.js` | `test_market_data_ingestion.py` | 500-candle RELIANCE chart with hover tooltip verified |
| Company document upload + analyze | done | `api.py`, `knowledge_service.py`, `tools/registry.py` | 9 tests (routes + contracts) | PDF needs optional `pypdf`; BM25 (lexical, not vector) |
| MCP (HTTP + stdio) | done | `mcp_server.py`, `api.py` | `test_mcp.py` (9) | Researcher-level tools only; approvals stay in UI |
| NL strategy compiler + versions + backtests | done | `strategies/` | `test_nl_strategy_compiler.py`, runtime tests | Single instrument per spec; no multi-leg options |
| Walk-forward robustness | done | `robustness_service.py` | `test_robustness.py` | Grid + chronological OOS validation |
| Paper trading approval state machine | done | `sandbox_execution_service.py` | `test_sandbox_execution.py` (15) | Approval on by default; opt-out is explicit config |
| Live trading gates | done | risk/sandbox services | rejection-path tests | Live not verified against a real broker (no credentials in CI) |
| Risk policy env config + live-mode fix | done | `risk_service.py`, `config.py` | `RiskPolicyEnvTest` (3) | `IIMC_RISK_*` overrides |
| Scheduled news refresh (conditional) | done | `operations_service.py` | `test_jobs.py` (2) | Registers only when provider configured |
| Audit documentation set | done | `docs/*.md` | n/a | This change |

| Fundamental analysis (statements import + deterministic ratio engine) | done | `fundamentals_service.py`, `tools/registry.py`, `api.py`, `orchestration.py` | `test_fundamentals.py` (5) | Ratios carry formula+inputs; warnings for missing data; chat: "analyze TCS fundamentally". Statement data is user-imported (no free provider) |

| Broker emergency controls + holdings | done | `openalgo.py`, `openalgo_service.py`, `api.py`, Monitor UI | `test_openalgo_emergency.py` (6) | Cancel-all/square-off approver-gated with typed CONFIRM; audited; holdings snapshot |
| Versioned fundamental screens | done | `screen_service.py`, `api.py`, `orchestration.py` | `test_screens.py` (7) | quality/growth/low_leverage defaults; new versions via API; chat "run the quality screen" |
| Fundamentals import UI | done | `frontend/index.html`, `app.js` | browser-verified | Data-view form → chat analysis verified live |

Remaining optional items (not gaps in local usability): per-order modification passthrough, WebSocket streaming quotes (15s polling covers localhost use), external statements provider adapter.
