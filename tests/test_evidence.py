from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.evidence_service import EvidenceService


class EvidenceServiceTest(unittest.TestCase):
    def test_legacy_report_table_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.duckdb"
            con = duckdb.connect(str(db_path))
            try:
                con.execute(
                    """
                    CREATE TABLE report_artifacts (
                        report_id VARCHAR PRIMARY KEY,
                        report_type VARCHAR NOT NULL,
                        title VARCHAR NOT NULL,
                        path VARCHAR NOT NULL,
                        source_run_id VARCHAR,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
            finally:
                con.close()

            initialize_database(db_path)
            con = duckdb.connect(str(db_path))
            try:
                columns = {
                    row[0]
                    for row in con.execute(
                        "DESCRIBE report_artifacts"
                    ).fetchall()
                }
            finally:
                con.close()
            self.assertIn("metadata_json", columns)

    def test_compare_requires_existing_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "evidence.duckdb"
            initialize_database(db_path)
            service = EvidenceService(db_path, root / "artifacts")

            with self.assertRaisesRegex(ValueError, "Run not found"):
                service.compare_runs(["run_missing_a", "run_missing_b"])

    def test_timeline_rejects_unknown_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "evidence.duckdb"
            initialize_database(db_path)
            service = EvidenceService(db_path, root / "artifacts")

            with self.assertRaisesRegex(ValueError, "Run not found"):
                service.run_timeline("run_missing")


if __name__ == "__main__":
    unittest.main()
