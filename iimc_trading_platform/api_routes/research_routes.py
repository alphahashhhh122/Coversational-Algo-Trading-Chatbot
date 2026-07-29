"""The research workflow: runs, experiments, authored specs, screens, evidence.

Lifted out of ``create_app``. The handler bodies are unchanged; what was
an implicit closure over the application's service objects is now a
signature that names them.
"""

from __future__ import annotations

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from ..api_models import (
    AiEvaluationRequest,
    CustomStrategyBacktestRequest,
    RobustnessExperimentRequest,
    RunComparisonRequest,
    ScreenDefinitionRequest,
)
from ..db import connect
from ..orchestration import build_orchestrator
from ..services import (
    CustomStrategyService,
    Principal,
)
from ..services.backtest_service import BacktestService
from ..tools.registry import (
    CompileCustomStrategyInput,
    CreateCustomStrategySpecInput,
    ListCustomStrategySpecsInput,
    RunCustomStrategySpecInput,
    RunIdInput,
)
from fastapi.responses import PlainTextResponse
from typing import Any


def register(
    app: FastAPI,
    *,
    active_config: Any,
    ai_evaluation_service: Any,
    approver: Any,
    evidence_service: Any,
    execute_tool: Any,
    researcher: Any,
    retrieval_evaluation_service: Any,
    robustness_service: Any,
    task_service: Any,
    viewer: Any,
) -> None:
    @app.get("/evaluations")
    def evaluations(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return ai_evaluation_service.list(limit)
    @app.post("/evaluations/run")
    def run_evaluation(
        request: AiEvaluationRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        configured_api_key = (
            active_config.groq_api_key
            if active_config.llm_provider == "groq"
            else active_config.openai_api_key
        )
        configured_model = (
            active_config.groq_model
            if active_config.llm_provider == "groq"
            else active_config.openai_model
        )
        if request.mode == "configured" and not configured_api_key:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{active_config.llm_provider.upper()} API key is "
                    "required for configured evaluation"
                ),
            )
        orchestrator = build_orchestrator(
            api_key=configured_api_key if request.mode == "configured" else None,
            model=active_config.openai_model,
            provider=active_config.llm_provider,
            groq_api_key=(
                active_config.groq_api_key
                if request.mode == "configured"
                else None
            ),
            groq_model=active_config.groq_model,
            groq_fallback_model=active_config.groq_fallback_model,
            require_real_llm=(request.mode == "configured"),
        )
        return ai_evaluation_service.run(
            orchestrator=orchestrator,
            model=configured_model if request.mode == "configured" else None,
            created_by=principal.username,
        )
    @app.get("/evaluations/retrieval")
    def retrieval_evaluations(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return retrieval_evaluation_service.list(limit)
    @app.post("/evaluations/retrieval/run")
    def run_retrieval_evaluation(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return retrieval_evaluation_service.run(
            created_by=principal.username,
        )
    @app.get("/custom-strategy-specs")
    def custom_strategy_specs(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = ListCustomStrategySpecsInput(limit=limit).model_dump(
            mode="json"
        )
        return execute_tool("list_custom_strategy_specs", payload)
    @app.get("/custom-strategy-capabilities")
    def custom_strategy_capabilities(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execute_tool("get_custom_strategy_capabilities", {})
    @app.post("/custom-strategy-specs")
    def create_custom_strategy_spec(
        request: CreateCustomStrategySpecInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        payload["created_by"] = principal.username
        return execute_tool("create_custom_strategy_spec", payload)
    @app.post("/custom-strategy-specs/compile")
    def compile_custom_strategy_spec(
        request: CompileCustomStrategyInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return execute_tool(
            "compile_custom_strategy_spec",
            request.model_dump(mode="json"),
        )
    @app.put("/custom-strategy-specs/{spec_id}")
    def update_custom_strategy_spec(
        spec_id: str,
        request: CreateCustomStrategySpecInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        payload["spec_id"] = spec_id
        payload["created_by"] = principal.username
        return execute_tool("update_custom_strategy_spec", payload)
    @app.get("/custom-strategy-specs/{spec_id}")
    def get_custom_strategy_spec(
        spec_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return CustomStrategyService(
                active_config.database_path
            ).get_spec(spec_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    @app.delete("/custom-strategy-specs/{spec_id}")
    def delete_custom_strategy_spec(
        spec_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return CustomStrategyService(
                active_config.database_path
            ).delete_spec(spec_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    @app.post("/custom-strategy-specs/validate")
    def validate_custom_strategy_spec(
        request: CreateCustomStrategySpecInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        payload.pop("created_by", None)
        return CustomStrategyService(active_config.database_path).validate_spec(
            **payload
        )
    @app.post("/custom-strategy-specs/{spec_id}/backtest")
    def run_custom_strategy_spec(
        spec_id: str,
        request: CustomStrategyBacktestRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = RunCustomStrategySpecInput(
            spec_id=spec_id,
            **request.model_dump(mode="json"),
        ).model_dump(mode="json")
        return execute_tool("run_custom_strategy_spec", payload)
    @app.get("/runs/{run_id}/trades.csv")
    def export_run_trades_csv(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> PlainTextResponse:
        con = connect(active_config.database_path)
        try:
            rows = con.execute(
                """
                SELECT trade_id, symbol, side, quantity, price,
                       realized_pnl, fees, filled_at
                FROM trade_fills
                WHERE run_id = ?
                ORDER BY filled_at, trade_id
                """,
                [run_id],
            ).fetchall()
        finally:
            con.close()
        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"No trades stored for run {run_id!r}",
            )
        lines = ["trade_id,symbol,side,quantity,price,realized_pnl,fees,filled_at"]
        for row in rows:
            lines.append(
                ",".join(str(value) for value in row)
            )
        return PlainTextResponse(
            "\n".join(lines) + "\n",
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename={run_id}_trades.csv"
                ),
            },
        )
    @app.get("/screens")
    def list_screens(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        from ..services.screen_service import ScreenService

        service = ScreenService(active_config.database_path)
        service.ensure_defaults()
        return service.list_definitions()
    @app.post("/screens")
    def save_screen(
        request: ScreenDefinitionRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        from ..services.screen_service import ScreenService

        try:
            return ScreenService(active_config.database_path).save_definition(
                name=request.name,
                description=request.description,
                criteria=request.criteria,
                created_by=principal.username,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.get("/screens/{name}/run")
    def run_screen(
        name: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execute_tool("run_screen", {"name": name})
    @app.get("/runs")
    def runs(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return {
            "runs": BacktestService(
                active_config.database_path
            ).list_runs(limit)
        }
    @app.post("/runs/compare")
    def compare_runs(
        request: RunComparisonRequest,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return evidence_service.compare_runs(request.run_ids)
    @app.post("/experiments/robustness")
    def run_robustness_experiment(
        request: RobustnessExperimentRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return robustness_service.run(
            **request.model_dump(),
            requested_by=principal.username,
        )
    @app.post("/experiments/robustness/submit")
    def submit_robustness_experiment(
        request: RobustnessExperimentRequest,
        background_tasks: BackgroundTasks,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        task = task_service.submit(
            task_type="robustness_experiment",
            payload={
                **request.model_dump(),
                "requested_by": principal.username,
            },
            requested_by=principal.username,
        )
        background_tasks.add_task(
            task_service.run_due,
            f"api:{principal.username}",
            1,
        )
        return task
    @app.get("/experiments/robustness")
    def list_robustness_experiments(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return robustness_service.list(limit)
    @app.get("/experiments/robustness/{experiment_id}")
    def robustness_experiment(
        experiment_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return robustness_service.get(experiment_id)
    @app.post("/experiments/robustness/{experiment_id}/reports")
    def create_robustness_report(
        experiment_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return evidence_service.create_robustness_report(
            experiment_id,
            created_by=principal.username,
        )
    @app.get("/runs/{run_id}")
    def run_detail(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_backtest_result", payload)
    @app.get("/runs/{run_id}/performance")
    def run_performance(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_performance", payload)
    @app.get("/runs/{run_id}/risk")
    def run_risk(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_risk_decisions", payload)
    @app.get("/runs/{run_id}/orders")
    def run_orders(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        payload = RunIdInput(run_id=run_id).model_dump(mode="json")
        return execute_tool("get_order_timeline", payload)
    @app.get("/runs/{run_id}/timeline")
    def run_timeline(
        run_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return evidence_service.run_timeline(run_id)
    @app.post("/runs/{run_id}/reports")
    def create_run_report(
        run_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return evidence_service.create_run_report(
            run_id,
            created_by=principal.username,
        )
    @app.get("/reports")
    def reports(
        limit: int = 100,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 500",
            )
        return evidence_service.list_reports(limit)
    @app.get("/reports/{report_id}")
    def report_detail(
        report_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return evidence_service.get_report(report_id)
