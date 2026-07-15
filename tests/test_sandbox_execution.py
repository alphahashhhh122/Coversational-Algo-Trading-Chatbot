from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.db import connect
from iimc_trading_platform.domain import ExecutionMode, OrderStatus
from iimc_trading_platform.infrastructure import (
    DuckDBAuditRepository,
    initialize_database,
)
from iimc_trading_platform.infrastructure.openalgo import OpenAlgoResponseError
from iimc_trading_platform.services import AuditService, RiskService
from iimc_trading_platform.services.sandbox_execution_service import (
    SandboxExecutionService,
)
from iimc_trading_platform.tools.registry import build_default_tool_registry


class FakeSandboxBroker:
    def __init__(self, *, analyze: bool = True) -> None:
        self.analyze = analyze
        self.place_calls = 0
        self.status = "complete"

    def analyzer_status(self) -> dict:
        return {
            "analyze_mode": self.analyze,
            "mode": "analyze" if self.analyze else "live",
        }

    def place_sandbox_order(self, **kwargs) -> dict:
        self.place_calls += 1
        return {
            "status": "success",
            "mode": "analyze",
            "orderid": "sandbox_123",
        }

    def place_live_order(self, **kwargs) -> dict:
        self.place_calls += 1
        return {
            "status": "success",
            "mode": "live",
            "orderid": "live_123",
        }

    def cancel_order(self, *, order_id: str, strategy: str) -> dict:
        self.status = "cancelled"
        return {
            "status": "success",
            "mode": "analyze",
            "orderid": order_id,
            "message": "Order cancelled successfully",
        }

    def order_status(self, *, order_id: str, strategy: str) -> dict:
        return {
            "orderid": order_id,
            "order_status": self.status,
            "average_price": 101.25,
            "symbol": "NHPC",
            "quantity": "1",
        }


class UncertainSandboxBroker(FakeSandboxBroker):
    def place_sandbox_order(self, **kwargs) -> dict:
        self.place_calls += 1
        raise TimeoutError("response timed out")


class RejectedSandboxBroker(FakeSandboxBroker):
    def place_sandbox_order(self, **kwargs) -> dict:
        self.place_calls += 1
        raise OpenAlgoResponseError("Cannot place MARKET order without a price")


class BlockingSandboxBroker(FakeSandboxBroker):
    def __init__(self) -> None:
        super().__init__()
        self.submission_started = threading.Event()
        self.release_submission = threading.Event()

    def place_sandbox_order(self, **kwargs) -> dict:
        self.submission_started.set()
        self.release_submission.wait(timeout=2)
        return super().place_sandbox_order(**kwargs)


class SandboxExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sandbox.duckdb"
        initialize_database(self.db_path)
        self.audit = AuditService(DuckDBAuditRepository(self.db_path))
        self.decision_id = self._approved_decision()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_approval_submission_and_reconciliation(self) -> None:
        broker = FakeSandboxBroker()
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )
        intent = self._prepare(service)
        approved = service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="operator_user",
            reason="Reviewed quantity and sandbox destination",
        )
        submitted = service.submit(
            approved["intent_id"],
            actor="operator_user",
        )
        reconciled = service.reconcile(submitted["intent_id"])

        self.assertEqual(broker.place_calls, 1)
        self.assertEqual(reconciled["status"], "filled")
        self.assertEqual(reconciled["broker_order_id"], "sandbox_123")
        self.assertTrue(reconciled["reconciliation_snapshot_id"].startswith("oas_"))
        con = connect(self.db_path)
        try:
            order = con.execute(
                """
                SELECT status, broker_order_id, average_fill_price
                FROM order_events
                WHERE order_id = ?
                """,
                [reconciled["order_id"]],
            ).fetchone()
            fill = con.execute(
                """
                SELECT order_id, symbol, side, quantity, price, fees, realized_pnl
                FROM trade_fills
                WHERE order_id = ?
                """,
                [reconciled["order_id"]],
            ).fetchone()
            audit_actions = [
                row[0]
                for row in con.execute(
                    """
                    SELECT action
                    FROM audit_events
                    WHERE entity_type = 'order_intent'
                      AND entity_id = ?
                    ORDER BY created_at
                    """,
                    [intent["intent_id"]],
                ).fetchall()
            ]
        finally:
            con.close()
        self.assertEqual(
            order,
            (OrderStatus.FILLED.value, "sandbox_123", 101.25),
        )
        self.assertEqual(
            fill,
            (reconciled["order_id"], "NHPC", "BUY", 1, 101.25, 0.0, 0.0),
        )
        self.assertEqual(
            audit_actions,
            [
                "approval_requested",
                "submission_started",
                "submitted",
                "reconciled",
            ],
        )

    def test_live_intent_is_blocked_when_config_disabled(self) -> None:
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            FakeSandboxBroker(),
            require_approval=True,
            allow_live_trading=False,
        )

        with self.assertRaises(PermissionError):
            service.prepare_live_intent(
                decision_id=self._approved_live_decision(),
                symbol="NHPC",
                exchange="NSE",
                side="BUY",
                product="MIS",
                order_type="MARKET",
                quantity=1,
                strategy_name="IIMC_Live_Test",
                requested_by="user",
            )

    def test_live_intent_requires_approval_and_submits_live_order(self) -> None:
        broker = FakeSandboxBroker(analyze=False)
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=False,
            allow_live_trading=True,
        )
        intent = service.prepare_live_intent(
            decision_id=self._approved_live_decision(),
            symbol="NHPC",
            exchange="NSE",
            side="BUY",
            product="MIS",
            order_type="MARKET",
            quantity=1,
            strategy_name="IIMC_Live_Test",
            requested_by="user",
        )
        self.assertEqual(intent["execution_mode"], "live")
        self.assertEqual(intent["status"], "pending_approval")
        self.assertIsNotNone(intent["approval_id"])

        approved = service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="manual_approver",
            reason="Tiny live smoke test reviewed manually",
        )
        submitted = service.submit(approved["intent_id"], actor="manual_approver")

        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(submitted["broker_order_id"], "live_123")
        con = connect(self.db_path)
        try:
            stored = con.execute(
                """
                SELECT execution_mode, broker_order_id
                FROM order_events
                WHERE order_id = ?
                """,
                [submitted["order_id"]],
            ).fetchone()
            approval_action = con.execute(
                """
                SELECT requested_action
                FROM approval_requests
                WHERE approval_id = ?
                """,
                [intent["approval_id"]],
            ).fetchone()
        finally:
            con.close()
        self.assertEqual(stored, ("live", "live_123"))
        self.assertEqual(approval_action, ("submit_openalgo_live_order",))

    def test_live_submission_refuses_degraded_provider_before_order_call(self) -> None:
        broker = FakeSandboxBroker(analyze=False)
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=False,
            allow_live_trading=True,
            provider_readiness=lambda: {
                "ok": False,
                "message": "Account position check failed",
            },
        )
        intent = service.prepare_live_intent(
            decision_id=self._approved_live_decision(),
            symbol="NHPC",
            exchange="NSE",
            side="BUY",
            product="MIS",
            order_type="MARKET",
            quantity=1,
            strategy_name="IIMC_Live_Test",
        )
        service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="manual_approver",
            reason="Live intent reviewed",
        )

        with self.assertRaisesRegex(ValueError, "readiness failed"):
            service.submit(intent["intent_id"], actor="manual_approver")
        self.assertEqual(broker.place_calls, 0)

    def test_submitted_intent_can_be_cancelled_and_audited(self) -> None:
        broker = FakeSandboxBroker()
        broker.status = "open"
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )
        intent = self._prepare(service)
        approved = service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="risk_owner",
            reason="Approved for sandbox cancellation test",
        )
        submitted = service.submit(approved["intent_id"], actor="user")
        cancelled = service.cancel(submitted["intent_id"], actor="user")

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["broker_order_id"], "sandbox_123")
        self.assertEqual(cancelled["broker_cancel_state"]["mode"], "analyze")
        con = connect(self.db_path)
        try:
            latest_order = con.execute(
                """
                SELECT status
                FROM order_events
                WHERE order_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [cancelled["order_id"]],
            ).fetchone()
            audit_actions = [
                row[0]
                for row in con.execute(
                    """
                    SELECT action
                    FROM audit_events
                    WHERE entity_type = 'order_intent'
                      AND entity_id = ?
                    ORDER BY created_at
                    """,
                    [cancelled["intent_id"]],
                ).fetchall()
            ]
        finally:
            con.close()
        self.assertEqual(latest_order, (OrderStatus.CANCELLED.value,))
        self.assertIn("cancelled", audit_actions)

    def test_live_mode_refuses_submission_before_order_call(self) -> None:
        broker = FakeSandboxBroker(analyze=False)
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )
        intent = self._prepare(service)
        service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="user",
            reason="Sandbox test approved",
        )

        with self.assertRaisesRegex(ValueError, "analyzer mode"):
            service.submit(intent["intent_id"], actor="user")
        self.assertEqual(broker.place_calls, 0)

    def test_uncertain_submission_is_not_automatically_retryable(self) -> None:
        broker = UncertainSandboxBroker()
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )
        intent = self._prepare(service)
        service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="user",
            reason="Sandbox test approved",
        )

        with self.assertRaises(TimeoutError):
            service.submit(intent["intent_id"], actor="user")
        stored = service.get_intent(intent["intent_id"])
        self.assertEqual(stored["status"], "submission_uncertain")
        with self.assertRaisesRegex(ValueError, "must be approved"):
            service.submit(intent["intent_id"], actor="user")
        self.assertEqual(broker.place_calls, 1)

    def test_provider_rejection_is_failed_not_submission_uncertain(self) -> None:
        broker = RejectedSandboxBroker()
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )
        intent = self._prepare(service)
        service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="user",
            reason="Sandbox test approved",
        )

        with self.assertRaisesRegex(ValueError, "No analyzer order was created"):
            service.submit(intent["intent_id"], actor="user")
        stored = service.get_intent(intent["intent_id"])
        self.assertEqual(stored["status"], "failed")
        self.assertIn("Cannot place MARKET", stored["rejection_reason"])

    def test_submission_claim_prevents_a_duplicate_broker_call(self) -> None:
        broker = BlockingSandboxBroker()
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )
        intent = self._prepare(service)
        approved = service.decide(
            intent["approval_id"],
            approved=True,
            decided_by="user",
            reason="Sandbox test approved",
        )
        errors: list[Exception] = []

        def submit_first_request() -> None:
            try:
                service.submit(approved["intent_id"], actor="first_user")
            except Exception as exc:  # pragma: no cover - assertion below
                errors.append(exc)

        first = threading.Thread(target=submit_first_request)
        first.start()
        self.assertTrue(broker.submission_started.wait(timeout=2))

        with self.assertRaisesRegex(ValueError, "already in progress"):
            service.submit(approved["intent_id"], actor="second_user")
        broker.release_submission.set()
        first.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(broker.place_calls, 1)

    def test_rejected_approval_cannot_submit(self) -> None:
        broker = FakeSandboxBroker()
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )
        intent = self._prepare(service)
        rejected = service.decide(
            intent["approval_id"],
            approved=False,
            decided_by="risk_owner",
            reason="Quantity not desired by user",
        )

        self.assertEqual(rejected["status"], "rejected")
        with self.assertRaisesRegex(ValueError, "must be approved"):
            service.submit(intent["intent_id"], actor="user")

    def test_research_risk_decision_cannot_become_order_intent(self) -> None:
        research = RiskService(self.db_path).evaluate(
            run_id="run_research",
            signal_id="sig_research",
            signal_type="entry",
            symbol="NHPC",
            price=100.0,
            requested_quantity=1,
            confidence=1.0,
            execution_mode=ExecutionMode.RESEARCH,
        )
        service = SandboxExecutionService(
            self.db_path,
            self.audit,
            FakeSandboxBroker(),
        )

        with self.assertRaisesRegex(ValueError, "semi_auto mode"):
            service.prepare_intent(
                decision_id=research.decision_id,
                symbol="NHPC",
                exchange="NSE",
                side="BUY",
                product="MIS",
                order_type="MARKET",
                quantity=1,
                strategy_name="IIMC_Demo",
            )

    def test_llm_tool_registry_cannot_approve_or_submit_requests(self) -> None:
        names = {
            tool["name"]
            for tool in build_default_tool_registry(
                self.db_path,
                openalgo_base_url="http://127.0.0.1:5000",
                openalgo_api_key="configured",
            ).list_tools()
        }
        self.assertIn("prepare_sandbox_order_intent", names)
        self.assertNotIn("submit_approved_sandbox_intent", names)
        self.assertTrue(
            {
                "approve_request",
                "decide_approval",
                "approve_order_intent",
            }.isdisjoint(names)
        )

    def test_http_workflow_submits_paper_intent_without_manual_approval(self) -> None:
        broker = FakeSandboxBroker()
        config = AppConfig(
            database_path=self.db_path,
            artifacts_dir=Path(self.temp_dir.name) / "artifacts",
            openalgo_root=Path(self.temp_dir.name),
            openalgo_api_key="configured",
        )
        config.artifacts_dir.mkdir()
        with patch(
            "iimc_trading_platform.api.OpenAlgoClient",
            return_value=broker,
        ):
            client = TestClient(create_app(config))

        prepared_response = client.post(
            "/sandbox/intents",
            json={
                "decision_id": self.decision_id,
                "symbol": "NHPC",
                "exchange": "NSE",
                "side": "BUY",
                "product": "MIS",
                "order_type": "MARKET",
                "quantity": 1,
                "strategy_name": "IIMC_Demo",
                "requested_by": "user",
            },
        )
        self.assertEqual(prepared_response.status_code, 200)
        prepared = prepared_response.json()
        self.assertEqual(prepared["status"], "approved")
        self.assertIsNone(prepared["approval_id"])

        submitted = client.post(
            f"/sandbox/intents/{prepared['intent_id']}/submit",
            json={"actor": "user"},
        )
        self.assertEqual(submitted.status_code, 200)
        reconciled = client.post(
            f"/sandbox/intents/{prepared['intent_id']}/reconcile",
            json={"actor": "system"},
        )
        self.assertEqual(reconciled.status_code, 200)
        self.assertEqual(reconciled.json()["status"], "filled")

    def _prepare(
        self,
        service: SandboxExecutionService,
    ) -> dict:
        return service.prepare_intent(
            decision_id=self.decision_id,
            symbol="NHPC",
            exchange="NSE",
            side="BUY",
            product="MIS",
            order_type="MARKET",
            quantity=1,
            strategy_name="IIMC_Demo",
            requested_by="user",
        )

    def _approved_decision(self) -> str:
        evaluation = RiskService(self.db_path).evaluate(
            run_id="run_sandbox",
            signal_id="sig_sandbox",
            signal_type="entry",
            symbol="NHPC",
            price=100.0,
            requested_quantity=1,
            confidence=1.0,
            execution_mode=ExecutionMode.SEMI_AUTO,
        )
        return evaluation.decision_id

    def _approved_live_decision(self) -> str:
        evaluation = RiskService(
            self.db_path,
            allow_live_trading=True,
        ).evaluate(
            run_id="run_live",
            signal_id="sig_live",
            signal_type="entry",
            symbol="NHPC",
            price=100.0,
            requested_quantity=1,
            confidence=1.0,
            execution_mode=ExecutionMode.LIVE,
        )
        self.assertTrue(evaluation.approved)
        return evaluation.decision_id


if __name__ == "__main__":
    unittest.main()
