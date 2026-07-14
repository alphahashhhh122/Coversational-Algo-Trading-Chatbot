# OpenAlgo Sandbox Bridge

## Ownership Boundary

The platform owns:

- strategy signal and risk decision
- order intent and idempotency key
- explicit human approval
- local order lifecycle and audit trail
- reconciliation snapshots and grounded explanations

OpenAlgo owns:

- analyzer-mode validation
- sandbox order acceptance
- `sandbox_orders`, `sandbox_trades`, `sandbox_positions`, and `sandbox_funds`
- simulated matching, margin, positions, and execution state

The platform does not write directly to OpenAlgo's sandbox database.

## Workflow

```text
Approved risk decision
  -> prepare order intent
  -> pending human approval
  -> explicit approve/reject decision
  -> verify OpenAlgo analyze mode
  -> create local order
  -> submit /api/v1/placeorder
  -> store OpenAlgo order ID
  -> query /api/v1/orderstatus
  -> reconcile local order state
  -> store sanitized OpenAlgo snapshot
```

## Safety Rules

- The LLM has no approval tool.
- The bridge never toggles OpenAlgo analyzer mode.
- Submission is refused unless OpenAlgo proves `analyze_mode=true`.
- Live execution is refused unless explicitly configured, broker readiness
  passes, and a live-mode approval exists.
- An ambiguous network failure becomes `submission_uncertain`.
- `submission_uncertain` is not automatically retried.
- Secrets are sent to OpenAlgo but never stored in order or snapshot tables.

## State Model

Order intent:

```text
pending_approval -> approved -> submitting -> submitted
                 -> rejected
submitting -> submission_uncertain
submitted -> open | pending | filled | rejected | cancelled
```

Local order:

```text
created -> submitted -> filled | rejected | cancelled | failed
```

## Operator Workflow Commands

Prepare a risk decision, order intent, and approval request:

```powershell
python scripts\openalgo_sandbox_workflow.py prepare `
  --symbol NHPC --exchange NSE --side BUY --product MIS `
  --quantity 1 --reference-price 100
```

Review the returned intent and approval IDs. Then approve and submit:

```powershell
python scripts\openalgo_sandbox_workflow.py approve-and-submit `
  --intent-id <intent_id> --actor <your_name> `
  --reason "Reviewed for operator sandbox workflow" `
  --confirm I_UNDERSTAND_THIS_IS_AN_OPENALGO_SANDBOX_ORDER
```

Reconcile OpenAlgo status:

```powershell
python scripts\openalgo_sandbox_workflow.py reconcile `
  --intent-id <intent_id>
```

These commands require `OPENALGO_API_KEY` and an already-running OpenAlgo server
with analyzer mode enabled.

## Interview Defense

Why not submit immediately after risk approval?

Risk approval means the order fits machine policy. Human approval means the user
consents to the external action. They solve different problems.

Why store both an order intent and an order?

The intent represents a proposed action before external submission. The order
represents an accepted submission lifecycle. Keeping them separate prevents a
pending approval from pretending to be an executed order.

Why not retry a timeout?

The request may have reached OpenAlgo even if the response was lost. Retrying
could create a duplicate order, so the platform enters manual review instead.
