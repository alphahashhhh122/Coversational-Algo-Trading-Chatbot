from __future__ import annotations

import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services import BackupService


class BackupServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "source.duckdb"
        self.backup_dir = root / "backups"
        initialize_database(self.db_path)
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO strategy_definitions VALUES (
                    'backup_test', 'backup_test', '1.0.0',
                    'Backup verification record', '{}',
                    TRUE, CURRENT_TIMESTAMP
                )
                """
            )
        finally:
            con.close()
        self.service = BackupService(self.db_path, self.backup_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_verify_and_restore(self) -> None:
        backup = self.service.create(created_by="test")
        verification = self.service.verify(backup["backup_id"])
        restored_path = Path(self.temp_dir.name) / "restored.duckdb"
        restored = self.service.restore(
            backup["backup_id"],
            restored_path,
        )

        self.assertTrue(backup["verified"])
        self.assertTrue(verification["verified"])
        self.assertEqual(restored["restored_path"], str(restored_path.resolve()))
        con = connect(restored_path)
        try:
            count = con.execute(
                """
                SELECT COUNT(*)
                FROM strategy_definitions
                WHERE strategy_id = 'backup_test'
                """
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 1)

    def test_restore_refuses_existing_target(self) -> None:
        backup = self.service.create(created_by="test")
        target = Path(self.temp_dir.name) / "existing.duckdb"
        target.write_bytes(b"do not replace")

        with self.assertRaisesRegex(ValueError, "already exists"):
            self.service.restore(backup["backup_id"], target)
        self.assertEqual(target.read_bytes(), b"do not replace")

    def test_tampered_export_file_fails_verification(self) -> None:
        backup = self.service.create(created_by="test")
        archive_path = Path(backup["archive_path"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive_path, mode="a") as archive:
                exported_file = next(
                    item["path"]
                    for item in backup["files"]
                    if item["path"].endswith(".parquet")
                )
                archive.writestr(exported_file, b"tampered")

        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.service.verify(backup["backup_id"])


if __name__ == "__main__":
    unittest.main()
