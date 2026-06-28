# ADR-005: Portfolio Ledger, Position Projection, And Risk Reservations

## Status

Accepted

## Decision

Maintain portfolio state using:

- an append-only `portfolio_ledger` as audit evidence
- `portfolio_positions` as the current materialized position projection
- atomic pre-trade `risk_reservations`
- versioned portfolio risk decisions
- an operator-controlled portfolio kill switch

## Why

A current-position table alone cannot explain how the state was reached. A
ledger alone is expensive to replay for every risk check. Keeping both gives
traceability and efficient exposure calculation.

Risk reservations are required because checking cash and exposure without
reserving them creates a race: two simultaneous orders can both pass against the
same available funds or position.

## Invariants

- Ledger entries are immutable and idempotent by portfolio, event type, and
  external reference.
- A fill requires an active, unexpired risk reservation.
- A sell cannot exceed the unreserved long position.
- Activating the kill switch releases active reservations.
- The current projection is updated in the same database transaction as the
  fill ledger entry.
- Chat tools may inspect portfolio state but cannot reserve risk, record fills,
  or change the kill switch.

## Production Evolution

DuckDB serializes this single-node workflow adequately. A multi-node deployment
should move portfolio transactions and reservations to PostgreSQL using row
locks or serializable transactions. Broker fills should arrive through a durable
event queue, with reconciliation repairing projection drift from the ledger and
broker source of truth.
