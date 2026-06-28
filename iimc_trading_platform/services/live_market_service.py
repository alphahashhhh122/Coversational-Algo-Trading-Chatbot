from __future__ import annotations

from typing import Any

from .openalgo_readiness_service import OpenAlgoReadinessService


class LiveMarketService:
    def __init__(self, openalgo: OpenAlgoReadinessService) -> None:
        self.openalgo = openalgo

    def quote_readiness(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        return self.openalgo.readiness(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
