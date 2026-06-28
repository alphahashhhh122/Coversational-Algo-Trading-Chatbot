# ADR-014: Hardened Single-Node Kubernetes Deployment

## Status

Accepted

## Decision

Provide Kustomize manifests for the current deployment class with:

- exactly one replica and `Recreate` updates
- separate ReadWriteOnce database and artifact volumes
- non-root, read-only-root-filesystem execution
- dropped Linux capabilities and runtime-default seccomp
- CPU and memory requests and limits
- startup, liveness, and readiness probes
- TLS ingress and restricted request size
- ingress and egress NetworkPolicy
- externally created secrets

The application image includes the governed project-document corpus needed by
scheduled knowledge indexing.

## Why

Kubernetes does not make a single-writer database horizontally scalable. A
one-replica manifest is safer and more truthful than deploying multiple pods
against one DuckDB volume. The manifests still provide repeatable security,
storage, network, probe, and TLS configuration.

## Scale Gate

Replica count remains one until transactional writes move to PostgreSQL,
rate-limiting state becomes shared, and jobs/tasks move to a distributed queue.
Only after those migrations may rolling updates, horizontal scaling, and
multi-worker execution be enabled.
