# Claude Handoff Audit

## Inputs

- `makriop.zip`
- `OpenAlgo_Study_and_Action_Plan (1) (1).pptx`

## Verification Result

The ZIP was reconstructed into its intended Python package layout and tested in
an isolated workspace.

- Python compilation: passed
- Tests discovered: 72
- Tests passed: 68
- Tests failed: 4
- Failure area: SMA and momentum strategies were implemented in a detached file
  but never registered with the strategy runtime.

## Adopt And Harden

- generic strategy plugin contract
- EMA and RSI strategy implementations
- risk-policy service concept
- idempotent order state machine
- performance analytics
- typed tool registry
- LLM tool-calling orchestration
- evaluator and safety checks
- conversation memory
- OpenAlgo adapter boundary
- latency instrumentation
- FastAPI endpoint surface

## Rewrite Before Adoption

- database schema: conflicts with the existing real-data database
- evaluator: regex-only checks are insufficient for grounding
- OpenAlgo adapter: failures must raise typed errors, not return error-shaped
  position/fund data
- strategy registration: must be explicit and tested
- API lifecycle: use FastAPI lifespan, not deprecated startup events
- risk policy persistence: policies must live in `risk_limits`, not fake rows in
  `risk_decisions`
- order lifecycle: store current order state separately from append-only state
  transition history
- tool schemas: derive from Pydantic models and validate every call
- LLM integration: use the supported OpenAI SDK rather than raw HTTP calls

## Defer

- multi-agent handoffs
- MCP server exposure
- real-time broker WebSocket streaming
- multi-user authentication and authorization
- PostgreSQL deployment

These are valid production extensions but not required for the first
interview-ready release.

## Reject As Resume Evidence

- synthetic Warren Buffett stock picks
- synthetic five-year Bank Nifty straddle claims
- any live-trading claim
- any test count not produced by the merged repository

Synthetic data remains useful for unit tests, but it cannot be presented as a
real financial research result.

