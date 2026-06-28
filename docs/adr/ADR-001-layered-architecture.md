# ADR-001: Layered Architecture

## Status

Accepted

## Decision

Separate the platform into:

- domain models and rules
- repository contracts
- application services
- infrastructure adapters
- callable tools/API
- agent orchestration
- frontend

## Rationale

Trading concepts should not depend directly on DuckDB, FastAPI, OpenAlgo, or an
LLM provider. Repository contracts allow infrastructure to change without
rewriting business workflows.

## Consequences

- more initial structure
- easier testing and migration
- clearer interview explanation
- reduced coupling between the LLM and trading state
