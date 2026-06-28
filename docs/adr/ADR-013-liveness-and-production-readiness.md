# ADR-013: Separate Liveness From Production Readiness

## Status

Accepted

## Decision

Expose separate probes:

- `/live` proves the application process can answer HTTP
- `/health` reports foundation and capability health
- `/ready` decides whether the deployment may receive production traffic

The container health check uses `/live`. Production readiness additionally
requires:

- authentication and a production secret
- restricted hosts and disabled live trading
- a configured OpenAI key
- at least one active admin
- a passing configured-model AI evaluation
- a passing retrieval evaluation
- a recent successful backup restore verification
- at least one alert evaluation
- zero active or acknowledged critical alerts

OpenAlgo readiness is reported as a capability because research-only operation
can remain available while broker access is intentionally disabled.

## Why

Restarting a healthy process because a backup is stale or an evaluation is
missing causes an outage without repairing the underlying condition. Liveness
drives process recovery; readiness gates traffic and exposes actionable
operational evidence.

## Consequence

A new production environment intentionally remains not ready until bootstrap,
evaluation, backup verification, and alert evaluation are complete. Operators
can still reach the process and execute those controlled setup steps.
