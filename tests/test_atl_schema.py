from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure.database import (
    initialize_database,
    list_tables,
)

_ATL_TABLES = {"agents", "agent_runs", "agent_scores", "eval_datasets"}


class AtlSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)
        self.path = Path(path)

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_atl_tables_created(self) -> None:
        initialize_database(self.path)
        tables = set(list_tables(self.path))
        self.assertTrue(
            _ATL_TABLES.issubset(tables),
            f"missing: {_ATL_TABLES - tables}",
        )

    def test_migration_is_idempotent_on_existing_db(self) -> None:
        # The migration must apply cleanly to a database that has already been
        # initialised (the live-DB upgrade path) — running it again must not
        # raise or drop anything.
        initialize_database(self.path)
        before = set(list_tables(self.path))
        initialize_database(self.path)
        after = set(list_tables(self.path))
        self.assertEqual(before, after)
        self.assertTrue(_ATL_TABLES.issubset(after))


if __name__ == "__main__":
    unittest.main()
