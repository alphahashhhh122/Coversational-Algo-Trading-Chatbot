# ADR-002: Development And Production Storage

## Status

Accepted

## Decision

Use DuckDB for the local analytical prototype and initial app state. Keep
repository boundaries so production app state can move to PostgreSQL while
research data remains in DuckDB or Parquet.

## Rationale

DuckDB provides fast local analytics and simple setup. PostgreSQL is better for
concurrent multi-user transactions, authentication, and long-running production
services.

## Consequences

- rapid local development
- explicit future migration path
- services must not depend on DuckDB-specific behavior unnecessarily
