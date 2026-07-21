"""Resolve broker ticker symbols to readable company names.

Reads the OpenAlgo master contract (``openalgo.db`` ``symtoken`` table)
read-only and caches the symbol->name map per exchange. Everything falls
back to the ticker when a name is unavailable, so callers never break and
nothing is fabricated.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path


def _default_root() -> Path:
    return Path.home() / "openalgo"


def _pretty(raw: str) -> str:
    """'RELIANCE INDUSTRIES LTD' -> 'Reliance Industries'."""
    name = " ".join(raw.strip().split()).title()
    for suffix in (" Ltd.", " Ltd", " Limited"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


@lru_cache(maxsize=16)
def _name_map(root_str: str, exchange: str) -> dict[str, str]:
    database_path = Path(root_str) / "db" / "openalgo.db"
    if not database_path.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT symbol, name FROM symtoken WHERE exchange = ?",
                [exchange.upper()],
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    mapping: dict[str, str] = {}
    for symbol, name in rows:
        if symbol and name and symbol not in mapping:
            mapping[str(symbol).upper()] = _pretty(str(name))
    return mapping


def company_name(
    symbol: str | None,
    exchange: str = "NSE",
    *,
    openalgo_root: Path | None = None,
) -> str | None:
    """Readable company name for a ticker, or None if unknown."""
    if not symbol:
        return None
    root = openalgo_root or _default_root()
    return _name_map(str(root), (exchange or "NSE").upper()).get(
        str(symbol).upper()
    )
