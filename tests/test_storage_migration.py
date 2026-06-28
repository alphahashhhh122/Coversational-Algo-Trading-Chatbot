from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database, list_tables
from iimc_trading_platform.services.storage_migration_service import (
    StorageMigrationService,
)


class StorageMigrationServiceTest(unittest.TestCase):
    def test_all_tables_are_classified_and_postgres_ddl_is_generated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "migration.duckdb"
            artifacts = root / "artifacts"
            initialize_database(db_path)
            result = StorageMigrationService(
                db_path,
                artifacts,
            ).generate()

            manifest = json.loads(
                Path(result["manifest_path"]).read_text(
                    encoding="utf-8"
                )
            )
            ddl = Path(result["ddl_path"]).read_text(
                encoding="utf-8"
            )
            classified = {
                item["table"] for item in manifest["tables"]
            }

            self.assertEqual(
                classified,
                set(list_tables(db_path)),
            )
            self.assertIn(
                'CREATE TABLE "app_users"',
                ddl,
            )
            self.assertNotIn(
                'CREATE TABLE "options_ohlcv"',
                ddl,
            )
            self.assertIn(
                'FOREIGN KEY ("session_id")',
                ddl,
            )
            self.assertIn('"metadata_json" JSONB', ddl)
            self.assertTrue(result["foreign_keys_verified"])
            self.assertEqual(len(result["schema_sha256"]), 64)
            self.assertEqual(len(result["ddl_sha256"]), 64)

    def test_analytical_export_round_trips_partitioned_parquet(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "migration.duckdb"
            artifacts = root / "artifacts"
            initialize_database(db_path)
            con = connect(db_path)
            try:
                con.execute(
                    """
                    INSERT INTO options_ohlcv VALUES (
                        'NIFTY', 'NFO', '2026-06-25', '5m', ?,
                        'ATM', 24000, 'CE', 100, 105, 95, 102,
                        10, 20, 15.0, 24010, 'source_1',
                        'source.csv', 'clean', ?
                    )
                    """,
                    [
                        datetime(2026, 6, 20, 9, 15),
                        datetime(2026, 6, 20, 9, 16),
                    ],
                )
            finally:
                con.close()

            result = StorageMigrationService(
                db_path,
                artifacts,
            ).export_analytical_history()

            self.assertEqual(result["source_row_count"], 1)
            self.assertEqual(result["verified_row_count"], 1)
            self.assertEqual(result["file_count"], 1)
            self.assertTrue(Path(result["manifest_path"]).exists())


if __name__ == "__main__":
    unittest.main()
