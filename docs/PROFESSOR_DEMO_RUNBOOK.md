# Professor Demo Runbook

## Objective

Demonstrate a real, persisted research workflow from governed data through
signal generation, risk management, order management, fills, performance, and
auditable conversational retrieval. Do not present a historical simulation as
live trading.

## Preflight

```powershell
python -m iimc_trading_platform.cli doctor
python -m iimc_trading_platform.cli run-worker --once
node --check iimc_trading_platform\frontend\app.js
python -m iimc_trading_platform.cli platform-status `
  --symbol RELIANCE --exchange NSE --asset-class equity `
  --interval 5m --start-date 2026-04-23 --end-date 2026-05-23
python -m iimc_trading_platform.cli openalgo-monitor
python scripts\professor_demo.py --create-report
```

Run these stateful commands sequentially. DuckDB allows only one writer at a
time, so parallel preflight checks can create false lock failures.

The final command selects the canonical completed EMA 9/21 full-dataset run
unless `--run-id` is given. It prints database-backed counts and creates a
report in `artifacts/reports`.

Current verified demo run:

- run: `run_9f83c1c9ab65`
- signals: 56
- risk decisions: 56
- orders: 56
- fills: 56
- closed trades: 28
- latest report: `artifacts/reports/report_afb2773d7e05.md`

## Ten-Minute Flow

1. Open `http://127.0.0.1:8000/`.
2. Open Professor Dashboard. Show the backend workflow and table-count evidence.
3. Show the RELIANCE readiness check. Explain that the architecture supports
   multi-asset readiness validation, but each provider/symbol is verified per
   request and no missing data is fabricated.
4. Show Data Catalog: source coverage, row count, quality, and purpose-aware
   freshness.
5. Open Strategy Runs and select the EMA crossover run.
6. Explain that the run stores parameters before execution, then appends
   signals, risk decisions, orders, order transitions, fills, and performance.
7. Show the equity curve and the workflow counts.
8. Run a new IIMC historical backtest only if time permits. State
   `visible_in_openalgo=false`.
9. Generate the run report and identify its persisted `report_id`.
10. Open OpenAlgo Monitor. Explain that read-only snapshots require credentials
   and stored history remains visible without enabling live trading.
11. Open Data Catalog and run governed document search. Show `document_id`,
   `chunk_id`, `source_uri`, and score.
12. Open Operations and show completed freshness and knowledge-sync jobs.
13. Ask chat for the complete workflow of the run. Show the `tool_call_id` and
   run evidence rather than treating the LLM response as the source of truth.
14. Show Approvals. Explain that the LLM cannot approve or submit an order.
15. State the boundary: OpenAlgo account reads and analyzer submission require
    credentials; live trading remains disabled.

Verified local boundary as of June 27, 2026: without `OPENALGO_API_KEY`,
OpenAlgo monitor/readiness returns `credential_required` with credentials
redacted and `no_synthetic_fallback=true`.

## Backend Explanation

The orchestrator selects a typed tool. Pydantic validates its arguments. The
tool execution service records the request lifecycle. A domain service applies
business rules. Repositories and database adapters persist evidence. The
evaluator checks that the final answer does not introduce unsupported trading
metrics. Every external action is separated from approval and reconciliation.

The `/platform/*` routes are a stable dashboard contract over those internals.
They are read-only and safe without OpenAI or OpenAlgo keys.

## Browser QA Checklist

Manual browser QA should be done before the professor meeting:

- open `http://127.0.0.1:8000/`
- confirm Professor Dashboard loads without console errors
- confirm Strategy Runs table and run detail timeline are scrollable
- confirm IIMC backtest form shows JSON result or structured safe failure
- confirm OpenAlgo Monitor says `credential_required` or `unavailable` clearly
- confirm long run IDs, dataset IDs, report paths, and JSON panels wrap/scroll
- confirm Data Catalog document search shows chunk provenance

Screenshot automation was not completed in this environment unless explicitly
recorded in `docs/RESUME_EVIDENCE_TRACKER.md`.

## Questions To Expect

- Why is the LLM not allowed to write directly to the database?
- How do roles prevent a viewer from starting a backtest through chat?
- Why are signals, risk decisions, orders, and fills separate tables?
- How is an uncertain broker response reconciled without duplicate orders?
- What does dataset freshness mean for historical research versus current data?
- Why is DuckDB acceptable for this demonstration but not horizontal scale?
- How would PostgreSQL and a distributed worker queue replace the single-node
  deployment components?
