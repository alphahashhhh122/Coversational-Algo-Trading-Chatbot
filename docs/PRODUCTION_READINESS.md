# Production Readiness

## Current Deployment Class

The repository implements a production-minded, single-node research and
OpenAlgo analyzer platform. It is suitable for a controlled institutional demo,
development environment, and a small authenticated research deployment. It is
not represented as a horizontally scalable live-trading system.

## Implemented Controls

- Signed, revocable server-side sessions.
- Viewer, researcher, approver, and admin roles.
- Capability-filtered chat tools, preventing RBAC bypass through the LLM.
- Strict tool schemas and persisted tool-call lifecycle records.
- Human approval separated from OpenAlgo analyzer submission.
- Idempotency keys and append-only order state transitions.
- Dataset provenance, quality reports, and purpose-aware freshness.
- Persisted scheduled jobs with locking, retries, and failure evidence.
- Durable one-off research tasks with leases, bounded retries, and terminal
  failure evidence.
- Portable DuckDB exports with file checksums, manifest row counts, and an
  actual temporary restore verification.
- Provider-independent BM25 retrieval with audited provenance, daily ranking
  evaluation, corpus identity, Recall@K, MRR, and nDCG release evidence.
- Preview-first operational retention with protected financial evidence,
  admin-only confirmed deletion, transactional execution, and audit records.
- Persisted critical/warning alert rules with deduplication, acknowledgement,
  observed-state resolution, Prometheus gauges, and linked failure runbooks.
- Machine-checked PostgreSQL DDL and storage placement manifest covering every
  table, with verified logical foreign keys and workload indexes.
- Actual Zstandard Parquet market-history export partitioned by underlying,
  expiry, and trade date, with checksums and read-back row-count verification.
- Request IDs, structured logs, body limits, rate limits, security headers,
  trusted hosts, readiness checks, and Prometheus-compatible metrics.
- Vendor-neutral OpenTelemetry HTTP and tool spans exported through a real
  collector, with trace/span IDs persisted in tool and audit evidence and
  exposed as `X-Trace-ID`.
- Non-root container, one API worker, persistent volumes, and CI verification.
- Separate liveness and traffic-readiness probes, with production admission
  gated by admin bootstrap, configured-model evaluation, retrieval quality,
  recent backup verification, alert evaluation, and zero critical alerts.
- Kustomize deployment with TLS ingress, NetworkPolicy, persistent volumes,
  external secrets, non-root execution, resource limits, and honest one-replica
  single-writer semantics.

## Deliberate Constraints

- DuckDB is a single-node analytical store. The container runs one API worker,
  and the maintenance worker is an explicit profile to avoid concurrent writer
  assumptions.
- The in-memory rate limiter is per process. A scaled deployment requires a
  shared limiter such as Redis or an API gateway.
- Scheduled jobs and one-off tasks use database locking, not a distributed
  queue. Separate writers are maintenance-only while DuckDB is in use.
- Markdown is the current report artifact. PDF export is a later presentation
  layer over the same evidence service.
- Model and OpenAlgo calls cannot be verified until valid credentials are
  configured.
- Direct local execution keeps tracing optional. The Compose stack enables the
  collector and Jaeger trace viewer automatically.

## Scale-Up Path

1. Apply the generated transactional DDL to a managed PostgreSQL deployment and
   execute the verified cutover manifest.
2. Upload the verified partitioned Parquet export to versioned object storage
   and attach the production analytical query layer.
3. Move jobs to a durable queue with separate workers and dead-letter handling.
4. Put the API behind TLS, an identity provider, a shared rate limiter, and
   centralized secrets management.
5. Add managed alert routing, point-in-time recovery, and object-storage
   retention. OpenTelemetry tracing and environment-specific manifests are
   already implemented.
6. Complete paper-trading soak tests before considering any live-trading
   enablement.

## Required Production Configuration

- `IIMC_ENVIRONMENT=production`
- `IIMC_AUTH_REQUIRED=true`
- A long random `IIMC_AUTH_SECRET`
- Restricted `IIMC_ALLOWED_HOSTS`
- `IIMC_ALLOW_LIVE_TRADING=false`
- `OPENAI_API_KEY` configured
- `IIMC_OTEL_ENABLED=true`
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` configured
- Persistent database and artifact volumes
- At least one bootstrapped admin account

Create the first account inside the deployment:

```powershell
python -m iimc_trading_platform.cli create-user `
  admin `
  --role admin
```

Before admitting traffic, run configured AI and retrieval evaluations, create
and verify a backup, and evaluate alerts. `GET /ready` returns HTTP 503 while
any mandatory production evidence is absent. `GET /live` remains the process
liveness probe.
