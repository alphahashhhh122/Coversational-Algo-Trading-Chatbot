from __future__ import annotations

import tempfile
import unittest
import io
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from iimc_trading_platform.config import AppConfig, load_config, public_config
from iimc_trading_platform.db import connect
from iimc_trading_platform.domain import (
    DataDomain,
    DataQualityStatus,
    Dataset,
    DatasetQuality,
    ExecutionMode,
    OrderStatus,
    RunStatus,
    ToolCallStatus,
)
from iimc_trading_platform.infrastructure import CORE_TABLES, initialize_database, list_tables
from iimc_trading_platform.infrastructure import DuckDBToolCallRepository
from iimc_trading_platform.observability import configure_logging
from iimc_trading_platform.services import CatalogService
from iimc_trading_platform.services import foundation_health, verify_clean_foundation


class FakeDatasetRepository:
    def __init__(self, datasets: list[Dataset]):
        self.datasets = datasets

    def list(self) -> list[Dataset]:
        return self.datasets

    def get(self, dataset_id: str) -> Dataset | None:
        for dataset in self.datasets:
            if dataset.dataset_id == dataset_id:
                return dataset
        return None


class FoundationTest(unittest.TestCase):
    def test_initialize_database_and_list_empty_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.duckdb"
            initialize_database(db_path)

            from iimc_trading_platform.infrastructure import DuckDBDatasetRepository

            service = CatalogService(DuckDBDatasetRepository(db_path))
            self.assertEqual(service.list_datasets(), [])

    def test_catalog_service_uses_repository_contract(self) -> None:
        dataset = Dataset(
            dataset_id="demo_dataset",
            data_domain=DataDomain.MARKET_DATA,
            data_type="ohlcv",
            symbol="NIFTY",
            exchange="NFO",
            interval="5m",
            start_ts=datetime(2026, 1, 1, 9, 15),
            end_ts=datetime(2026, 1, 1, 15, 30),
            row_count=10,
            storage_table="options_ohlcv",
            source_id="source_1",
            quality=DatasetQuality(
                status=DataQualityStatus.CLEAN,
                total_rows=10,
                valid_rows=10,
            ),
        )
        service = CatalogService(FakeDatasetRepository([dataset]))

        self.assertEqual(service.list_datasets(), [dataset])
        self.assertEqual(service.get_dataset("demo_dataset"), dataset)
        self.assertIsNone(service.get_dataset("missing"))
        self.assertEqual(asdict(service.list_datasets()[0])["symbol"], "NIFTY")

    def test_core_tables_are_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.duckdb"
            initialize_database(db_path)

            tables = set(list_tables(db_path))
            for table_name in CORE_TABLES:
                self.assertIn(table_name, tables)

            con = connect(db_path)
            try:
                strategy_columns = {
                    row[0]
                    for row in con.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'strategy_runs'
                        """
                    ).fetchall()
                }
                signal_columns = {
                    row[0]
                    for row in con.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'strategy_signals'
                        """
                    ).fetchall()
                }
            finally:
                con.close()

            self.assertIn("strategy_id", strategy_columns)
            self.assertIn("execution_mode", strategy_columns)
            self.assertIn("direction", signal_columns)
            self.assertIn("features_json", signal_columns)

    def test_database_initialization_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.duckdb"
            initialize_database(db_path)
            initialize_database(db_path)

            con = connect(db_path)
            try:
                version_count = con.execute(
                    """
                    SELECT COUNT(*)
                    FROM schema_versions
                    WHERE version_id = 'week1_foundation_v1'
                    """
                ).fetchone()[0]
            finally:
                con.close()
            self.assertEqual(version_count, 1)

    def test_domain_status_values_are_stable(self) -> None:
        self.assertEqual(DataDomain.FUNDAMENTAL_DATA, "fundamental_data")
        self.assertEqual(DataDomain.ALTERNATIVE_DATA, "alternative_data")
        self.assertEqual(DataDomain.AUDIT_DATA, "audit_data")
        self.assertEqual(DataQualityStatus.CLEAN_WITH_WARNINGS, "clean_with_warnings")
        self.assertEqual(RunStatus.RUNNING, "running")
        self.assertEqual(ExecutionMode.SANDBOX, "sandbox")
        self.assertEqual(OrderStatus.PENDING_APPROVAL, "pending_approval")
        self.assertEqual(ToolCallStatus.SUCCEEDED, "succeeded")
        self.assertEqual(ToolCallStatus.CANCELLED, "cancelled")

    def test_schema_migrates_legacy_market_data_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.duckdb"
            initialize_database(db_path)
            con = connect(db_path)
            try:
                con.execute(
                    """
                    INSERT INTO data_catalog VALUES (
                        'legacy', 'MD', 'ohlcv', 'NIFTY', 'NSE', '5m',
                        NULL, NULL, 0, 'options_ohlcv', 'source',
                        'empty', NULL, CURRENT_TIMESTAMP
                    )
                    """
                )
            finally:
                con.close()

            initialize_database(db_path)
            con = connect(db_path)
            try:
                stored_domain = con.execute(
                    "SELECT data_domain FROM data_catalog WHERE dataset_id = 'legacy'"
                ).fetchone()[0]
            finally:
                con.close()
            self.assertEqual(stored_domain, DataDomain.MARKET_DATA.value)

    def test_public_config_never_returns_secret_values(self) -> None:
        config = AppConfig(
            openai_api_key="secret-openai",
            openalgo_api_key="secret-openalgo",
        )
        visible = public_config(config)

        self.assertNotIn("secret-openai", visible.values())
        self.assertNotIn("secret-openalgo", visible.values())
        self.assertTrue(visible["openai_api_key_configured"])
        self.assertTrue(visible["openalgo_api_key_configured"])

    def test_load_config_reads_local_dotenv_without_overriding_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=dotenv-openai",
                        "OPENALGO_API_KEY=dotenv-openalgo",
                        "MARKET_NEWS_PROVIDER=dotenv-news",
                        "IIMC_ALLOW_LIVE_TRADING=true",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "IIMC_ALLOW_LIVE_TRADING": "false",
                    "USERPROFILE": str(root),
                },
                clear=True,
            ):
                with patch("pathlib.Path.cwd", return_value=root):
                    config = load_config()

        self.assertEqual(config.openai_api_key, "dotenv-openai")
        self.assertEqual(config.openalgo_api_key, "dotenv-openalgo")
        self.assertEqual(config.market_news_provider, "dotenv-news")
        self.assertFalse(config.allow_live_trading)

    def test_foundation_health_requires_schema_and_safe_live_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "test.duckdb"
            artifacts = root / "artifacts"
            artifacts.mkdir()
            initialize_database(db_path)

            status = foundation_health(
                AppConfig(
                    database_path=db_path,
                    artifacts_dir=artifacts,
                    openalgo_root=root / "missing-openalgo",
                )
            )

            self.assertEqual(status["status"], "healthy")
            self.assertTrue(status["checks"]["core_schema_complete"])
            self.assertTrue(status["checks"]["live_trading_disabled"])

    def test_foundation_health_accepts_intentional_live_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "test.duckdb"
            artifacts = root / "artifacts"
            artifacts.mkdir()
            initialize_database(db_path)

            status = foundation_health(
                AppConfig(
                    database_path=db_path,
                    artifacts_dir=artifacts,
                    openalgo_root=root / "missing-openalgo",
                    allow_live_trading=True,
                )
            )

            self.assertEqual(status["status"], "healthy")
            self.assertFalse(status["checks"]["live_trading_disabled"])
            self.assertTrue(
                any("Live trading is enabled" in note for note in status["notes"])
            )

    def test_foundation_health_does_not_create_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "missing.duckdb"
            status = foundation_health(AppConfig(database_path=db_path))

            self.assertEqual(status["status"], "unhealthy")
            self.assertFalse(status["checks"]["database_exists"])
            self.assertFalse(db_path.exists())

    def test_foundation_health_reports_inaccessible_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.duckdb"
            initialize_database(db_path)

            with patch(
                "iimc_trading_platform.services.health_service.list_tables",
                side_effect=RuntimeError("database busy"),
            ):
                status = foundation_health(AppConfig(database_path=db_path))

            self.assertEqual(status["status"], "unhealthy")
            self.assertTrue(status["checks"]["database_exists"])
            self.assertFalse(status["checks"]["database_accessible"])
            self.assertFalse(status["checks"]["core_schema_complete"])
            self.assertEqual(
                status["database_error"],
                "Database inspection failed: RuntimeError",
            )

    def test_initialize_database_migrates_tool_call_error_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.duckdb"
            con = connect(db_path)
            try:
                con.execute(
                    """
                    CREATE TABLE tool_calls (
                        tool_call_id VARCHAR PRIMARY KEY,
                        session_id VARCHAR,
                        tool_name VARCHAR NOT NULL,
                        request_json VARCHAR NOT NULL,
                        response_json VARCHAR,
                        status VARCHAR NOT NULL,
                        created_at TIMESTAMP NOT NULL,
                        finished_at TIMESTAMP
                    )
                    """
                )
            finally:
                con.close()

            initialize_database(db_path)
            con = connect(db_path)
            try:
                columns = {
                    row[0]
                    for row in con.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'tool_calls'
                        """
                    ).fetchall()
                }
            finally:
                con.close()

            self.assertIn("error_message", columns)

            from iimc_trading_platform.domain import ToolCall

            DuckDBToolCallRepository(db_path).add(
                ToolCall(
                    tool_call_id="tool_migrated",
                    session_id="session_1",
                    tool_name="migration_check",
                    request_json="{}",
                    response_json=None,
                    status=ToolCallStatus.RUNNING,
                    created_at=datetime(2026, 1, 1, 9, 15),
                )
            )
            stored = DuckDBToolCallRepository(db_path).get("tool_migrated")
            self.assertIsNotNone(stored)
            self.assertEqual(stored.status, ToolCallStatus.RUNNING)

    def test_clean_foundation_verification_uses_isolated_database(self) -> None:
        result = verify_clean_foundation()

        self.assertEqual(result["status"], "healthy")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["missing_tables"], [])

    def test_structured_logging_outputs_json(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", stream)

        logging.getLogger("foundation-test").info(
            "verification completed",
            extra={"event": "foundation_verified", "result": "healthy"},
        )

        record = json.loads(stream.getvalue())
        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["message"], "verification completed")
        self.assertEqual(record["event"], "foundation_verified")
        self.assertEqual(record["result"], "healthy")


if __name__ == "__main__":
    unittest.main()
