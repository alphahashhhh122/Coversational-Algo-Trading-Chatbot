from __future__ import annotations

from enum import StrEnum


class DataDomain(StrEnum):
    MARKET_DATA = "market_data"
    FUNDAMENTAL_DATA = "fundamental_data"
    ALTERNATIVE_DATA = "alternative_data"
    REFERENCE_DATA = "reference_data"
    STRATEGY_DATA = "strategy_data"
    SIGNAL_DATA = "signal_data"
    RISK_DATA = "risk_data"
    ORDER_DATA = "order_data"
    TRADE_DATA = "trade_data"
    POSITION_DATA = "position_data"
    FUNDS_DATA = "funds_data"
    PERFORMANCE_DATA = "performance_data"
    CONVERSATION_DATA = "conversation_data"
    AUDIT_DATA = "audit_data"
    REPORT_DATA = "report_data"
    SYSTEM_DATA = "system_data"


class DataQualityStatus(StrEnum):
    PENDING = "pending"
    CLEAN = "clean"
    CLEAN_WITH_WARNINGS = "clean_with_warnings"
    REJECTED = "rejected"
    EMPTY = "empty"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Distinct from CANCELLED, which means somebody chose to stop it. A run is
    # INTERRUPTED when the process died underneath it — nobody decided
    # anything, and the run will never produce a result.
    INTERRUPTED = "interrupted"


class ExecutionMode(StrEnum):
    RESEARCH = "research"
    SANDBOX = "sandbox"
    SEMI_AUTO = "semi_auto"
    LIVE = "live"


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    EXIT = "exit"
    FLAT = "flat"


class RiskOutcome(StrEnum):
    APPROVED = "approved"
    RESIZED = "resized"
    REJECTED = "rejected"


class OrderStatus(StrEnum):
    CREATED = "created"
    RISK_APPROVED = "risk_approved"
    RISK_REJECTED = "risk_rejected"
    SUBMITTED = "submitted"
    PENDING_APPROVAL = "pending_approval"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ToolCallStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
