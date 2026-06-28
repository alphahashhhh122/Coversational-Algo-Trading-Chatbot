# ADR-010: Preview-First Operational Data Retention

## Status

Accepted

## Decision

Apply time-based retention only to operational records:

- expired or revoked authentication sessions
- inactive chat sessions and messages
- completed tool calls
- OpenAlgo account snapshots
- retrieval query events
- scheduled-job runs
- terminal durable tasks

Trading and research evidence is protected from automatic retention. This
includes source and market data, manifests, runs, signals, risk decisions,
orders, fills, portfolio ledgers, reports, audit events, and evaluation runs.

Daily automation creates a non-destructive preview. Deletion requires:

- an authenticated admin role
- the exact `PURGE_EXPIRED_OPERATIONAL_DATA` confirmation
- an explicit API or CLI action
- one database transaction
- a persisted retention run and immutable audit event

## Why

Keeping every operational payload forever increases privacy, storage, and
incident impact. Automatically deleting financial evidence is worse. A
preview-first boundary gives operators visibility while preserving the records
needed for reproducibility, reconciliation, and institutional review.

## Production Evolution

Managed deployments should add legal-hold flags, tenant-specific policies,
encrypted archival storage, regional privacy requirements, and database-native
partition retirement. Those controls extend this policy model rather than
changing the protected-data boundary.
