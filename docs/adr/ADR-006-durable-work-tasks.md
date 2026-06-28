# ADR-006: Durable Work Tasks On The Single-Node Runtime

## Status

Accepted

## Decision

Run expensive one-off research operations through a persisted `work_tasks`
queue. A task records its request, state, attempts, lease, result, and terminal
error. API and chat submissions enqueue first, then trigger an in-process
background drain. Operators can inspect or retry due work through the API and
CLI.

## Why

Robustness experiments can take much longer than an ordinary request. Keeping
them inside the request handler makes timeouts ambiguous and loses operational
visibility. A durable task gives the user an immediate ID, survives process
failure, supports bounded retries, and preserves evidence of failure.

## DuckDB Boundary

DuckDB is retained for the current single-node research deployment. Independent
web and worker processes must not write the same DuckDB file concurrently.
Therefore:

- the web process drains tasks in-process after returning the submission
- `run-task-worker --once` is a maintenance and recovery command
- the Compose task worker is behind the `maintenance` profile
- stale leases are retried or terminally failed after fifteen minutes

For multi-instance production, move transactional state and task claims to
PostgreSQL and use a queue such as Redis Streams, SQS, or Kafka. Task handlers
remain idempotent and continue to persist domain results separately from queue
metadata.

## Consequences

The current design is durable and observable for one application node, but it
does not claim horizontal worker scalability. That limitation is explicit,
tested, and isolated behind `TaskService`.
