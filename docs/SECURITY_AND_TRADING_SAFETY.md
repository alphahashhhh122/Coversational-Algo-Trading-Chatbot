# Security and Trading Safety

Credential handling detail lives in `SECURITY_AND_SECRETS.md`; this file is the trading-safety contract and its enforcement points.

## Hard guarantees (all enforced server-side, all tested)

1. **Live trading disabled by default.** `IIMC_ALLOW_LIVE_TRADING=false`; enabling requires deliberate config change plus live-mode risk decision plus mandatory human approval plus provider readiness (analyzer OFF). Rejection paths are covered by tests.
2. **Paper execution requires explicit human approval by default.** `IIMC_REQUIRE_PAPER_APPROVAL=true` in code and env; opting out requires explicit configuration. Submission hard-requires `approved` status; the only transition to `approved` is an approver-role decision with a recorded reason.
3. **No fabrication.** Missing providers fail safely with explicit "no synthetic … was generated" answers. No `random`/faker in production code; grounded responses only render real tool output.
4. **LLM containment.** The LLM routes intents and paraphrases tool JSON only. It cannot submit or approve orders (no such tools exist in the registry or MCP), cannot run arbitrary code, and deterministic guardrail refusals override it. NL strategies compile to a governed spec that requires user review before saving or running.
5. **Auditability.** Chat messages, tool calls (inputs/outputs/status), risk decisions with policy version, approvals with actor and reason, broker submissions and normalized errors, snapshots, uploads, and job runs are persisted. Failed and uncertain submissions are preserved, never rewritten.
6. **Idempotency & duplicate prevention.** Order intents carry idempotency keys; submission uses an atomic DuckDB claim (`UPDATE … WHERE status='approved' RETURNING`); duplicate keys are rejected.
7. **Stale-data rejection.** Paper intents are refused when the underlying strategy signal exceeds `IIMC_PAPER_SIGNAL_MAX_AGE_MINUTES` (default 20).
8. **Kill switch.** Portfolio-level kill-switch state blocks new reservations; emergency state is visible in the portfolio snapshot.

## Risk engine

Deterministic `RiskPolicy` (versioned, persisted with each decision): max quantity, max order value, max position value, max loss per trade, max daily loss, stop-loss percentage, allowed execution modes. All env-tunable via `IIMC_RISK_*` without code changes. Every evaluation persists a `risk_decisions` row with individual check results and human-readable reasons.

## AuthN/AuthZ

Roles `viewer < researcher < approver < admin` gate every endpoint and every tool (registry `required_role`). Passwords: PBKDF2-SHA256, 310k iterations, 12-char minimum. Sessions: HMAC-signed tokens with TTL. API hardening: rate limiting, request-size limits, trusted hosts, configurable CORS, input validation via Pydantic, upload size capped by middleware.

**Deployment note:** `IIMC_AUTH_REQUIRED=false` (the local-dev default) runs every request as an anonymous admin. Any multi-user or networked deployment MUST set it to `true` — this is the master switch that makes the role model real.

## Secrets

Env-only (`.env` gitignored; `.env.example` has empty values). Settings endpoint redacts secrets; news adapter redacts keys from error bodies; logs never include credentials or auth headers. No secrets in Docker/Compose files or container images.
