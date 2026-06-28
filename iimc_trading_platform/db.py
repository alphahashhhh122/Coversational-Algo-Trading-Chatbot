from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

# DuckDB may probe optional pandas integrations. This project intentionally uses
# DuckDB's native APIs and does not require pandas.
sys.modules.setdefault("pandas", None)

import duckdb


DEFAULT_DB_PATH = Path("data/iimc_platform.duckdb")
_LOCKS_GUARD = threading.Lock()
_DB_LOCKS: dict[Path, threading.RLock] = {}


class SerializedDuckDBConnection:
    """DuckDB connection that releases the per-file lock on close."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        lock: threading.RLock,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __enter__(self) -> "SerializedDuckDBConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._connection.close()
        finally:
            self._closed = True
            self._lock.release()


def _lock_for(db_path: Path) -> threading.RLock:
    resolved = db_path.resolve()
    with _LOCKS_GUARD:
        if resolved not in _DB_LOCKS:
            _DB_LOCKS[resolved] = threading.RLock()
        return _DB_LOCKS[resolved]


def connect(db_path: Path = DEFAULT_DB_PATH) -> SerializedDuckDBConnection:
    """Open a serialized DuckDB connection without changing schema state."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(db_path)
    lock.acquire()
    try:
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                connection = duckdb.connect(str(db_path))
                return SerializedDuckDBConnection(connection, lock)
            except (duckdb.IOException, duckdb.BinderException) as exc:
                last_error = exc
                if attempt == 5:
                    break
                time.sleep(0.1 * (attempt + 1))
        assert last_error is not None
        raise last_error
    except Exception:
        lock.release()
        raise
