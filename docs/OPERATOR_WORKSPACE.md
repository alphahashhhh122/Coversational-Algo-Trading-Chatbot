# Operator Workspace

## Purpose

The frontend is an operational surface over the existing FastAPI contracts. It
does not calculate indicators, risk, P&L, or order state in the browser.

## Views

- Workspace: grounded chat, tool evidence, health, and current metrics
- Operator Console: read-only workflow, storage counts, and operational navigation
- Strategy Runs: stored runs, risk/order counts, and equity curve
- Experiments: chronological robustness runs and parameter candidates
- Portfolios: cash, positions, reservations, and kill-switch controls
- Approvals: pending external actions with explicit human decisions
- Data Catalog: quality, coverage, provenance, and freshness assessment
- Governed Document Search: query indexed RAG chunks with provenance
- OpenAlgo Monitor: read-only analyzer/account snapshots when credentials exist
- Operations: jobs, tasks, backups, evaluations, retention, alerts, readiness

## Safety Behavior

- Live trading status is always visible.
- Missing OpenAI credentials are shown as offline orchestration.
- Missing OpenAlgo credentials are shown as not configured.
- Approval does not automatically submit an order.
- The LLM cannot approve requests.
- Submission remains blocked by backend analyzer-mode and credential checks.
- `/platform/*` dashboard routes are read-only and safe without external keys.
- Failed backtests do not fabricate fallback results; API errors expose
  `no_synthetic_fallback`.
- IIMC historical backtests show `visible_in_openalgo=false`.
- OpenAlgo monitor distinguishes `credential_required`, `unavailable`,
  provider errors, and actual analyzer availability.
- Market news is shown only when a real provider is configured; otherwise the
  UI/API reports `news_provider_not_configured`.

## Current Limitations

- no background polling or push updates
- no browser screenshot QA completed on this host
- no real OpenAI/OpenAlgo call has been claimed without configured keys

## Verification

```powershell
node --check iimc_trading_platform\frontend\app.js
python -m unittest discover -s tests -v
python scripts\operator_evidence.py --create-report
python scripts\smoke_real_api.py
python -m iimc_trading_platform.cli platform-status --symbol RELIANCE --exchange NSE --asset-class equity --interval 5m --start-date 2026-04-23 --end-date 2026-05-23
python -m iimc_trading_platform.cli openalgo-monitor
```

Static assets and the root workspace are served directly by FastAPI.
