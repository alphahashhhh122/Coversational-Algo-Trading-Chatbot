# Local Development

## Prerequisites

Python 3.12+. No Node toolchain (frontend is framework-free and served by FastAPI). Optional: an OpenAlgo instance for broker features, a Groq key for LLM routing, a news provider key, `pip install pypdf` for PDF uploads.

## Setup

```powershell
python -m pip install -e .
copy .env.example .env          # fill only what you want to validate
python -m iimc_trading_platform.cli init-db
python -m iimc_trading_platform.cli verify-foundation
python -m uvicorn iimc_trading_platform.asgi:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ (dashboard) and http://127.0.0.1:8000/docs (OpenAPI).

Without any credentials the platform runs fully: deterministic chat routing, education, strategy compilation, local imports, backtests, document upload/analysis, dashboards. Broker/news/LLM features degrade honestly with explanations, never with synthetic data.

## Tests

```powershell
python -m pytest tests/ -q            # full suite, 249 tests, ~15 min
python -m pytest tests/test_orchestration_contracts.py -q   # fast routing contracts
```

## Useful checks

```powershell
python -m iimc_trading_platform.cli doctor
python -m iimc_trading_platform.cli openalgo-monitor
```

## Auth in development

`IIMC_AUTH_REQUIRED=false` (default) runs as a local admin principal with no login — appropriate only on a private machine. Set it to `true`, set `IIMC_AUTH_SECRET`, and create users (`python -m iimc_trading_platform.cli create-user`) for any shared deployment; role gates (viewer/researcher/approver/admin) are then enforced end to end.

## Troubleshooting

- DuckDB file lock: only one uvicorn process may own the database file.
- Windows: use the provided `.claude/launch.json` dev-server config or plain uvicorn; `--reload` picks up backend and frontend edits.
- Tests are slower on first run (DuckDB schema init per test database).
