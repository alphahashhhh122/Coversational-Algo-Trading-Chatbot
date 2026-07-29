"""Platform status, readiness, dashboards, and instrument lookup.

Lifted out of ``create_app``. The handler bodies are unchanged; what was
an implicit closure over the application's service objects is now a
signature that names them.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Response
from ..api_models import (
    DashboardPreferencesRequest,
    ResearchBriefRequest,
)
from ..services import Principal
from ..tools.registry import (
    InstrumentSearchInput,
    MarketQuoteInput,
    OptionSymbolInput,
    RunBacktestInput,
    SymbolValidationInput,
)
from pydantic import ValidationError
from typing import Any


def register(
    app: FastAPI,
    *,
    capability_coverage_service: Any,
    dashboard_preference_service: Any,
    execute_tool: Any,
    execution_readiness_service: Any,
    instrument_discovery_service: Any,
    openalgo_readiness_service: Any,
    platform_dashboard_service: Any,
    research_service: Any,
    researcher: Any,
    viewer: Any,
) -> None:
    @app.get("/platform/summary")
    def platform_summary(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.summary()
    @app.get("/platform/dashboard")
    def platform_dashboard(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.summary()
    @app.get("/platform/dashboard/summary")
    def platform_dashboard_summary(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.summary()
    @app.get("/platform/dashboard/preferences")
    def dashboard_preferences(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return dashboard_preference_service.get(principal.user_id)
    @app.put("/platform/dashboard/preferences")
    def update_dashboard_preferences(
        request: DashboardPreferencesRequest,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return dashboard_preference_service.update(
                principal.user_id,
                widgets=request.widgets,
                auto_refresh=request.auto_refresh,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.get("/platform/operator-review")
    def platform_operator_review(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return platform_dashboard_service.operator_review()
    @app.get("/platform/status")
    def platform_status(
        symbol: str = "NIFTY",
        exchange: str = "NFO",
        asset_class: str = "options",
        interval: str = "5m",
        start_date: str = "2026-04-23",
        end_date: str = "2026-05-23",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return capability_coverage_service.platform_status(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
    @app.get("/platform/execution/readiness")
    def platform_execution_readiness(
        symbol: str = "NIFTY",
        exchange: str = "NFO",
        asset_class: str = "options",
        interval: str = "5m",
        start_date: str = "2026-04-23",
        end_date: str = "2026-05-23",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execution_readiness_service.readiness(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
    @app.get("/platform/symbol/readiness")
    def platform_symbol_readiness(
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return capability_coverage_service.platform_status(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
    @app.get("/platform/research/context")
    def platform_research_context(
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return research_service.research_context(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
    @app.get("/platform/research/briefs")
    def platform_research_briefs(
        limit: int = 20,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 100",
            )
        return research_service.list_briefs(limit=limit)
    @app.post("/platform/research/briefs")
    def create_platform_research_brief(
        request: ResearchBriefRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return research_service.create_brief(
            **request.model_dump(),
            created_by=principal.username,
        )
    @app.get("/platform/openalgo/monitor")
    def platform_openalgo_monitor(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return openalgo_readiness_service.monitor()
    @app.get("/platform/instruments/search")
    def platform_instrument_search(
        query: str,
        exchange: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = InstrumentSearchInput(
                query=query,
                exchange=exchange,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return instrument_discovery_service.search(**payload.model_dump())
    @app.get("/platform/instruments/symbol")
    def platform_instrument_symbol(
        symbol: str,
        exchange: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = SymbolValidationInput(
                symbol=symbol,
                exchange=exchange,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return instrument_discovery_service.validate_symbol(
            **payload.model_dump()
        )
    @app.get("/platform/instruments/quote")
    def platform_instrument_quote(
        query: str,
        exchange: str = "NSE",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = MarketQuoteInput(
                query=query,
                exchange=exchange,
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return execute_tool("get_market_quote", payload)
    @app.get("/platform/instruments/optionsymbol")
    def platform_option_symbol(
        underlying: str,
        exchange: str,
        expiry_date: str,
        option_type: str,
        offset: str = "ATM",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = OptionSymbolInput(
                underlying=underlying,
                exchange=exchange,
                expiry_date=expiry_date,
                offset=offset,
                option_type=option_type,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return instrument_discovery_service.resolve_option_symbol(
            **payload.model_dump()
        )
    @app.post("/platform/backtest/run", response_model=None)
    def platform_backtest_run(
        request: RunBacktestInput,
        response: Response,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            result = execute_tool(
                "run_backtest",
                request.model_dump(mode="json"),
            )
        except HTTPException as exc:
            response.status_code = int(exc.status_code)
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "ok": False,
                "status": "backtest_failed",
                "safe_failure": True,
                "message": (
                    detail.get("message")
                    if isinstance(detail, dict)
                    else str(exc.detail)
                ),
                "no_synthetic_fallback": True,
                "data_source": "real",
                "visible_in_openalgo": False,
                "detail": exc.detail,
            }
        return {
            "ok": True,
            "status": "completed",
            "safe_failure": False,
            "message": "IIMC historical backtest completed.",
            "no_synthetic_fallback": True,
            "data_source": "real",
            "visible_in_openalgo": False,
            **result,
        }
