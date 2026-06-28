from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ..config import load_config
from ..infrastructure import DuckDBDatasetRepository
from ..services.catalog_service import CatalogService


def list_datasets(db_path: Path | None = None) -> dict:
    config = load_config()
    repository = DuckDBDatasetRepository(db_path or config.database_path)
    service = CatalogService(repository)
    datasets = service.list_datasets()
    return {"datasets": [asdict(dataset) for dataset in datasets]}


def get_dataset_detail(dataset_id: str, db_path: Path | None = None) -> dict:
    config = load_config()
    repository = DuckDBDatasetRepository(db_path or config.database_path)
    service = CatalogService(repository)
    dataset = service.get_dataset(dataset_id)
    if dataset is None:
        return {"found": False, "dataset": None}
    return {"found": True, "dataset": asdict(dataset)}
