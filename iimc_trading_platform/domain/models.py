from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import (
    DataDomain,
    DataQualityStatus,
    ExecutionMode,
    OrderStatus,
    RiskOutcome,
    RunStatus,
    SignalDirection,
    ToolCallStatus,
)


@dataclass(frozen=True)
class SourceFile:
    source_id: str
    source_path: str
    source_name: str
    sha256: str
    byte_size: int
    ingested_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetQuality:
    status: DataQualityStatus
    total_rows: int
    valid_rows: int
    duplicate_rows: int = 0
    invalid_rows: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    data_domain: DataDomain
    data_type: str
    symbol: str
    exchange: str
    interval: str | None
    start_ts: datetime | None
    end_ts: datetime | None
    row_count: int
    storage_table: str
    source_id: str
    quality: DatasetQuality


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    version: str
    description: str
    parameter_schema: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class StrategyRun:
    run_id: str
    strategy_id: str
    dataset_id: str
    status: RunStatus
    execution_mode: ExecutionMode
    parameters: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class Signal:
    signal_id: str
    run_id: str
    timestamp: datetime
    symbol: str
    signal_type: str
    direction: SignalDirection | str
    confidence: float | None
    reason: str
    features: dict[str, Any]


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    run_id: str
    signal_id: str
    approved: bool
    requested_quantity: int
    approved_quantity: int
    reason: str
    checks: dict[str, Any]
    created_at: datetime
    risk_policy_version: str
    outcome: RiskOutcome | None = None


@dataclass(frozen=True)
class OrderEvent:
    order_id: str
    run_id: str
    decision_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    status: OrderStatus
    payload: dict[str, Any]
    created_at: datetime
    broker_order_id: str | None = None
    updated_at: datetime | None = None
    execution_mode: ExecutionMode = ExecutionMode.RESEARCH
    price: float | None = None
    idempotency_key: str | None = None
    filled_quantity: int = 0
    average_fill_price: float | None = None
    rejection_reason: str | None = None


@dataclass(frozen=True)
class TradeFill:
    trade_id: str
    order_id: str
    run_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    fees: float
    realized_pnl: float
    filled_at: datetime


@dataclass(frozen=True)
class PerformanceSummary:
    run_id: str
    total_trades: int
    net_pnl: float
    max_drawdown: float
    return_pct: float
    metrics: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    tool_call_id: str
    session_id: str | None
    tool_name: str
    request_json: str
    response_json: str | None
    status: ToolCallStatus
    created_at: datetime
    finished_at: datetime | None = None
    error_message: str | None = None
    trace_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True)
class ReportArtifact:
    report_id: str
    report_type: str
    title: str
    path: str
    source_run_id: str | None
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenAlgoSnapshot:
    snapshot_id: str
    snapshot_type: str
    source_table: str
    payload: dict[str, Any]
    captured_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    audit_id: str
    entity_type: str
    entity_id: str
    action: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime
