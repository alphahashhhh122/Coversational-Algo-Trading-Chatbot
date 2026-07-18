# OpenAlgo Integration

OpenAlgo is treated strictly as an **external broker-integration service**: this platform talks only to its public REST API through `infrastructure/openalgo.py` and never reads its internal database. `OPENALGO_SANDBOX_BRIDGE.md` documents the analyzer bridge in depth; this file is the capability contract.

## Configuration

```env
OPENALGO_BASE_URL=http://127.0.0.1:5000
OPENALGO_API_KEY=            # never committed; env/.env only
```

Without a key, every OpenAlgo-backed tool and endpoint reports "not configured" and the capability registry marks broker features as gated. Nothing is faked.

## Implemented capabilities (normalized behind the adapter)

| Capability | Surface |
|---|---|
| Connection/health + analyzer-mode status | monitor endpoints, readiness checks |
| Funds, positionbook, orderbook, tradebook | `get_openalgo_snapshot` (persisted, sanitized history) |
| Quotes (LTP/quote resolution incl. option symbols) | `get_market_quote`, instrument tools |
| Historical candles → governed datasets | `import_openalgo_history` (provenance SHA-256, catalog, freshness) |
| Instrument search / symbol validation / option symbol construction | instrument tools + `/platform/instruments/*` |
| Analyzer (paper) order submission | sandbox intent state machine (below) |
| Live order intent | separate gated path; disabled by default |
| Error normalization | `OpenAlgoUnavailableError`, `OpenAlgoAuthenticationError` → typed HTTP statuses; secrets redacted |

Not yet wrapped: WebSocket live quotes/market depth (the platform polls), order modification, cancel-all, square-off, holdings endpoint. These are listed in `GAP_ANALYSIS.md`.

## Paper workflow (analyzer mode, the default)

```
semi-auto backtest → persisted risk decision
→ POST /sandbox/intents (validated against approved scope: symbol, exchange,
  side, quantity ≤ approved, signal freshness window, idempotency key)
→ approval_requests row (pending) — human approver decides with reason
→ POST /sandbox/intents/{id}/submit — requires status approved, atomic claim
  (UPDATE … WHERE status='approved' RETURNING) prevents double submission
→ server re-checks analyzer mode is ON before submitting
→ order/trade/position/funds snapshots synchronized; full audit trail
```

Backtest results, analyzer paper trades, and live records are **never merged**: backtests live in strategy runs, paper execution evidence is shown separately in Monitor.

## Live trading gates (all required simultaneously)

1. `IIMC_ALLOW_LIVE_TRADING=true` in server config (default false).
2. Risk decision evaluated in LIVE mode by the deterministic risk engine.
3. Mandatory human approval (cannot be disabled for live).
4. Provider readiness OK **and analyzer mode OFF** at submission time.
5. Idempotency key unused; intent fields unchanged since approval.

Neither the chat LLM nor MCP exposes any submit/approve tool.

## Reconciliation & restart

Snapshots are persisted with timestamps; the optional `openalgo_snapshot` scheduled job (30s) refreshes account state when a key is configured. Intents in `submission_uncertain` are preserved for manual reconciliation and surfaced in operations metrics — uncertain records are never deleted or auto-resolved.
