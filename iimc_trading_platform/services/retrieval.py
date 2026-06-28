from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class RetrievalDocument:
    chunk_id: str
    document_id: str
    title: str
    source_uri: str
    content: str


@dataclass(frozen=True)
class RankedDocument:
    document: RetrievalDocument
    score: float
    rank: int
    component_scores: dict[str, float]


class Retriever(Protocol):
    name: str

    def rank(
        self,
        query: str,
        documents: list[RetrievalDocument],
        *,
        limit: int,
    ) -> list[RankedDocument]: ...


class BM25Retriever:
    name = "lexical_bm25_v2"

    def __init__(
        self,
        *,
        k1: float = 1.2,
        b: float = 0.75,
        title_weight: float = 1.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("BM25 k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("BM25 b must be between 0 and 1")
        if title_weight < 0:
            raise ValueError("Title weight cannot be negative")
        self.k1 = k1
        self.b = b
        self.title_weight = title_weight

    def rank(
        self,
        query: str,
        documents: list[RetrievalDocument],
        *,
        limit: int,
    ) -> list[RankedDocument]:
        if limit < 1:
            raise ValueError("Retrieval limit must be positive")
        query_tokens = tokenize(query)
        if not query_tokens:
            raise ValueError("Knowledge query contains no searchable terms")
        if not documents:
            return []

        content_tokens = [tokenize(item.content) for item in documents]
        title_tokens = [tokenize(item.title) for item in documents]
        combined_unique = [
            set(content) | set(title)
            for content, title in zip(content_tokens, title_tokens)
        ]
        document_frequency = Counter()
        for unique_tokens in combined_unique:
            document_frequency.update(unique_tokens)
        average_length = (
            sum(len(tokens) for tokens in content_tokens)
            / len(content_tokens)
        ) or 1.0
        query_counts = Counter(query_tokens)
        scored: list[tuple[float, float, RetrievalDocument]] = []
        total_documents = len(documents)
        for item, content, title in zip(
            documents,
            content_tokens,
            title_tokens,
        ):
            content_counts = Counter(content)
            title_counts = Counter(title)
            lexical_score = 0.0
            for token, query_frequency in query_counts.items():
                document_frequency_for_token = document_frequency[token]
                if document_frequency_for_token == 0:
                    continue
                inverse_document_frequency = math.log(
                    1
                    + (
                        total_documents
                        - document_frequency_for_token
                        + 0.5
                    )
                    / (document_frequency_for_token + 0.5)
                )
                term_frequency = content_counts[token]
                normalized_content = 0.0
                if term_frequency:
                    denominator = term_frequency + self.k1 * (
                        1
                        - self.b
                        + self.b * len(content) / average_length
                    )
                    normalized_content = (
                        term_frequency * (self.k1 + 1) / denominator
                    )
                normalized_title = min(title_counts[token], 1)
                lexical_score += (
                    inverse_document_frequency
                    * (
                        normalized_content
                        + self.title_weight * normalized_title
                    )
                    * query_frequency
                )
            if lexical_score > 0:
                scored.append((lexical_score, lexical_score, item))
        scored.sort(
            key=lambda value: (
                -value[0],
                value[2].document_id,
                value[2].chunk_id,
            )
        )
        return [
            RankedDocument(
                document=item,
                score=round(score, 8),
                rank=index,
                component_scores={self.name: round(component_score, 8)},
            )
            for index, (score, component_score, item) in enumerate(
                scored[:limit],
                start=1,
            )
        ]


class ReciprocalRankFusionRetriever:
    """Fuse real retrievers without assuming a specific semantic provider."""

    name = "reciprocal_rank_fusion_v1"

    def __init__(
        self,
        retrievers: list[tuple[Retriever, float]],
        *,
        rank_constant: int = 60,
    ) -> None:
        if len(retrievers) < 2:
            raise ValueError("Rank fusion requires at least two retrievers")
        if rank_constant < 1:
            raise ValueError("Rank constant must be positive")
        if any(weight <= 0 for _, weight in retrievers):
            raise ValueError("Retriever weights must be positive")
        self.retrievers = retrievers
        self.rank_constant = rank_constant

    def rank(
        self,
        query: str,
        documents: list[RetrievalDocument],
        *,
        limit: int,
    ) -> list[RankedDocument]:
        candidate_limit = max(limit * 4, 20)
        fused_scores: dict[str, float] = Counter()
        component_scores: dict[str, dict[str, float]] = {}
        document_by_chunk: dict[str, RetrievalDocument] = {}
        for retriever, weight in self.retrievers:
            ranked = retriever.rank(
                query,
                documents,
                limit=candidate_limit,
            )
            for item in ranked:
                chunk_id = item.document.chunk_id
                document_by_chunk[chunk_id] = item.document
                contribution = weight / (
                    self.rank_constant + item.rank
                )
                fused_scores[chunk_id] += contribution
                component_scores.setdefault(chunk_id, {})[
                    retriever.name
                ] = round(contribution, 8)
        ordered = sorted(
            fused_scores,
            key=lambda chunk_id: (
                -fused_scores[chunk_id],
                document_by_chunk[chunk_id].document_id,
                chunk_id,
            ),
        )
        return [
            RankedDocument(
                document=document_by_chunk[chunk_id],
                score=round(fused_scores[chunk_id], 8),
                rank=index,
                component_scores=component_scores[chunk_id],
            )
            for index, chunk_id in enumerate(ordered[:limit], start=1)
        ]


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]
