# ADR-008: Versioned AI Routing And Safety Evaluation

## Status

Accepted

## Decision

Maintain a versioned JSONL evaluation set covering:

- intent-to-tool routing
- Pydantic argument validity
- role-based tool availability
- financial guarantee rejection
- metric grounding
- historical-simulation labelling
- retrieval and tool evidence propagation

Each run stores its case-set SHA-256, per-case expected and actual values,
latency, category scores, model/mode, and a JSON artifact.

## Why

Unit tests prove deterministic services, but they do not measure whether the
orchestration layer chooses the correct capability or whether generated answers
survive grounding checks. A repeatable evaluation suite makes prompt, model, and
tool-description changes measurable.

## Modes

`offline` evaluates the deterministic degraded router and structural response
evaluator without external credentials. `configured` sends the same routing
cases through the configured OpenAI model. Configured runs are explicit because
they incur provider calls and cost.

## Release Rule

No routing prompt, model, tool contract, or evaluator-policy change should be
promoted when it lowers a safety category score. Failures are reviewed by case;
the aggregate pass rate is not a substitute for financial-safety invariants.
