"""Long-term memory for the agent layer.

A small, honest key/value store so the assistant can remember things across
sessions — free-text notes the user asks it to keep (preferences, risk profile)
and a compact summary of the last research briefing for each symbol. It stores
only what it is told or what it actually produced; it never invents facts, and
recall returns exactly what was saved (with the timestamp) so the user can see
how fresh it is.

The watchlist is deliberately *not* duplicated here — that already lives in
``watchlist_symbols`` (``ScreenerService``); memory complements it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect

_NOTE = "note"
_RESEARCH = "research"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    # -- writes ---------------------------------------------------------------

    def remember_note(self, text: str, *, created_by: str = "chat") -> dict[str, Any]:
        """Store a free-text thing to remember (a preference, a risk profile).

        Each note is distinct, so notes accumulate rather than overwrite.
        """

        text = (text or "").strip()
        if not text:
            raise ValueError("There's nothing to remember — the note is empty.")
        memory_id = uuid.uuid4().hex
        key = memory_id  # notes never collide; the id is the key
        now = _utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO agent_memory VALUES (?, ?, ?, ?, ?, ?, ?)",
                [memory_id, _NOTE, key, text, created_by, now, now],
            )
        finally:
            con.close()
        return {"memory_id": memory_id, "kind": _NOTE, "content": text}

    def save_research(
        self, symbol: str, summary: str, *, created_by: str = "agent"
    ) -> dict[str, Any]:
        """Upsert the latest research summary for a symbol (one per symbol)."""

        symbol = (symbol or "").upper().strip()
        summary = (summary or "").strip()
        if not symbol or not summary:
            return {"status": "skipped"}
        now = _utc_now()
        con = connect(self.db_path)
        try:
            existing = con.execute(
                "SELECT memory_id, created_at FROM agent_memory "
                "WHERE kind = ? AND memory_key = ?",
                [_RESEARCH, symbol],
            ).fetchone()
            if existing:
                con.execute(
                    "UPDATE agent_memory SET content = ?, updated_at = ? "
                    "WHERE memory_id = ?",
                    [summary, now, existing[0]],
                )
            else:
                con.execute(
                    "INSERT INTO agent_memory VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [uuid.uuid4().hex, _RESEARCH, symbol, summary, created_by, now, now],
                )
        finally:
            con.close()
        return {"status": "saved", "symbol": symbol}

    def forget_note(self, memory_id: str) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            con.execute(
                "DELETE FROM agent_memory WHERE memory_id = ? AND kind = ?",
                [memory_id, _NOTE],
            )
        finally:
            con.close()
        return {"status": "forgotten", "memory_id": memory_id}

    # -- reads ----------------------------------------------------------------

    def list_notes(self, limit: int = 50) -> list[dict[str, Any]]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT memory_id, content, created_at FROM agent_memory "
                "WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
                [_NOTE, limit],
            ).fetchall()
        finally:
            con.close()
        return [
            {"memory_id": r[0], "content": r[1], "created_at": _iso(r[2])}
            for r in rows
        ]

    def get_research(self, symbol: str) -> dict[str, Any] | None:
        symbol = (symbol or "").upper().strip()
        if not symbol:
            return None
        con = connect(self.db_path)
        try:
            row = con.execute(
                "SELECT content, updated_at FROM agent_memory "
                "WHERE kind = ? AND memory_key = ?",
                [_RESEARCH, symbol],
            ).fetchone()
        finally:
            con.close()
        if not row:
            return None
        return {"symbol": symbol, "content": row[0], "updated_at": _iso(row[1])}

    def recall(self, query: str | None = None) -> dict[str, Any]:
        """Everything worth surfacing when the user asks 'what do you remember?'.

        If ``query`` names a symbol we also pull that symbol's research summary.
        """

        notes = self.list_notes()
        research: dict[str, Any] | None = None
        if query:
            token = _first_ticker_like(query)
            if token:
                research = self.get_research(token)
        return {"notes": notes, "research": research}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


_RECALL_STOPWORDS = {
    "WHAT", "WHEN", "WHERE", "WHO", "WHY", "HOW", "DID", "DO", "WE", "YOU",
    "THE", "ON", "IN", "OF", "ABOUT", "FIND", "FOUND", "RESEARCH",
    "RESEARCHED", "KNOW", "REMEMBER", "NOTE", "NOTES", "LATEST", "SHOW",
    "TELL", "ME", "MY", "OUR", "AND", "FOR", "IS", "WAS", "HAVE", "HAD",
}


def _first_ticker_like(text: str) -> str | None:
    """Pull the most likely symbol out of a recall question.

    Prefers a token that resolves to a real instrument name; otherwise falls
    back to the last non-stopword uppercase token (symbols usually trail, as in
    "what did we find on RELIANCE").
    """

    import re

    from .instrument_names import company_name

    candidates = [
        token
        for token in re.findall(r"\b[A-Z][A-Z0-9&.-]{1,}\b", (text or "").upper())
        if token not in _RECALL_STOPWORDS
    ]
    for token in candidates:
        if company_name(token):
            return token
    return candidates[-1] if candidates else None
