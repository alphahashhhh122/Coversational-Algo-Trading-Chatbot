from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.knowledge_service import KnowledgeService
from iimc_trading_platform.services.retrieval import (
    BM25Retriever,
    RankedDocument,
    ReciprocalRankFusionRetriever,
    RetrievalDocument,
)
from iimc_trading_platform.services.retrieval_evaluation_service import (
    RetrievalEvaluationService,
)


class _FixedRetriever:
    def __init__(self, name: str, order: list[str]) -> None:
        self.name = name
        self.order = order

    def rank(self, query, documents, *, limit):
        by_id = {item.chunk_id: item for item in documents}
        return [
            RankedDocument(
                document=by_id[chunk_id],
                score=1.0 / rank,
                rank=rank,
                component_scores={self.name: 1.0 / rank},
            )
            for rank, chunk_id in enumerate(
                self.order[:limit],
                start=1,
            )
        ]


class RetrievalTest(unittest.TestCase):
    def test_bm25_ranks_relevant_document_first(self) -> None:
        documents = [
            RetrievalDocument(
                "chunk_1",
                "doc_1",
                "Risk Controls",
                "risk.md",
                "Atomic reservations prevent concurrent orders from "
                "spending the same cash.",
            ),
            RetrievalDocument(
                "chunk_2",
                "doc_2",
                "Reports",
                "reports.md",
                "Reports summarize historical strategy performance.",
            ),
        ]

        ranked = BM25Retriever().rank(
            "How do atomic risk reservations prevent double spending?",
            documents,
            limit=2,
        )

        self.assertEqual(ranked[0].document.chunk_id, "chunk_1")
        self.assertEqual(len(ranked), 1)
        self.assertGreater(ranked[0].score, 0)

    def test_rank_fusion_combines_independent_retrievers(self) -> None:
        documents = [
            RetrievalDocument(
                f"chunk_{index}",
                f"doc_{index}",
                f"Title {index}",
                f"{index}.md",
                f"content {index}",
            )
            for index in range(1, 4)
        ]
        fusion = ReciprocalRankFusionRetriever(
            [
                (_FixedRetriever("lexical", ["chunk_1", "chunk_2"]), 1.0),
                (_FixedRetriever("semantic", ["chunk_2", "chunk_3"]), 1.0),
            ]
        )

        ranked = fusion.rank("query", documents, limit=3)

        self.assertEqual(ranked[0].document.chunk_id, "chunk_2")
        self.assertEqual(
            set(ranked[0].component_scores),
            {"lexical", "semantic"},
        )

    def test_retrieval_evaluation_persists_quality_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "retrieval.duckdb"
            cases_path = root / "cases.jsonl"
            initialize_database(db_path)
            knowledge = KnowledgeService(db_path)
            knowledge.index_text(
                title="RISK POLICY",
                source_uri="risk.md",
                text=(
                    "Atomic reservations prevent two orders from spending "
                    "the same available cash."
                ),
            )
            knowledge.index_text(
                title="REPORTING",
                source_uri="reports.md",
                text="Reports contain historical performance evidence.",
            )
            cases_path.write_text(
                json.dumps(
                    {
                        "case_id": "risk",
                        "query": "How is double spending prevented?",
                        "relevant_titles": ["RISK POLICY"],
                    }
                ),
                encoding="utf-8",
            )
            service = RetrievalEvaluationService(
                db_path,
                root / "artifacts",
                cases_path,
            )

            result = service.run(created_by="test", top_k=1)

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["recall_at_k"], 1.0)
            self.assertEqual(result["mean_reciprocal_rank"], 1.0)
            self.assertEqual(result["ndcg_at_k"], 1.0)
            self.assertEqual(len(service.list()["evaluations"]), 1)


if __name__ == "__main__":
    unittest.main()
