from __future__ import annotations

import hashlib
import html as html_module
import ipaddress
import json
import re
import socket
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from ..db import connect
from .retrieval import BM25Retriever, RetrievalDocument, Retriever, tokenize

_MAX_FETCH_BYTES = 1_500_000
_FETCH_TIMEOUT_SECONDS = 20.0


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
                       d.status, d.ingested_at, COUNT(c.chunk_id),
                       ANY_VALUE(d.metadata_json)
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
        documents = []
        for row in rows:
            try:
                metadata = json.loads(row[7] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            documents.append(
                {
                    "document_id": row[0],
                    "title": row[1],
                    "source_uri": row[2],
                    "document_type": row[3],
                    "status": row[4],
                    "ingested_at": row[5],
                    "chunk_count": row[6],
                    "corpus": metadata.get("corpus", "unknown"),
                }
            )
        return {"documents": documents}

    def fetch_and_index_url(
        self,
        url: str,
        *,
        title: str | None = None,
        fetched_by: str = "chat",
    ) -> dict[str, Any]:
        """Download a public web page, extract its text, and index it."""
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http and https URLs can be fetched")
        if not parsed.hostname:
            raise ValueError("The URL has no hostname")
        _reject_private_host(parsed.hostname)
        request = urllib.request.Request(
            url.strip(),
            headers={"User-Agent": "iimc-trading-platform/1.0 (+local)"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=_FETCH_TIMEOUT_SECONDS
            ) as response:
                raw = response.read(_MAX_FETCH_BYTES + 1)
                final_url = response.geturl()
        except Exception as exc:
            raise ValueError(f"Could not fetch the page: {exc}") from exc
        final_parsed = urlparse(final_url)
        if final_parsed.hostname:
            _reject_private_host(final_parsed.hostname)
        if len(raw) > _MAX_FETCH_BYTES:
            raise ValueError(
                "The page is larger than the 1.5 MB fetch limit"
            )
        html_text = raw.decode("utf-8", errors="replace")
        page_title, text = _extract_web_text(html_text)
        if not text.strip():
            raise ValueError(
                "No readable text could be extracted from that page"
            )
        document = self.index_text(
            title=title or page_title or parsed.hostname,
            source_uri=final_url,
            text=text,
            document_type="web",
            metadata={
                "corpus": "web_fetched",
                "fetched_by": fetched_by,
                "fetched_at": utc_now().isoformat(),
            },
        )
        return {**document, "source_url": final_url}

    def search_and_fetch(
        self,
        query: str,
        *,
        fetched_by: str = "chat",
    ) -> dict[str, Any]:
        """Find a public page for a free-text query and index it.

        Uses the keyless DuckDuckGo HTML endpoint to locate a URL, then reuses
        the SSRF-guarded ``fetch_and_index_url``. Fails safely (clear message)
        when search is unreachable, empty, or only yields unreadable results
        (e.g. PDFs).
        """
        cleaned = " ".join(query.split()).strip()
        if not cleaned:
            raise ValueError("Tell me what document to look for.")
        # DuckDuckGo's HTML endpoint returns server-rendered results only for a
        # POST form submission (a GET returns a script-only shell).
        request = urllib.request.Request(
            "https://html.duckduckgo.com/html/",
            data=urlencode({"q": cleaned}).encode("utf-8"),
            method="POST",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "iimc-trading-platform/1.0"
                ),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=_FETCH_TIMEOUT_SECONDS
            ) as response:
                page = response.read(_MAX_FETCH_BYTES).decode(
                    "utf-8", errors="replace"
                )
        except Exception as exc:
            raise ValueError(
                f"Web search is unavailable right now ({exc}). You can paste a "
                "URL, or upload the document instead."
            ) from exc
        candidate_urls = _duckduckgo_result_urls(page)
        if not candidate_urls:
            raise ValueError(
                f"I couldn't find a readable page for {cleaned!r}. Try a more "
                "specific title, paste a URL, or upload the document."
            )
        last_error = "no readable result"
        for url in candidate_urls[:5]:
            if url.lower().split("?")[0].endswith((".pdf", ".zip", ".doc",
                                                    ".docx", ".xls", ".xlsx")):
                last_error = "the top results are files I can't read as text"
                continue
            try:
                result = self.fetch_and_index_url(url, fetched_by=fetched_by)
                return {**result, "search_query": cleaned}
            except ValueError as exc:
                last_error = str(exc)
                continue
        raise ValueError(
            f"I found results for {cleaned!r} but couldn't read them "
            f"({last_error}). Paste a URL or upload the document."
        )

    def find_and_analyze_document(
        self,
        query: str,
        *,
        max_chunks: int = 8,
        fetched_by: str = "chat",
    ) -> dict[str, Any]:
        """Analyze a stored document, or fetch one from the web first."""
        try:
            overview = self.document_overview(query, max_chunks=max_chunks)
            return {**overview, "source": "stored"}
        except ValueError:
            pass
        fetched = self.search_and_fetch(query, fetched_by=fetched_by)
        overview = self.document_overview(
            fetched["document_id"], max_chunks=max_chunks
        )
        return {
            **overview,
            "source": "web_fetched",
            "source_url": fetched.get("source_url"),
        }

    def get_document(self, document_id: str) -> dict[str, Any]:
        documents = self.list_documents()["documents"]
        for document in documents:
            if document["document_id"] == document_id:
                return document
        raise ValueError(f"Knowledge document not found: {document_id}")

    def document_overview(
        self,
        identifier: str,
        *,
        max_chunks: int = 8,
    ) -> dict[str, Any]:
        """Return a stored document's metadata plus ordered chunk excerpts."""
        if not 1 <= max_chunks <= 50:
            raise ValueError("max_chunks must be between 1 and 50")
        wanted = identifier.strip()
        if not wanted:
            raise ValueError("Provide a document title or document_id")
        documents = self.list_documents()["documents"]
        document = next(
            (
                item
                for item in documents
                if item["document_id"] == wanted
                or item["title"].lower() == wanted.lower()
            ),
            None,
        )
        if document is None:
            document = next(
                (
                    item
                    for item in documents
                    if wanted.lower() in item["title"].lower()
                ),
                None,
            )
        if document is None:
            available = ", ".join(
                item["title"] for item in documents[:10]
            ) or "none"
            raise ValueError(
                f"Knowledge document not found: {identifier!r}. "
                f"Stored documents include: {available}."
            )
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT chunk_id, chunk_index, content
                FROM knowledge_chunks
                WHERE document_id = ?
                ORDER BY chunk_index
                """,
                [document["document_id"]],
            ).fetchall()
        finally:
            con.close()
        total_words = sum(len(str(row[2]).split()) for row in rows)
        return {
            "document_id": document["document_id"],
            "title": document["title"],
            "source_uri": document["source_uri"],
            "document_type": document["document_type"],
            "ingested_at": document["ingested_at"],
            "chunk_count": len(rows),
            "total_words": total_words,
            "chunks": [
                {
                    "chunk_id": row[0],
                    "chunk_index": row[1],
                    "content": row[2],
                }
                for row in rows[:max_chunks]
            ],
        }

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
                  AND (
                    d.metadata_json IS NULL
                    OR d.metadata_json NOT LIKE '%curated_project_docs%'
                  )
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


def _reject_private_host(hostname: str) -> None:
    """Block loopback/private/link-local targets (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise ValueError(f"Could not resolve host {hostname!r}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise ValueError(
                "Fetching private, loopback, or link-local addresses is "
                "not allowed"
            )


def _duckduckgo_result_urls(html_text: str) -> list[str]:
    """Extract organic result URLs from a DuckDuckGo HTML results page."""
    urls: list[str] = []
    for match in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"', html_text
    ):
        href = html_module.unescape(match.group(1))
        # DuckDuckGo wraps targets in a /l/?uddg=<encoded-url> redirect.
        if "uddg=" in href:
            query = urlparse(
                href if href.startswith("http") else "https:" + href
            ).query
            values = parse_qs(query).get("uddg")
            if values:
                href = values[0]
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http") and href not in urls:
            urls.append(href)
    return urls


def _extract_web_text(html_text: str) -> tuple[str | None, str]:
    """Return (title, readable text) from raw HTML."""
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>", html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    title = (
        html_module.unescape(title_match.group(1)).strip()[:200]
        if title_match
        else None
    )
    cleaned = re.sub(
        r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>",
        " ",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"</(p|div|li|h[1-6]|tr|section|article|br)>", "\n\n", cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html_module.unescape(cleaned)
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in cleaned.splitlines()
    ]
    paragraphs: list[str] = []
    for line in lines:
        if line:
            paragraphs.append(line)
    text = "\n\n".join(paragraphs)
    return title, text


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
