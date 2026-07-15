# ADR-003: Governed LLM Orchestration

## Status

Accepted

## Decision

Use a custom orchestrator with a strict Pydantic tool registry for the local
platform. Groq is the configured normal provider through its OpenAI-compatible
tool-calling interface; an OpenAI-compatible provider path and an explicit
offline fallback share the same governed contracts.

## Rationale

The platform needs typed, auditable routing to deterministic services. A direct
orchestrator keeps tool authorization, argument validation, evidence
persistence, and failure handling visible and testable. The LLM interprets
intent and produces grounded language; it does not calculate trading outcomes,
write database queries, or execute broker actions.

## Consequences

- Every tool defines its schema, required role, side-effect class, dependencies,
  and capability metadata in one registry.
- Custom strategies compile into declarative rule specifications rather than
  arbitrary generated Python.
- Approval and broker submission remain backend workflows outside the tool
  registry exposed to the LLM.
- A graph framework is not required for the current bounded request lifecycle.
  It may be evaluated later only when durable multi-step coordination adds value
  beyond the existing task and approval services.
