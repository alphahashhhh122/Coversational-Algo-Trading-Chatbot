"""Shared test harness for the full-app (integration) test modules.

Building a FastAPI app costs ~4.2s (123 routes + pydantic schema generation +
service construction), and the suite did it once per *test*. This harness pays
that cost once per test *class* and resets the database between tests instead.

The reset is the important part: it must leave the database exactly as a fresh
one would, or tests start leaking into each other. ``reset_database`` deletes
every row from every table, so a test that counts rows without scoping its
query (``SELECT COUNT(*) FROM approval_requests WHERE status='pending'`` in
test_api_chat is the canary) still sees only its own data.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database

def _table_names(con: Any) -> list[str]:
    return [
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'"
        ).fetchall()
    ]


def _snapshot_startup_rows(db_path: Path) -> dict[str, list[tuple]]:
    """Rows written by app construction itself (e.g. the agent roster).

    These are startup state, not test data: without restoring them a reset
    would leave the app referring to a roster that no longer exists. Snapshotting
    (rather than simply preserving the table) means rows a *test* adds — an
    authored agent, say — are still cleared.
    """

    con = connect(db_path)
    try:
        snapshot: dict[str, list[tuple]] = {}
        for table in _table_names(con):
            rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
            if rows:
                snapshot[table] = rows
        return snapshot
    finally:
        con.close()


def reset_database(
    db_path: Path, startup_rows: dict[str, list[tuple]] | None = None
) -> None:
    """Empty every table, then restore app-startup rows.

    The result is indistinguishable from a freshly built app on a fresh
    database — which is what the per-test isolation contract requires.
    """

    con = connect(db_path)
    try:
        for table in _table_names(con):
            con.execute(f'DELETE FROM "{table}"')
        for table, rows in (startup_rows or {}).items():
            if not rows:
                continue
            placeholders = ", ".join("?" for _ in rows[0])
            for row in rows:
                con.execute(
                    f'INSERT INTO "{table}" VALUES ({placeholders})', list(row)
                )
    finally:
        con.close()


class AppHarness:
    """One app + database per test class; cheap reset per test."""

    def __init__(self, config_overrides: dict[str, Any] | None = None) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "test.duckdb"
        self.artifacts_dir = self.root / "artifacts"
        self.artifacts_dir.mkdir()
        initialize_database(self.db_path)
        self.config = AppConfig(
            database_path=self.db_path,
            artifacts_dir=self.artifacts_dir,
            openalgo_root=self.root,
            **(config_overrides or {}),
        )
        self.client = TestClient(create_app(self.config))
        # Captured after construction so a reset can restore exactly what the
        # app writes at startup (the agent roster, for instance).
        self._startup_rows = _snapshot_startup_rows(self.db_path)

    def reset(self, seed: Callable[[], None] | None = None) -> None:
        """Clear all data, then re-apply the class's fixtures."""
        reset_database(self.db_path, self._startup_rows)
        if seed is not None:
            seed()

    def close(self) -> None:
        self.temp_dir.cleanup()
