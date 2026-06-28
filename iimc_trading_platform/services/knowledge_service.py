from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .retrieval import BM25Retriever, RetrievalDocument, Retriever, tokenize


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class KnowledgeService:
    def __init__(
        self,
        db_path: Path,
        retriever: Retriever | None = None,
    ) -> None:
        self.db_path = db_path
        self.retriever = retriever or BM25Retriever()

    def index_text(
        self,
        *,
        title: str,
        source_uri: str,
        text: str,
        document_type: str = "markdown",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("Knowledge document is empty")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        con = connect(self.db_path)
        try:
            existing = con.execute(
                """
                SELECT document_id
                FROM knowledge_documents
                WHERE sha256 = ?
                """,
                [digest],
            ).fetchone()
            existing_source = con.execute(
                """
                SELECT document_id
                FROM knowledge_documents
                WHERE source_uri = ?
                ORDER BY ingested_at DESC
                LIMIT 1
                """,
                [source_uri],
            ).fetchone()
        finally:
            con.close()
        if existing:
            return self.get_document(existing[0])

        document_id = (
            existing_source[0]
            if existing_source
            else f"doc_{uuid.uuid4().hex[:12]}"
        )
        chunks = _chunk_text(normalized)
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            if existing_source:
                con.execute(
                    "DELETE FROM knowledge_chunks WHERE document_id = ?",
                    [document_id],
                )
                con.execute(
                    """
                    UPDATE knowledge_documents
                    SET title = ?, sha256 = ?, document_type = ?,
                        status = 'indexed', metadata_json = ?,
                        ingested_at = ?
                    WHERE document_id = ?
                    """,
                    [
                        title,
                        digest,
                        document_type,
                        json.dumps(
                            metadata or {},
                            sort_keys=True,
                            default=str,
                        ),
                        now,
                        document_id,
                    ],
                )
            else:
                con.execute(
                    """
                    INSERT INTO knowledge_documents VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        document_id,
                        title,
                        source_uri,
                        digest,
                        document_type,
                        "indexed",
                        json.dumps(
                            metadata or {},
                            sort_keys=True,
                            default=str,
                        ),
                        now,
                    ],
                )
            con.executemany(
                """
                INSERT INTO knowledge_chunks VALUES (
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    [
                        f"chunk_{uuid.uuid4().hex[:12]}",
                        document_id,
                        index,
                        chunk,
                        len(tokenize(chunk)),
                        json.dumps(
                            {"title": title, "source_uri": source_uri},
                            sort_keys=True,
                        ),
                        now,
                    ]
                    for index, chunk in enumerate(chunks)
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return self.get_document(document_id)

    def list_documents(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT d.document_id, d.title, d.source_uri, d.document_type,
                       d.status, d.ingested_at, COUNT(c.chunk_id)
                FROM knowledge_documents AS d
                LEFT JOIN knowledge_chunks AS c
                  ON c.document_id = d.document_id
                GROUP BY d.document_id, d.title, d.source_uri,
                         d.document_type, d.status, d.ingested_at
                ORDER BY d.ingested_at DESC
                """
            ).fetchall()
        finally:
            con.close()
        return {
            "documents": [
                {
                    "document_id": row[0],
                    "title": row[1],
                    "source_uri": row[2],
                    "document_type": row[3],
                    "status": row[4],
                    "ingested_at": row[5],
                    "chunk_count": row[6],
                }
                for row in rows
            ]
        }

    def get_document(self, document_id: str) -> dict[str, Any]:
        documents = self.list_documents()["documents"]
        for document in documents:
            if document["document_id"] == document_id:
                return document
        raise ValueError(f"Knowledge document not found: {document_id}")

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT c.chunk_id, c.document_id, c.content,
                       d.title, d.source_uri
                FROM knowledge_chunks AS c
                JOIN knowledge_documents AS d
                  ON d.document_id = c.document_id
                WHERE d.status = 'indexed'
                ORDER BY d.ingested_at DESC, c.chunk_index
                """
            ).fetchall()
        finally:
            con.close()

        documents = [
            RetrievalDocument(
                chunk_id=row[0],
                document_id=row[1],
                content=row[2],
                title=row[3],
                source_uri=row[4],
            )
            for row in rows
        ]
        ranked = self.retriever.rank(query, documents, limit=limit)
        matches = [
            {
                "chunk_id": item.document.chunk_id,
                "document_id": item.document.document_id,
                "content": item.document.content,
                "title": item.document.title,
                "source_uri": item.document.source_uri,
                "rank": item.rank,
                "score": item.score,
                "component_scores": item.component_scores,
            }
            for item in ranked
        ]
        retrieval_id = f"retr_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO retrieval_events VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    retrieval_id,
                    session_id,
                    query,
                    json.dumps(
                        sorted({match["document_id"] for match in matches})
                    ),
                    json.dumps([match["chunk_id"] for match in matches]),
                    self.retriever.name,
                    utc_now(),
                ],
            )
        finally:
            con.close()
        return {
            "retrieval_id": retrieval_id,
            "query": query,
            "method": self.retriever.name,
            "matches": matches,
        }


def _chunk_text(text: str, max_words: int = 220) -> list[str]:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        words = paragraph.split()
        if current and current_words + len(words) > max_words:
            chunks.append("\n\n".join(current))
            current = []
            current_words = 0
        if len(words) > max_words:
            for start in range(0, len(words), max_words):
                chunks.append(" ".join(words[start : start + max_words]))
            continue
        current.append(paragraph)
        current_words += len(words)
    if current:
        chunks.append("\n\n".join(current))
    return chunks
