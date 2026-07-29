"""Datasets, freshness, history import, and coverage.

One slice of the tool catalogue. ``build`` takes only the services its
own tools use, so each group's dependencies are visible instead of being
shared implicitly through one factory's scope.
"""

from __future__ import annotations

from typing import Any

from ..catalog_tools import get_dataset_detail, list_datasets
from ..contracts import ToolCapabilityMetadata, ToolDefinition
from ..inputs import (
    DatasetDetailInput,
    DatasetFreshnessInput,
    EmptyInput,
    OpenAlgoHistoryImportInput,
)


def build(
    *,
    _data_health: Any,
    db_path: Any,
    freshness: Any,
    openalgo_history_import: Any,
) -> list[ToolDefinition]:
    return [
                ToolDefinition(
                    name="list_datasets",
                    description="List governed datasets and their quality metadata.",
                    input_model=EmptyInput,
                    handler=lambda value: list_datasets(db_path),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_dataset_detail",
                    description="Retrieve one dataset by its exact dataset ID.",
                    input_model=DatasetDetailInput,
                    handler=lambda value: get_dataset_detail(
                        DatasetDetailInput.model_validate(
                            value.model_dump()
                        ).dataset_id,
                        db_path,
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="assess_dataset_freshness",
                    description=(
                        "Assess whether a governed dataset is fit for a specific "
                        "purpose such as historical research or current-market use."
                    ),
                    input_model=DatasetFreshnessInput,
                    handler=lambda value: freshness.assess(
                        DatasetFreshnessInput.model_validate(
                            value.model_dump()
                        ).dataset_id,
                        DatasetFreshnessInput.model_validate(
                            value.model_dump()
                        ).purpose,
                    ),
                    side_effects="creates a persisted freshness assessment",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_data_health",
                    description=(
                        "Report what market data the platform actually holds per "
                        "symbol across a universe: price history, fundamentals, "
                        "freshness, and which agents can therefore work on each "
                        "symbol. Read-only; names gaps plainly instead of letting "
                        "an agent discover them by failing."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: _data_health(db_path).coverage(),
                    side_effects="read-only: reads the data catalog",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("retrieve",),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="import_openalgo_history",
                    description=(
                        "Fetch verified historical candles from OpenAlgo and store "
                        "them as a governed local dataset for research and "
                        "backtesting. This never submits an order."
                    ),
                    input_model=OpenAlgoHistoryImportInput,
                    handler=lambda value: openalgo_history_import.import_history(
                        **OpenAlgoHistoryImportInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="imports provider history into the local governed catalog",
                    retry_safe=True,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("import_data", "backtest"),
                        asset_classes=(
                            "equity", "index", "futures", "options",
                            "commodity", "crypto",
                        ),
                        execution_modes=("research", "paper"),
                        required_providers=("openalgo",),
                        risk_level="low",
                    ),
                ),
    ]
