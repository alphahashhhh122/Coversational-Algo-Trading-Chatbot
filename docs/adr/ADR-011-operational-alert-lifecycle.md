# ADR-011: Persisted Operational Alert Lifecycle

## Status

Accepted

## Decision

Evaluate versioned operational thresholds into persisted alerts with three
states:

- `active`: threshold is breached
- `acknowledged`: an operator owns the still-breached condition
- `resolved`: a later evaluation proves the condition cleared

Rules cover broker uncertainty, stale worker leases, task and job failures,
current-market data freshness, overdue approvals, backup age, and failed AI or
retrieval evaluations. Repeated evaluations update one open alert rather than
creating duplicates. Every alert links to a failure runbook.

## Why

Raw counters do not provide ownership, deduplication, history, or proof of
recovery. Automatically resolving an alert when someone clicks it is also
unsafe. Resolution must follow observed system state.

## Scale Path

The single-node deployment exposes Prometheus-compatible gauges and persisted
alert state. A managed deployment forwards evaluations to Alertmanager,
PagerDuty, or an equivalent incident platform while retaining local alert IDs,
runbook links, and acknowledgement evidence.
