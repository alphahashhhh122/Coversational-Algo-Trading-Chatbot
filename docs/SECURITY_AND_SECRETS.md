# Security And Secrets Policy

## Rules

- API keys, broker credentials, access tokens, and passwords must never be stored
  in source files, prompts, reports, screenshots, logs, or committed databases.
- Local secrets are supplied through environment variables.
- `.env` is ignored by version control.
- `.env.example` contains names and safe defaults only.
- CLI configuration output shows whether a secret is configured, never its value.
- OpenAI Agents SDK tracing must exclude sensitive tool input/output when broker
  credentials or account information may be present.
- The platform database stores broker identifiers and sanitized snapshots, not
  reusable login credentials.

## Live-Trading Safety

- `IIMC_ALLOW_LIVE_TRADING` defaults to `false`.
- Research, sandbox, semi-auto, and live are separate execution modes.
- Enabling the environment flag alone must never place an order.
- Live execution will additionally require:
  - authenticated user
  - explicit confirmation
  - approved risk decision
  - configured live risk policy
  - immutable audit record

## Incident Response

If a credential appears in code, logs, chat, or screenshots:

1. Revoke and rotate it immediately.
2. Remove it from active files and history where possible.
3. inspect logs and broker activity.
4. Record the incident and corrective action.
