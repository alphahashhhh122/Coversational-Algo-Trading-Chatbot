from __future__ import annotations

from ..domain import Dataset
from ..repositories import DatasetRepository



class CatalogService:
    def __init__(self, dataset_repository: DatasetRepository):
        self.dataset_repository = dataset_repository

    def list_datasets(self) -> list[Dataset]:
        return self.dataset_repository.list()

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        return self.dataset_repository.get(dataset_id)
