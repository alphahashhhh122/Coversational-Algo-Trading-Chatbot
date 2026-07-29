"""Datasets, coverage, fundamentals, market news, strategy listings.

Lifted out of ``create_app``. The handler bodies are unchanged; what was
an implicit closure over the application's service objects is now a
signature that names them.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Response
from ..api_models import (
    BackfillRequest,
    FundamentalStatementsImportRequest,
    LocalFeatureDatasetInput,
    LocalOhlcvDatasetInput,
    OpenAlgoHistoryImportRequest,
    OptionsFeatureDerivationInput,
)
from ..infrastructure import DuckDBAuditRepository
from ..services import (
    AuditService,
    Principal,
)
from ..services.backtest_service import BacktestService
from ..tools.registry import (
    DatasetDetailInput,
    DatasetFreshnessInput,
    RunBacktestInput,
)
from pydantic import ValidationError
from typing import Any


def register(
    app: FastAPI,
    *,
    active_config: Any,
    data_health_service: Any,
    execute_tool: Any,
    market_data_ingestion_service: Any,
    market_news_service: Any,
    openalgo_history_import_service: Any,
    researcher: Any,
    universe_backfill: Any,
    viewer: Any,
) -> None:
    @app.get("/data/health")
    def data_health_endpoint(
        universe: str = "nifty50",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return data_health_service.coverage(universe)
    @app.get("/data/backfill/status")
    def backfill_status_endpoint(
        universe: str = "nifty50",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return universe_backfill.status(universe)
    @app.post("/data/backfill/run")
    def backfill_run_endpoint(
        request: BackfillRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return universe_backfill.run(
                universe=request.universe,
                interval=request.interval,
                exchange=request.exchange,
                lookback_days=request.lookback_days,
                max_symbols=request.max_symbols,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.get("/datasets")
    def datasets(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return execute_tool("list_datasets", {})
    @app.get("/datasets/{dataset_id}/instruments")
    def dataset_instruments(
        dataset_id: str,
        limit: int = 500,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return BacktestService(
                active_config.database_path,
                strategy_plugin_dir=active_config.strategy_plugin_dir,
            ).list_dataset_instruments(dataset_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.get("/datasets/{dataset_id}/ohlcv")
    def dataset_ohlcv(
        dataset_id: str,
        limit: int = 500,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 10 or limit > 2000:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 10 and 2000",
            )
        try:
            metadata, candles = BacktestService(
                active_config.database_path,
                strategy_plugin_dir=active_config.strategy_plugin_dir,
            ).load_dataset_candles(dataset_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        tail = candles[-limit:]
        return {
            "dataset_id": dataset_id,
            "symbol": metadata["symbol"],
            "exchange": metadata["exchange"],
            "interval": metadata["interval"],
            "asset_class": metadata["asset_class"],
            "total_candles": len(candles),
            "returned_candles": len(tail),
            "candles": [
                {
                    "timestamp": (
                        candle["timestamp"].isoformat()
                        if hasattr(candle["timestamp"], "isoformat")
                        else str(candle["timestamp"])
                    ),
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle["volume"],
                }
                for candle in tail
            ],
        }
    @app.post("/datasets/ohlcv")
    def import_local_ohlcv_dataset(
        request: LocalOhlcvDatasetInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            result = market_data_ingestion_service.import_ohlcv(
                **request.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        ).record(
            entity_type="dataset",
            entity_id=result["dataset_id"],
            action="local_ohlcv_imported",
            actor=principal.username,
            payload={
                "asset_class": result["asset_class"],
                "row_count": result["row_count"],
                "source_sha256": result["source_sha256"],
            },
        )
        return {**result, "audit_id": audit.audit_id}
    @app.post("/datasets/openalgo-history")
    def import_openalgo_history_dataset(
        request: OpenAlgoHistoryImportRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            result = openalgo_history_import_service.import_history(
                **request.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        ).record(
            entity_type="dataset",
            entity_id=result["dataset_id"],
            action="openalgo_history_imported",
            actor=principal.username,
            payload={
                "provider": "openalgo",
                "symbol": result["resolved_symbol"],
                "exchange": result["resolved_exchange"],
                "asset_class": result["asset_class"],
                "row_count": result["row_count"],
                "source_sha256": result["source_sha256"],
            },
        )
        return {**result, "audit_id": audit.audit_id}
    @app.post("/datasets/features")
    def import_local_feature_dataset(
        request: LocalFeatureDatasetInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            result = market_data_ingestion_service.import_features(
                **request.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        ).record(
            entity_type="dataset",
            entity_id=result["dataset_id"],
            action="local_feature_series_imported",
            actor=principal.username,
            payload={
                "feature_names": result["feature_names"],
                "row_count": result["row_count"],
                "source_sha256": result["source_sha256"],
                "point_in_time_safe": True,
            },
        )
        return {**result, "audit_id": audit.audit_id}
    @app.post("/datasets/options/{dataset_id}/derive-features")
    def derive_options_feature_dataset(
        dataset_id: str,
        request: OptionsFeatureDerivationInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            result = market_data_ingestion_service.derive_options_features(
                options_dataset_id=dataset_id,
                **request.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        ).record(
            entity_type="dataset",
            entity_id=result["dataset_id"],
            action="options_features_derived",
            actor=principal.username,
            payload={
                "source_dataset_id": dataset_id,
                "feature_names": result["feature_names"],
                "source_sha256": result["source_sha256"],
            },
        )
        return {**result, "audit_id": audit.audit_id}
    @app.get("/datasets/{dataset_id}")
    def dataset_detail(
        dataset_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = DatasetDetailInput(dataset_id=dataset_id).model_dump(
            mode="json"
        )
        return execute_tool("get_dataset_detail", payload)
    @app.get("/datasets/{dataset_id}/freshness")
    def dataset_freshness(
        dataset_id: str,
        purpose: str = "historical_research",
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            payload = DatasetFreshnessInput(
                dataset_id=dataset_id,
                purpose=purpose,
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        return execute_tool("assess_dataset_freshness", payload)
    @app.get("/strategies")
    def strategies(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        return execute_tool("list_strategies", {})
    @app.post("/fundamentals/statements")
    def import_fundamental_statements(
        request: FundamentalStatementsImportRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        from ..services.fundamentals_service import FundamentalsService

        try:
            result = FundamentalsService(
                active_config.database_path
            ).import_statements(
                symbol=request.symbol,
                currency=request.currency,
                source=request.source,
                statements=request.statements,
                imported_by=principal.username,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        )
        event = audit.record(
            actor=principal.username,
            action="fundamental_statements_imported",
            entity_type="financial_statements",
            entity_id=result["symbol"],
            payload=result,
        )
        return {**result, "audit_id": event.audit_id}
    @app.get("/fundamentals/{symbol}/analysis")
    def fundamental_analysis(
        symbol: str,
        market_price: float | None = None,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execute_tool(
            "analyze_fundamentals",
            {"symbol": symbol, "market_price": market_price},
        )
    @app.get("/market-news/status")
    def market_news_status(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return market_news_service.status()
    @app.get("/market-news/latest")
    def market_news_latest(
        limit: int = 20,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return market_news_service.latest(limit)
    @app.post("/market-news/fetch", response_model=None)
    def market_news_fetch(
        response: Response,
        query: str | None = None,
        symbol: str | None = None,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        result = market_news_service.fetch(query=query, symbol=symbol)
        if not result.get("ok"):
            response.status_code = 409
        return result
    @app.post("/backtests")
    def run_backtest(
        request: RunBacktestInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return execute_tool(
            "run_backtest",
            request.model_dump(mode="json"),
        )
