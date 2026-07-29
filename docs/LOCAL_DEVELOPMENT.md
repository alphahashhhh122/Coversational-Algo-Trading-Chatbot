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
python -m pytest tests/ -q            # full suite, 612 tests, 17-30 min
python -m pytest tests/test_orchestration_contracts.py -q   # fast routing contracts
```

**Run it serially, with nothing else touching Python.** DuckDB is single-writer,
so two pytest processes collide over the shared database and produce failures
that look real and are not. If a run comes back red, check for a stray pytest
process before believing it.

Why it takes that long, measured rather than guessed: building the FastAPI app
costs ~6.7s, and about 38 of those builds happen across the suite. Most of the
rest is real work — backtests over real candles, LangGraph loops, DuckDB
writes. `tests/_harness.py` already amortises the app across a test *class*;
the files that still build per test do so because they need a different config
or a patched broker at construction time, which a shared app cannot provide.

An earlier plan set an 8-minute target. On the measurements above that is not
reachable without either faking the work the slow tests exist to do, or
redesigning the app factory for lazy service construction. Recording the real
number is more useful than restating a target nothing meets.

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
