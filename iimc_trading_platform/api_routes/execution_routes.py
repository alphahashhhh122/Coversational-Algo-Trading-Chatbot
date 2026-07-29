"""Broker-facing surfaces: sandbox intents, approvals, portfolios, watches.

Lifted out of ``create_app``. The handler bodies are unchanged; what was
an implicit closure over the application's service objects is now a
signature that names them.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from ..api_models import (
    ApprovalDecisionRequest,
    BatchSubmitRequest,
    CreatePortfolioRequest,
    DirectOrderRequest,
    PortfolioControlRequest,
    PortfolioFillRequest,
    PortfolioRiskCheckRequest,
    SandboxActionRequest,
)
from ..infrastructure import (
    DuckDBAuditRepository,
    OpenAlgoUnavailableError,
)
from ..services import (
    AuditService,
    Principal,
)
from ..tools.registry import (
    OpenAlgoSnapshotInput,
    PrepareSandboxIntentInput,
)
from pydantic import ValidationError
from typing import Any

from ..services.instrument_names import company_name as _company_name


_NAME_SYMBOL_KEYS = ("symbol", "tradingsymbol", "tsym")
def _annotate_company_names(data: Any, openalgo_root: Path) -> None:
    """Add a readable ``company_name`` to each broker row, in place."""
    if not isinstance(data, list):
        return
    for row in data:
        if not isinstance(row, dict) or row.get("company_name"):
            continue
        symbol = next(
            (row[key] for key in _NAME_SYMBOL_KEYS if row.get(key)), None
        )
        exchange = row.get("exchange") or "NSE"
        name = _company_name(symbol, exchange, openalgo_root=openalgo_root)
        if name:
            row["company_name"] = name


def register(
    app: FastAPI,
    *,
    active_config: Any,
    approver: Any,
    direct_risk_service: Any,
    execute_tool: Any,
    openalgo_snapshot_service: Any,
    portfolio_service: Any,
    researcher: Any,
    sandbox_service: Any,
    viewer: Any,
    watch_service: Any,
) -> None:
    @app.get("/watches")
    def list_watches_endpoint(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return watch_service.list()
    @app.post("/watches/check")
    def check_watches_endpoint(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return watch_service.evaluate()
    @app.delete("/watches/{watch_id}")
    def remove_watch_endpoint(
        watch_id: str,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return watch_service.remove_by_id(watch_id)
    @app.get("/openalgo/snapshots")
    def openalgo_snapshots(
        limit: int = 100,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return openalgo_snapshot_service.list(limit)
    @app.post("/openalgo/emergency/{action}")
    def openalgo_emergency(
        action: str,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        """Protective broker controls: cancel-all orders or square-off."""
        if action not in {"cancel_all_orders", "square_off_positions"}:
            raise HTTPException(
                status_code=422,
                detail=(
                    "action must be cancel_all_orders or "
                    "square_off_positions"
                ),
            )
        if not active_config.openalgo_api_key:
            raise HTTPException(
                status_code=503,
                detail={
                    "ok": False,
                    "status": "credential_required",
                    "safe_failure": True,
                    "message": "OPENALGO_API_KEY is not configured",
                    "no_synthetic_fallback": True,
                },
            )
        try:
            result = openalgo_snapshot_service.emergency_action(
                action,
                actor=principal.username,
            )
        except OpenAlgoUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        )
        event = audit.record(
            actor=principal.username,
            action=f"openalgo_emergency_{action}",
            entity_type="openalgo_emergency",
            entity_id=result["record_id"],
            payload={"broker_response": result["broker_response"]},
        )
        return {**result, "audit_id": event.audit_id}
    @app.get("/openalgo/{snapshot_type}")
    def openalgo_snapshot(
        snapshot_type: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if not active_config.openalgo_api_key:
            raise HTTPException(
                status_code=503,
                detail={
                    "ok": False,
                    "status": "credential_required",
                    "safe_failure": True,
                    "message": "OPENALGO_API_KEY is not configured",
                    "no_synthetic_fallback": True,
                },
            )
        try:
            payload = OpenAlgoSnapshotInput(
                snapshot_type=snapshot_type
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        result = execute_tool("get_openalgo_snapshot", payload)
        _annotate_company_names(
            result.get("data"), active_config.openalgo_root
        )
        return result
    @app.post("/sandbox/intents")
    def prepare_sandbox_intent(
        request: PrepareSandboxIntentInput,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        payload = request.model_dump()
        payload["requested_by"] = principal.username
        try:
            return sandbox_service.prepare_intent(**payload)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.post("/sandbox/direct-order")
    def prepare_direct_order(
        request: DirectOrderRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        """Discretionary buy anchored to a fresh quote (no strategy needed)."""
        try:
            return sandbox_service.prepare_direct_intent(
                risk_service=direct_risk_service,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                exchange=request.exchange,
                product=request.product,
                order_type=request.order_type,
                limit_price=request.limit_price,
                requested_by=principal.username,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.get("/sandbox/intents")
    def sandbox_intents(
        limit: int = 50,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 200",
            )
        return sandbox_service.list_intents(limit)
    @app.get("/sandbox/intents/{intent_id}")
    def sandbox_intent(
        intent_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return sandbox_service.get_intent(intent_id)
    @app.get("/approvals/pending")
    def pending_approvals(
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return sandbox_service.list_pending_approvals()
    @app.post("/approvals/{approval_id}/decision")
    def decide_approval(
        approval_id: str,
        request: ApprovalDecisionRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        try:
            return sandbox_service.decide(
                approval_id,
                approved=request.approved,
                decided_by=principal.username,
                reason=request.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    @app.post("/sandbox/intents/{intent_id}/submit")
    def submit_sandbox_intent(
        intent_id: str,
        request: SandboxActionRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return sandbox_service.submit(
                intent_id,
                actor=principal.username,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    @app.post("/sandbox/intents/submit-batch")
    def submit_sandbox_intent_batch(
        request: BatchSubmitRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        """Submit several APPROVED intents as a basket.

        Each intent passes through the same gates as a single submission
        (approved status, atomic claim, idempotency, analyzer/live checks);
        failures are reported per intent and never halt honest reporting
        of the others.
        """
        results: list[dict[str, Any]] = []
        submitted = 0
        for intent_id in request.intent_ids:
            try:
                outcome = sandbox_service.submit(
                    intent_id,
                    actor=principal.username,
                )
                submitted += 1
                results.append(
                    {
                        "intent_id": intent_id,
                        "status": outcome.get("status", "submitted"),
                        "broker_order_id": outcome.get("broker_order_id"),
                    }
                )
            except (ValueError, PermissionError) as exc:
                results.append(
                    {
                        "intent_id": intent_id,
                        "status": "rejected",
                        "reason": str(exc),
                    }
                )
        return {
            "requested": len(request.intent_ids),
            "submitted": submitted,
            "results": results,
        }
    @app.post("/sandbox/intents/{intent_id}/reconcile")
    def reconcile_sandbox_intent(
        intent_id: str,
        request: SandboxActionRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return sandbox_service.reconcile(
                intent_id,
                actor=principal.username,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    @app.post("/sandbox/intents/{intent_id}/cancel")
    def cancel_sandbox_intent(
        intent_id: str,
        request: SandboxActionRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return sandbox_service.cancel(
                intent_id,
                actor=principal.username,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    @app.post("/portfolios")
    def create_portfolio(
        request: CreatePortfolioRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return portfolio_service.create(
            **request.model_dump(),
            created_by=principal.username,
        )
    @app.get("/portfolios")
    def portfolios(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return portfolio_service.list()
    @app.get("/portfolios/{portfolio_id}")
    def portfolio_snapshot(
        portfolio_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return portfolio_service.get(portfolio_id)
    @app.post("/portfolios/{portfolio_id}/risk-check")
    def portfolio_risk_check(
        portfolio_id: str,
        request: PortfolioRiskCheckRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return portfolio_service.evaluate_and_reserve(
            portfolio_id=portfolio_id,
            **request.model_dump(),
        )
    @app.post("/portfolios/{portfolio_id}/fills")
    def portfolio_fill(
        portfolio_id: str,
        request: PortfolioFillRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return portfolio_service.apply_fill(
            portfolio_id=portfolio_id,
            **request.model_dump(),
        )
    @app.post("/portfolio-risk/reservations/{reservation_id}/release")
    def release_portfolio_reservation(
        reservation_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return portfolio_service.release_reservation(reservation_id)
    @app.post("/portfolios/{portfolio_id}/trading-control")
    def set_portfolio_trading_control(
        portfolio_id: str,
        request: PortfolioControlRequest,
        principal: Principal = Depends(approver),
    ) -> dict[str, Any]:
        return portfolio_service.set_trading_enabled(
            portfolio_id=portfolio_id,
            enabled=request.enabled,
            reason=request.reason,
            changed_by=principal.username,
        )
