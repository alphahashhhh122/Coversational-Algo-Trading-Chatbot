# Operations Failure Runbook

## Alert Lifecycle

`active` means the threshold is currently breached. `acknowledged` means an
operator owns the incident but the condition remains. `resolved` is set only
when a later evaluation proves the metric is back within threshold.

Acknowledgement is not resolution. Never manually close a financial or broker
alert merely to clear the dashboard.

## Uncertain Broker Submissions

1. Stop new submissions for the affected portfolio.
2. Query OpenAlgo orderbook and tradebook using the stored idempotency key and
   broker order ID when available.
3. Reconcile the local intent and order state.
4. Do not retry an ambiguous submission until broker state proves no order was
   accepted.

## Stale Running Tasks

1. Inspect the task payload, worker lease, and last error.
2. Confirm no process is still executing the handler.
3. Run the task worker recovery path once.
4. Escalate repeated stale leases as application or host instability.

## Failed Tasks

Inspect `error_type`, `error_message`, attempt count, and the associated domain
result. Correct deterministic input failures before retrying. External transient
failures may use the bounded retry path.

## Failed Jobs

Inspect the latest `job_runs` record and whether the job was disabled at its
retry limit. Repair the source or configuration, then explicitly re-enable or
run the job. Never silently discard a failed maintenance result.

## Stale Market Data

Confirm the dataset purpose. Historical datasets may remain valid for research
while being stale for current-market questions. Refresh or ingest a current
dataset before allowing current-market analysis.

## Overdue Approvals

Verify the approver queue and the requesting user. Reject obsolete intents.
Never approve based only on age; re-check current portfolio risk and broker mode.

## Stale Or Missing Backup

Create a new backup, verify checksums, and complete a temporary restore. If
verification fails, retain the failed archive for diagnosis and do not replace
the last known-good backup.

## Failed AI Evaluation

Review failed cases by category. A safety, authorization, prompt-injection, or
grounding regression blocks release even when the aggregate score remains high.

## Failed Retrieval Evaluation

Inspect per-query ranks and corpus identity. Re-index changed documents, then
rerun the benchmark. Do not promote a retriever that lowers an
architecture-critical query below the accepted baseline.
