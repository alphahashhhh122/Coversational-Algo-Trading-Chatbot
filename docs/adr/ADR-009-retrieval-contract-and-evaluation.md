# ADR-009: Measured Retrieval Contract And Rank Fusion

## Status

Accepted

## Decision

Use a provider-independent `Retriever` contract for governed knowledge search.
The default implementation is explicit BM25 with length normalization, term
frequency saturation, inverse document frequency, and a documented title
weight. Retrieval events store the exact method version.

Semantic retrieval is not simulated when no embedding provider is configured.
Future semantic retrievers implement the same contract and combine with BM25
through reciprocal rank fusion.

Every retrieval release is measured against a versioned question set using:

- Recall@K
- mean reciprocal rank
- nDCG@K
- case-set and corpus SHA-256 identities
- per-query ranked document evidence

## Why

Calling a token-overlap heuristic "RAG" is not enough for a production system.
Likewise, adding embeddings without a benchmark cannot prove an improvement.
The contract separates ranking from document storage and lets lexical,
semantic, and future reranking implementations compete on identical evidence.

## Release Gate

The current release requires Recall@5 of 1.0, MRR of at least 0.8, and nDCG@5
of at least 0.8. A new retriever must not reduce any safety- or
architecture-critical query below the accepted baseline.

## Scale Path

For a larger corpus, chunk text and embeddings move to an indexed retrieval
store while document identity, access policy, source provenance, evaluation
runs, and audit events remain in the transactional platform database.
