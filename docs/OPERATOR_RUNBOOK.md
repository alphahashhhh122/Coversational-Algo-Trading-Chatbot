# Local Review Runbook

## Start the Platform

```powershell
python -m pip install -e .
python -m iimc_trading_platform.cli init-db
python -m iimc_trading_platform.cli verify-foundation
python -m uvicorn iimc_trading_platform.asgi:app --host 127.0.0.1 --port 8001
```

Open `http://127.0.0.1:8001/`. The dashboard and all local research workflows
work without external credentials. Configure provider credentials only when
testing broker, model, or news-provider connectivity.

## Recommended Review Flow

1. Open **Data Catalog** and import a small OHLCV CSV/JSON payload using the
   local import workflow. Review the catalog entry, quality result, and source
   hash.
2. In **Chat**, ask a research or catalog question. Inspect the returned tool
   evidence; the response is grounded in a persisted tool call rather than an
   unverified model assertion.
3. Open **Custom Strategy** and create a supported declarative strategy. Validate
   it before saving, then run it against the imported dataset.
4. Review **Strategy Runs**. The backtest stores parameters, signals, risk
   decisions, order events, fills, performance, and lineage as historical
   research evidence.
5. Import a point-in-time feature series, attach it through `feature_inputs`,
   and rerun validation. Feature values are used only after their declared
   availability time.
6. Open **Approvals** and **OpenAlgo Monitor**. These views show the separation
   between research, paper intent, approval, and broker-side execution.

## Boundaries to State Clearly

- Historical backtests are local research, not broker executions.
- The LLM cannot write SQL, run generated Python, approve an order, or submit
  an order directly.
- Missing credentials or unavailable providers return structured safe failures;
  the platform does not fabricate data or account state.
- Live execution requires explicit configuration, provider readiness, risk
  validation, and human approval. It is outside a credential-free review.

## Verification

Run the checks below sequentially when the database is local, because DuckDB
uses a single-writer model:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q iimc_trading_platform tests scripts
python -m iimc_trading_platform.cli verify-foundation
node --check iimc_trading_platform\frontend\app.js
```

Use `GET /health` for application health and `GET /ready` to evaluate configured
deployment readiness. See [Operations Failure Runbook](OPERATIONS_FAILURE_RUNBOOK.md)
for recovery steps and [Production Readiness](PRODUCTION_READINESS.md) for
deployment-scale controls.
