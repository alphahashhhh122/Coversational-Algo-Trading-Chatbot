# AI-First Target Architecture

## Implemented Request Path

```text
User / Web UI
  -> FastAPI
  -> OpenTelemetry request span
  -> OpenAI Responses Orchestrator or explicit offline fallback
  -> Strict Pydantic Tool Registry
  -> Deterministic Services
  -> DuckDB repositories / read-only OpenAlgo adapter
  -> Tool-call and audit persistence with trace correlation
  -> Structural evaluator
  -> Grounded response with evidence IDs
```

The LLM interprets intent, selects registered tools, and explains returned
results. It never performs trading calculations, writes SQL, or talks directly
to a broker. Strategies, freshness, risk, orders, performance, and retrieval are
normal Python services with testable contracts.

Every governed tool invocation creates an explicit child span. Its trace and
span identifiers are stored with the tool-call lifecycle and immutable audit
events, allowing a request returned to the user to be correlated with logs,
Jaeger, orchestration evidence, and database state.

## Operator Workspace

The FastAPI application serves a responsive operational frontend at `/`. It
uses the same REST endpoints as external clients and contains no duplicate
strategy, risk, order, or performance logic.

Implemented views:

- grounded chat with evaluator and evidence inspection
- operational health and credential-state indicators
- governed dataset coverage, quality, and current-market freshness
- recent strategy runs and stored equity-curve visualization
- pending human approvals with explicit approve/reject actions

Authentication and role-based approval identity remain mandatory before public
or multi-user deployment. Browser screenshot verification is also pending
because the local browser automation helper was unavailable on this host.

## Orchestration Decision

The current production path uses the OpenAI Responses API with strict function
tools. It is smaller, easier to test, and easier to explain than introducing a
multi-agent framework before the tool contracts are stable.

The offline router is a clearly labelled degraded mode for local tests and demos
without an API key. It is not represented as the production AI system.

Agents SDK, LangGraph, or another workflow framework becomes justified when the
platform needs durable long-running workflows, independent specialist state,
parallel work, or human approval checkpoints that cannot be managed cleanly by
the application service layer.

## Implemented Tools

- dataset listing and detail
- purpose-aware freshness assessment
- governed knowledge-document listing and retrieval
- strategy-plugin listing
- backtest execution
- stored run, performance, risk, and order retrieval
- authenticated OpenAlgo snapshots when configured
- approval-gated analyzer order preparation, submission, and reconciliation

There are 11 default tools and 16 when OpenAlgo credentials are enabled.

## Data And RAG Boundary

Structured facts stay in governed DuckDB tables and are accessed through typed
tools. This includes market data, runs, signals, risk, orders, trades, funds,
performance, conversations, and audit state.

RAG is used only for unstructured material such as architecture documents,
policies, professor notes, and operating manuals. The current retrieval layer:

- indexes a curated corpus
- chunks documents deterministically
- deduplicates by SHA-256
- uses explicit BM25 ranking with length normalization, IDF, and title weighting
- stores every retrieval event
- returns document and chunk provenance

The retriever is provider-independent and reciprocal-rank fusion is implemented
for future lexical plus semantic ranking. Embeddings are intentionally not
simulated while no provider is configured. The current system must be described
as measured governed BM25 retrieval, not a semantic vector database.

## Freshness Contract

Freshness is purpose-aware:

- `historical_research`: closed data is acceptable when coverage and quality pass
- `current_market`: data older than the configured threshold is stale
- `broker_state`: account snapshots use a stricter threshold
- `reference`: symbols and calendars use a slower-changing threshold

Every assessment stores its policy, reference time, age, status, and reason.
Backtests require a successful historical-research assessment before execution.

## Safety And Evaluation

The evaluator checks:

- evidence exists for tool-backed answers
- metric claims match persisted backtest output
- financial guarantee language is rejected
- historical simulations are labelled
- retrieval, document, chunk, dataset, run, and tool-call IDs are exposed

The versioned AI regression harness additionally measures intent routing,
Pydantic argument validity, role-based tool availability, retrieval provenance,
metric grounding, and financial-safety policies. Every run stores the case-set
SHA-256 and per-case evidence. The verified offline baseline is 27/27 cases;
the configured OpenAI model uses the same suite once credentials are supplied.

Live order placement remains disabled. The OpenAlgo analyzer bridge implements
human approval, analyzer-mode proof, idempotent intent preparation, sandbox
submission, and reconciliation. It has automated contract and HTTP tests; an
actual local OpenAlgo submission remains pending configured credentials and an
explicit user-confirmed sandbox demo.

## Verified State On June 20, 2026

- 69 automated tests passing in the complete regression suite
- durable robustness tasks with retries and stale-worker recovery
- append-only portfolio ledger with atomic risk reservations and kill switch
- verifiable database backups with a successful 51-table restore drill
- 27-case versioned AI evaluation baseline at 100%
- 8-query BM25 baseline: Recall@5 1.0, MRR 0.9375, nDCG@5 0.953866
- 66,080 governed NIFTY options rows
- 7 curated knowledge documents and 22 chunks
- real robustness experiment `robust_9a9b1aa14f1f`
- selected out-of-sample result correctly labelled `insufficient_sample`

Backtest metrics are historical simulation evidence, not performance promises.
