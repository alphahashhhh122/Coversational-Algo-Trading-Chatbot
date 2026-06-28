# ADR-004: Two-Stage Strategy Robustness Evaluation

## Status

Accepted

## Decision

Use a two-stage robustness workflow:

1. Screen a bounded parameter grid using the same deterministic strategy and
   execution-ledger code as canonical backtests.
2. Select parameters from the chronological training window only.
3. Evaluate the selected parameters on a later, untouched test window.
4. Persist the selected train and test executions through the complete signal,
   risk, order, fill, performance, and manifest workflow.

Candidate screening stores compact trial metrics rather than creating complete
order timelines for every candidate.

## Why

- Prevents selecting parameters using future test results.
- Avoids duplicating P&L, fee, and slippage calculations.
- Keeps parameter searches bounded and operationally understandable.
- Preserves full audit evidence for the selected candidate.
- Avoids flooding transactional tables with low-value intermediate orders.

## Alternatives Rejected

### Full order workflow for every candidate

This is maximally auditable but creates excessive signal, risk, order, and fill
records during parameter exploration. It also makes synchronous research
unnecessarily slow. It remains an option for regulated validation jobs.

### Separate vectorized screening formula

This is faster but risks semantic drift from the canonical engine. The platform
instead shares one `ResearchLedger` for costs, slippage, fills, and performance.

### Random or Bayesian optimization now

These methods are useful for large search spaces but add optimization complexity
before the validation and experiment contracts are mature. A bounded explicit
grid is easier to reproduce, review, and defend.

## Consequences

- The platform produces train, test, benchmark, parameter-sensitivity, and
  explicit verdict evidence.
- The verdict is a transparent rule-based research label, not a prediction.
- Large searches should later run asynchronously through dedicated workers.
- Walk-forward and regime-based validation remain later extensions.
