# ADR-015: OpenTelemetry Trace Correlation

## Status

Accepted

## Decision

Use the OpenTelemetry SDK and FastAPI instrumentation for vendor-neutral
distributed tracing. Export spans over OTLP/HTTP to an external collector.
Create an explicit child span around every governed tool execution and persist
its 32-character trace ID and 16-character span ID with the tool-call record.
Copy the same identifiers into immutable audit-event payloads and structured
request logs.

Health, liveness, readiness, and metrics routes are excluded from sampling.
Local direct execution leaves tracing disabled by default. The Compose stack
enables a real OpenTelemetry Collector and Jaeger, while the Kubernetes
contract targets a collector in the `observability` namespace.

## Why

Request IDs identify one HTTP exchange but do not describe nested work or
cross-process propagation. OpenTelemetry provides a standard context model,
sampling, batching, and exporter boundary without binding business services to
one monitoring vendor. Persisting trace correlation beside tool and audit
evidence connects transient observability data to the durable financial and AI
governance record.

## Consequences

- Tool logic remains deterministic and independent of the exporter.
- An operator can move from an API response or database record to the exact
  distributed trace.
- Production readiness requires tracing to be enabled and an OTLP trace
  endpoint to be configured.
- A collector outage does not change tool results; the batch exporter reports
  telemetry delivery failures independently.
- Trace payloads must not contain credentials, raw authorization headers, or
  unrestricted financial data.
