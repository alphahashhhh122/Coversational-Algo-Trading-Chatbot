# Phase Final Test Matrix

Run from the project root. Latest full verification was completed on
June 27, 2026.

| Area | Command | Expected |
|---|---|---|
| Python syntax | `python -m compileall -q iimc_trading_platform scripts` | no output, exit 0 |
| Full regression | `python -m pytest -q` | 140 tests pass |
| Frontend syntax | `node --check iimc_trading_platform\frontend\app.js` | no output, exit 0 |
| Local health | `python -m iimc_trading_platform.cli doctor` | `status: healthy` |
| Fresh foundation | `python -m iimc_trading_platform.cli verify-foundation` | clean isolated DB pass |
| Real DB workflow | `python scripts\verify_real_workflow.py` | NIFTY dataset and schema versions printed |
| Operator evidence | `python scripts\operator_evidence.py --create-report` | report artifact created, latest verified `report_afb2773d7e05` |
| API smoke | `python scripts\smoke_real_api.py` | live, ready, data, readiness, RAG, chat pass |
| Generic readiness | `python -m iimc_trading_platform.cli platform-status --symbol RELIANCE --exchange NSE --asset-class equity --interval 5m --start-date 2026-04-23 --end-date 2026-05-23` | supported by architecture; provider not verified unless configured |
| OpenAlgo check | `python -m iimc_trading_platform.cli openalgo-check` | safe `credential_required`, `unavailable`, or real provider status |
| OpenAlgo monitor | `python -m iimc_trading_platform.cli openalgo-monitor` | analyzer/funds/orders/trades/positions state or safe failure |
| OpenAlgo readiness | `python -m iimc_trading_platform.cli openalgo-readiness --symbol RELIANCE --exchange NSE --asset-class equity --interval 5m --start-date 2026-04-23 --end-date 2026-05-23` | no order placed; provider status only |

Latest API surface count: 83 unique FastAPI routes and 77 OpenAPI paths.

## External Provider Rules

OpenAI, OpenAlgo, and market-news provider checks are skipped or return safe
failures unless the relevant keys and local services are configured. Mocked
provider tests prove parsing and storage behavior without claiming live external
success.

## Browser QA

Before a local operator session, manually open the app and verify:

- Operator Console loads
- Strategy Runs backtest form works or returns structured safe JSON
- OpenAlgo Monitor clearly shows credential/unavailable status
- long IDs and JSON panels wrap or scroll
- Data Catalog document search returns provenance

Screenshot automation has not been claimed unless a dated screenshot artifact is
added to the evidence tracker.
