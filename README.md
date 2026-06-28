# IIMC Conversational Algo-Trading Platform

Local-first conversational trading research workspace for professor demos,
resume evidence, and interview defense.

This is not a cloud deployment, HFT system, autonomous live-trading bot, or
profitability claim. It is a local product that shows how a natural-language
interface can route to governed backend tools for data, research, risk, order
evidence, OpenAlgo readiness, RAG, and reporting.

## What Is Implemented

- FastAPI backend with typed tool contracts
- local DuckDB evidence store
- governed NIFTY options data catalog with 66,080 real rows
- deterministic strategy engine: EMA, SMA, RSI, momentum
- persisted signal, risk, order, fill, and performance workflow
- Professor Dashboard and Markdown report generation
- OpenAlgo monitor/readiness layer with safe unavailable/credential states
- analyzer/sandbox submission boundary with human approval
- generic symbol/asset-class readiness checks
- governed document retrieval and retrieval evaluation
- provider-backed market-news interface when configured
- frontend workspace for chat, runs, data, OpenAlgo, operations, and evidence

## What Is Not Claimed

- no autonomous live trading
- no guaranteed profitable strategy
- no fake market data, fake news, fake P&L, or fake broker responses
- no claim that all symbols/assets are verified
- no claim that IIMC historical backtests appear inside OpenAlgo
- no real OpenAI/OpenAlgo/news provider calls unless keys are configured
- no production cloud deployment claim

## Setup

```powershell
python -m pip install -e .
python -m iimc_trading_platform.cli init-db
python -m iimc_trading_platform.cli doctor
python -m iimc_trading_platform.cli verify-foundation
```

Run the local app:

```powershell
uvicorn iimc_trading_platform.asgi:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Run stateful DuckDB commands sequentially. DuckDB is used as the local evidence
store and should not be written by multiple processes at the same time.

## Professor Demo

```powershell
python scripts\verify_real_workflow.py
python scripts\professor_demo.py --create-report
python scripts\smoke_real_api.py
```

The canonical current demo run is:

- run: `run_9f83c1c9ab65`
- strategy: EMA crossover 9/21
- dataset: `NIFTY_MONTH_E1_5m_options`
- signals: 56
- risk decisions: 56
- orders: 56
- fills: 56
- closed trades: 28
- net P&L: 475.22
- max drawdown: 342.45
- return: 0.0475%

This is an IIMC historical backtest from local real data. It is not OpenAlgo
broker activity and is not a prediction.

## OpenAlgo

Configure only when you want real OpenAlgo checks:

```powershell
$env:OPENALGO_BASE_URL="http://127.0.0.1:5000"
$env:OPENALGO_API_KEY="..."
```

Then run:

```powershell
python -m iimc_trading_platform.cli openalgo-check
python -m iimc_trading_platform.cli openalgo-monitor
python -m iimc_trading_platform.cli openalgo-readiness `
  --symbol RELIANCE --exchange NSE --asset-class equity `
  --interval 5m --start-date 2026-04-23 --end-date 2026-05-23
```

Without credentials, the platform returns `credential_required` safely. If
OpenAlgo is down, it returns `unavailable`. Neither state is treated as success.

## Symbol Readiness

```powershell
python -m iimc_trading_platform.cli platform-status `
  --symbol RELIANCE --exchange NSE --asset-class equity `
  --interval 5m --start-date 2026-04-23 --end-date 2026-05-23
```

The readiness layer supports multi-asset validation by request. It checks local
catalog data, provider configuration, OpenAlgo status, analyzer path, paper/live
boundaries, and `no_synthetic_fallback`.

## Market News

Configure only for a real provider:

```powershell
$env:MARKET_NEWS_PROVIDER="your_provider"
$env:MARKET_NEWS_API_URL="https://provider.example/news"
$env:MARKET_NEWS_API_KEY="..."
```

If not configured, news APIs return `news_provider_not_configured` and no fake
articles. If configured, raw provider responses are stored under
`artifacts/market_news` and normalized articles are deduplicated in DuckDB.

## Validation

```powershell
python -m compileall -q iimc_trading_platform scripts
python -m unittest discover -s tests -v
node --check iimc_trading_platform\frontend\app.js
python -m iimc_trading_platform.cli doctor
python -m iimc_trading_platform.cli verify-foundation
python scripts\verify_real_workflow.py
python scripts\professor_demo.py --create-report
python scripts\smoke_real_api.py
```

See:

- `docs/PROFESSOR_DEMO_RUNBOOK.md`
- `docs/RESUME_EVIDENCE_TRACKER.md`
- `docs/OPERATOR_WORKSPACE.md`
- `docs/PHASE_FINAL_TEST_MATRIX.md`
- `docs/RESUME_BULLETS.md`
- `docs/INTERVIEW_DEFENSE.md`
