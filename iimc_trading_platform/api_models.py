from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .domain import ExecutionMode


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class McpCallRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentUploadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = ""
    document_type: str = Field(default="text", max_length=40)
    source_uri: str | None = Field(default=None, max_length=500)
    content_base64: str | None = None


class ToolEvidenceResponse(BaseModel):
    tool_call_id: str
    tool_name: str
    status: str


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    answer: str
    tool_calls: list[ToolEvidenceResponse]
    data: dict[str, Any]
    orchestration_mode: str
    evaluation: dict[str, Any]


class AiEvaluationRequest(BaseModel):
    mode: Literal["offline", "configured"] = "offline"


class RetentionPreviewRequest(BaseModel):
    policy_names: list[str] | None = Field(
        default=None,
        max_length=20,
    )


class RetentionExecuteRequest(RetentionPreviewRequest):
    confirmation: str = Field(min_length=1, max_length=100)


class AlertAcknowledgementRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=1000)


class SandboxActionRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=200)


class DashboardPreferencesRequest(BaseModel):
    widgets: list[str] = Field(max_length=12)
    auto_refresh: bool = False


class ResearchBriefRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=80)
    exchange: str = Field(min_length=1, max_length=20)
    asset_class: Literal[
        "equity",
        "index",
        "futures",
        "options",
        "commodity",
        "crypto",
    ]
    interval: str = Field(min_length=1, max_length=20)
    start_date: str = Field(min_length=4, max_length=20)
    end_date: str = Field(min_length=4, max_length=20)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RunComparisonRequest(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=10)


class RobustnessExperimentRequest(BaseModel):
    strategy_name: str = Field(min_length=1, max_length=100)
    dataset_id: str = Field(min_length=1, max_length=200)
    parameter_grid: list[dict[str, int | float]] = Field(
        min_length=1,
        max_length=12,
    )
    split_ratio: float = Field(default=0.7, ge=0.5, le=0.85)
    requested_quantity: int = Field(default=1, ge=1, le=100_000)
    starting_equity: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=1.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1_000)
    persist_selected_runs: bool = True


class CustomStrategyBacktestRequest(BaseModel):
    dataset_id: str = Field(min_length=1)
    execution_mode: ExecutionMode = ExecutionMode.RESEARCH
    requested_quantity: int = Field(default=1, ge=1, le=100_000)
    starting_equity: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=1.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1_000)
    instrument: dict[str, str | float] | None = None


class LocalOhlcvCandleInput(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(default=0.0, ge=0)


class LocalOhlcvDatasetInput(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=160)
    asset_class: Literal[
        "equity",
        "index",
        "futures",
        "options",
        "commodity",
        "crypto",
    ]
    symbol: str = Field(min_length=1, max_length=80)
    exchange: str = Field(min_length=1, max_length=40)
    interval: str = Field(min_length=1, max_length=20)
    candles: list[LocalOhlcvCandleInput] = Field(min_length=2, max_length=100_000)
    source_name: str = Field(default="local_ohlcv.json", min_length=1, max_length=200)


class OpenAlgoHistoryImportRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=80)
    exchange: str = Field(min_length=1, max_length=40)
    asset_class: Literal[
        "equity",
        "index",
        "futures",
        "options",
        "commodity",
        "crypto",
    ]
    interval: str = Field(min_length=1, max_length=20)
    start_date: str = Field(min_length=4, max_length=20)
    end_date: str = Field(min_length=4, max_length=20)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=160)


class LocalFeatureObservationInput(BaseModel):
    feature_name: str = Field(min_length=1, max_length=80)
    observed_at: datetime
    available_at: datetime
    value: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalFeatureDatasetInput(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=160)
    symbol: str = Field(min_length=1, max_length=80)
    exchange: str = Field(min_length=1, max_length=40)
    observations: list[LocalFeatureObservationInput] = Field(
        min_length=1,
        max_length=250_000,
    )
    source_name: str = Field(
        default="local_features.json",
        min_length=1,
        max_length=200,
    )


class OptionsFeatureDerivationInput(BaseModel):
    feature_dataset_id: str = Field(min_length=1, max_length=160)
    feature_names: list[str] = Field(min_length=1, max_length=7)
    availability_delay_seconds: int = Field(default=0, ge=0, le=86_400)


class CreatePortfolioRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    starting_cash: float = Field(gt=0)
    base_currency: str = Field(default="INR", min_length=3, max_length=3)


class PortfolioRiskCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=100)
    side: str = Field(pattern="^(BUY|SELL)$")
    quantity: int = Field(ge=1, le=10_000_000)
    price: float = Field(gt=0)


class PortfolioFillRequest(BaseModel):
    reservation_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1, max_length=200)
    price: float = Field(gt=0)
    fees: float = Field(default=0.0, ge=0)


class PortfolioControlRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)
