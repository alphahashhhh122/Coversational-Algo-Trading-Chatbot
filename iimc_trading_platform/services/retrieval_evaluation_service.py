from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .knowledge_service import KnowledgeService


class RetrievalEvaluationService:
    def __init__(
        self,
        db_path: Path,
        artifacts_dir: Path,
        cases_path: Path,
    ) -> None:
        self.db_path = db_path
        self.artifacts_dir = artifacts_dir
        self.cases_path = cases_path

    def run(
        self,
        *,
        created_by: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        if top_k < 1 or top_k > 20:
            raise ValueError("top_k must be between 1 and 20")
        raw_cases = self.cases_path.read_bytes()
        cases = [
            json.loads(line)
            for line in raw_cases.decode("utf-8").splitlines()
            if line.strip()
        ]
        case_ids = [case["case_id"] for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Retrieval evaluation case IDs must be unique")
        corpus = self._corpus_identity()
        service = KnowledgeService(self.db_path)
        eval_run_id = f"retrieval_eval_{uuid.uuid4().hex[:12]}"
        started_at = _utc_now()
        results = []
        for case in cases:
            case_started = time.perf_counter()
            result = service.search(
                case["query"],
                limit=max(top_k, 10),
                session_id=eval_run_id,
            )
            retrieved_titles = _unique_titles(result["matches"])
            relevant_titles = case["relevant_titles"]
            top_titles = retrieved_titles[:top_k]
            relevant_set = set(relevant_titles)
            first_relevant_rank = next(
                (
                    index
                    for index, title in enumerate(
                        retrieved_titles,
                        start=1,
                    )
                    if title in relevant_set
                ),
                None,
            )
            recall_at_k = (
                len(relevant_set.intersection(top_titles))
                / len(relevant_set)
            )
            reciprocal_rank = (
                1.0 / first_relevant_rank
                if first_relevant_rank
                else 0.0
            )
            ndcg_at_k = _ndcg(top_titles, relevant_set, top_k)
            results.append(
                {
                    "case_id": case["case_id"],
                    "query": case["query"],
                    "relevant_titles": relevant_titles,
                    "retrieved_titles": retrieved_titles,
                    "first_relevant_rank": first_relevant_rank,
                    "recall_at_k": round(recall_at_k, 6),
                    "reciprocal_rank": round(reciprocal_rank, 6),
                    "ndcg_at_k": round(ndcg_at_k, 6),
                    "passed": recall_at_k == 1.0,
                    "duration_ms": round(
                        (time.perf_counter() - case_started) * 1000,
                        3,
                    ),
                }
            )
        finished_at = _utc_now()
        case_count = len(results)
        recall_at_k = _mean(
            item["recall_at_k"] for item in results
        )
        mean_reciprocal_rank = _mean(
            item["reciprocal_rank"] for item in results
        )
        ndcg_at_k = _mean(item["ndcg_at_k"] for item in results)
        status = (
            "passed"
            if (
                case_count > 0
                and recall_at_k == 1.0
                and mean_reciprocal_rank >= 0.8
                and ndcg_at_k >= 0.8
            )
            else "failed"
        )
        report = {
            "eval_run_id": eval_run_id,
            "case_set_sha256": hashlib.sha256(raw_cases).hexdigest(),
            "corpus_sha256": corpus["corpus_sha256"],
            "corpus_document_count": corpus["document_count"],
            "corpus_chunk_count": corpus["chunk_count"],
            "retrieval_method": service.retriever.name,
            "case_count": case_count,
            "recall_at_k": round(recall_at_k, 6),
            "mean_reciprocal_rank": round(
                mean_reciprocal_rank,
                6,
            ),
            "ndcg_at_k": round(ndcg_at_k, 6),
            "top_k": top_k,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "results": results,
        }
        report_dir = self.artifacts_dir / "evaluations"
        report_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = report_dir / f"{eval_run_id}.json"
        artifact_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        self._persist(
            report,
            artifact_path=artifact_path,
            created_by=created_by,
        )
        return {
            key: value
            for key, value in report.items()
            if key != "results"
        } | {
            "artifact_path": str(artifact_path.resolve()),
            "failed_cases": [
                item["case_id"]
                for item in results
                if not item["passed"]
            ],
        }

    def list(self, limit: int = 50) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT eval_run_id, case_set_sha256, corpus_sha256,
                       retrieval_method, case_count, recall_at_k,
                       mean_reciprocal_rank, ndcg_at_k, top_k, status,
                       artifact_path, created_by, started_at, finished_at
                FROM retrieval_eval_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "evaluations": [
                {
                    "eval_run_id": row[0],
                    "case_set_sha256": row[1],
                    "corpus_sha256": row[2],
                    "retrieval_method": row[3],
                    "case_count": row[4],
                    "recall_at_k": row[5],
                    "mean_reciprocal_rank": row[6],
                    "ndcg_at_k": row[7],
                    "top_k": row[8],
                    "status": row[9],
                    "artifact_path": row[10],
                    "created_by": row[11],
                    "started_at": row[12],
                    "finished_at": row[13],
                }
                for row in rows
            ]
        }

    def _corpus_identity(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            documents = con.execute(
                """
                SELECT document_id, sha256
                FROM knowledge_documents
                WHERE status = 'indexed'
                ORDER BY document_id
                """
            ).fetchall()
            chunk_count = con.execute(
                """
                SELECT COUNT(*)
                FROM knowledge_chunks AS c
                JOIN knowledge_documents AS d
                  ON d.document_id = c.document_id
                WHERE d.status = 'indexed'
                """
            ).fetchone()[0]
        finally:
            con.close()
        if not documents:
            raise ValueError("No indexed knowledge corpus is available")
        identity = "\n".join(
            f"{document_id}:{sha256}"
            for document_id, sha256 in documents
        )
        return {
            "corpus_sha256": hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest(),
            "document_count": len(documents),
            "chunk_count": chunk_count,
        }

    def _persist(
        self,
        report: dict[str, Any],
        *,
        artifact_path: Path,
        created_by: str,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                INSERT INTO retrieval_eval_runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    report["eval_run_id"],
                    report["case_set_sha256"],
                    report["corpus_sha256"],
                    report["retrieval_method"],
                    report["case_count"],
                    report["recall_at_k"],
                    report["mean_reciprocal_rank"],
                    report["ndcg_at_k"],
                    report["top_k"],
                    report["status"],
                    str(artifact_path.resolve()),
                    created_by,
                    report["started_at"],
                    report["finished_at"],
                ],
            )
            for item in report["results"]:
                con.execute(
                    """
                    INSERT INTO retrieval_eval_results VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        report["eval_run_id"],
                        item["case_id"],
                        item["query"],
                        json.dumps(item["relevant_titles"]),
                        json.dumps(item["retrieved_titles"]),
                        item["first_relevant_rank"],
                        item["recall_at_k"],
                        item["reciprocal_rank"],
                        item["ndcg_at_k"],
                        item["passed"],
                        item["duration_ms"],
                    ],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()


def _unique_titles(matches: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(item["title"] for item in matches))


def _ndcg(
    retrieved_titles: list[str],
    relevant_titles: set[str],
    top_k: int,
) -> float:
    discounted_gain = sum(
        1.0 / math.log2(rank + 1)
        for rank, title in enumerate(
            retrieved_titles[:top_k],
            start=1,
        )
        if title in relevant_titles
    )
    ideal_count = min(len(relevant_titles), top_k)
    ideal_gain = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, ideal_count + 1)
    )
    return discounted_gain / ideal_gain if ideal_gain else 0.0


def _mean(values) -> float:
    materialized = list(values)
    return (
        sum(materialized) / len(materialized)
        if materialized
        else 0.0
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
