from __future__ import annotations

from typing import Any

from ..config import AppConfig
from .capability_coverage_service import CapabilityCoverageService
from .openalgo_readiness_service import OpenAlgoReadinessService


class ExecutionReadinessService:
    def __init__(
        self,
        config: AppConfig,
        capabilities: CapabilityCoverageService,
        openalgo: OpenAlgoReadinessService,
    ) -> None:
        self.config = config
        self.capabilities = capabilities
        self.openalgo = openalgo

    def readiness(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        data = self.capabilities.platform_status(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
        openalgo = self.openalgo.monitor()
        local_data_ready = bool(data.get("local_dataset_exists"))
        architecture_ready = bool(data.get("supported_by_architecture"))
        provider_ready = bool(openalgo.get("ok"))
        analyzer_ready = bool(openalgo.get("analyzer_mode"))

        stages = [
            _stage(
                "research",
                "ready" if architecture_ready else "blocked",
                architecture_ready,
                [],
                (
                    "Use research context and provider-backed news checks."
                    if architecture_ready
                    else "Use a supported asset class before research."
                ),
            ),
            _stage(
                "backtest",
                "ready" if local_data_ready else "blocked",
                local_data_ready,
                [] if local_data_ready else ["local_dataset_missing"],
                (
                    "Run deterministic strategy backtests on the governed dataset."
                    if local_data_ready
                    else "Ingest or connect real historical data before backtesting."
                ),
            ),
            _stage(
                "paper_trading",
                "ready" if local_data_ready and provider_ready and analyzer_ready else "blocked",
                local_data_ready and provider_ready and analyzer_ready,
                _blockers(
                    ("local_dataset_missing", not local_data_ready),
                    ("openalgo_not_ready", not provider_ready),
                    ("openalgo_analyzer_mode_required", provider_ready and not analyzer_ready),
                ),
                (
                    "Prepare and submit OpenAlgo analyzer orders from risk-approved decisions."
                    if local_data_ready and provider_ready and analyzer_ready
                    else "Configure OpenAlgo analyzer mode and complete a risk-approved run first."
                ),
                requires_human_approval=self.config.require_paper_approval,
                external_side_effects=True,
            ),
            _stage(
                "live_trading",
                "ready" if self.config.allow_live_trading and provider_ready else "disabled",
                bool(self.config.allow_live_trading and provider_ready),
                _blockers(
                    ("live_trading_disabled", not self.config.allow_live_trading),
                    ("openalgo_not_ready", not provider_ready),
                ),
                (
                    "Live submission remains human-approved and broker-gated."
                    if self.config.allow_live_trading and provider_ready
                    else "Keep live trading disabled until production controls are explicitly enabled."
                ),
                requires_human_approval=True,
                external_side_effects=True,
            ),
        ]
        return {
            "symbol": data["symbol"],
            "exchange": data["exchange"],
            "asset_class": data["asset_class"],
            "interval": data["interval"],
            "data_readiness": data,
            "openalgo": {
                "configured": openalgo.get("configured"),
                "status": openalgo.get("status"),
                "analyzer_mode": openalgo.get("analyzer_mode", False),
                "live_trading_enabled": openalgo.get("live_trading_enabled", False),
                "safe_failure": openalgo.get("safe_failure", False),
            },
            "stages": stages,
            "next_blocker": _first_blocker(stages),
            "no_synthetic_fallback": True,
            "research_not_advice": True,
        }


def _stage(
    name: str,
    status: str,
    can_start: bool,
    blockers: list[str],
    next_action: str,
    *,
    requires_human_approval: bool = False,
    external_side_effects: bool = False,
) -> dict[str, Any]:
    return {
        "stage": name,
        "status": status,
        "can_start": can_start,
        "requires_human_approval": requires_human_approval,
        "external_side_effects": external_side_effects,
        "blockers": blockers,
        "next_action": next_action,
    }


def _blockers(*pairs: tuple[str, bool]) -> list[str]:
    return [name for name, active in pairs if active]


def _first_blocker(stages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stage in stages:
        if stage["blockers"]:
            return {
                "stage": stage["stage"],
                "blockers": stage["blockers"],
                "next_action": stage["next_action"],
            }
    return None
