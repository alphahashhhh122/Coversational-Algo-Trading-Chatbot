from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..infrastructure import DuckDBDatasetRepository, initialize_database
from .catalog_service import CatalogService
from .health_service import foundation_health


def verify_clean_foundation() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / "data" / "verification.duckdb"
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True)

        initialize_database(db_path)
        initialize_database(db_path)

        health = foundation_health(
            AppConfig(
                database_path=db_path,
                artifacts_dir=artifacts_dir,
                openalgo_root=root,
            )
        )
        datasets = CatalogService(DuckDBDatasetRepository(db_path)).list_datasets()
        checks = {
            "database_initialized": health["checks"]["database_exists"],
            "database_accessible": health["checks"]["database_accessible"],
            "core_schema_complete": health["checks"]["core_schema_complete"],
            "repeat_initialization_succeeded": True,
            "new_catalog_is_empty": datasets == [],
            "live_trading_disabled": health["checks"]["live_trading_disabled"],
        }
        return {
            "status": "healthy" if all(checks.values()) else "unhealthy",
            "checks": checks,
            "missing_tables": health["missing_tables"],
        }

