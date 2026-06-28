# ADR-003: OpenAI Agents SDK Orchestration

## Status

Accepted for the agent phase

## Decision

Build deterministic Python services and typed tools first. Use the OpenAI Agents
SDK later for manager-style orchestration, tool calling, guardrails, sessions,
and tracing.

Specialist agents may be exposed as tools. Handoffs are reserved for cases where
a specialist should take over the conversation.

## Rationale

The LLM should plan and explain, while data, strategy, risk, order, and
performance calculations remain deterministic and testable.

## Consequences

- agent integration begins only after tool contracts stabilize
- SDK traces supplement but do not replace persistent application audit logs
- MCP remains optional for standardized external tool exposure
