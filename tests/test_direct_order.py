from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import OfflineOrchestrator
from iimc_trading_platform.infrastructure import DuckDBAuditRepository
from iimc_trading_platform.services.audit_service import AuditService
from iimc_trading_platform.services.risk_service import RiskService
from iimc_trading_platform.services.sandbox_execution_service import (
    SandboxExecutionService,
)
from iimc_trading_platform.tools.registry import build_default_tool_registry


class QuoteBroker:
    def __init__(self, ltp: float = 1334.7) -> None:
        self.ltp = ltp

    def analyzer_status(self) -> dict:
        return {"analyze_mode": True, "mode": "analyze"}

    def quote(self, *, symbol: str, exchange: str) -> dict:
        return {"data": {"ltp": self.ltp}}

    def place_sandbox_order(self, **kwargs) -> dict:
        return {"status": "success", "orderid": "sbx_1"}

    def order_status(self, *, order_id: str, strategy: str) -> dict:
        return {"data": {"order_status": "complete"}}


class DirectOrderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "direct.duckdb"
        initialize_database(self.db_path)
        self.audit = AuditService(DuckDBAuditRepository(self.db_path))
        self.risk = RiskService(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _service(self, broker):
        return SandboxExecutionService(
            self.db_path,
            self.audit,
            broker,
            require_approval=True,
        )

    def test_direct_buy_creates_pending_intent(self) -> None:
        service = self._service(QuoteBroker(ltp=1334.7))

        intent = service.prepare_direct_intent(
            risk_service=self.risk,
            symbol="reliance",
            side="BUY",
            quantity=1,
        )

        self.assertEqual(intent["symbol"], "RELIANCE")
        self.assertEqual(intent["side"], "BUY")
        self.assertEqual(intent["status"], "pending_approval")
        self.assertIsNotNone(intent["approval_id"])

    def test_direct_order_requires_broker(self) -> None:
        service = self._service(None)
        with self.assertRaisesRegex(ValueError, "OpenAlgo credentials"):
            service.prepare_direct_intent(
                risk_service=self.risk, symbol="INFY", side="BUY", quantity=1,
            )

    def test_no_quote_is_refused(self) -> None:
        service = self._service(QuoteBroker(ltp=0))
        with self.assertRaisesRegex(ValueError, "No fresh quote"):
            service.prepare_direct_intent(
                risk_service=self.risk, symbol="INFY", side="BUY", quantity=1,
            )

    def test_oversized_order_is_refused(self) -> None:
        service = self._service(QuoteBroker(ltp=1000.0))
        # 100 shares exceeds the default max_quantity (50); the order must
        # be refused rather than silently shrunk.
        with self.assertRaises(ValueError):
            service.prepare_direct_intent(
                risk_service=self.risk,
                symbol="RELIANCE",
                side="BUY",
                quantity=100,
            )

    def test_sell_direct_order_refused(self) -> None:
        service = self._service(QuoteBroker())
        with self.assertRaisesRegex(ValueError, "support BUY"):
            service.prepare_direct_intent(
                risk_service=self.risk, symbol="INFY", side="SELL", quantity=1,
            )


class DirectOrderRoutingTest(unittest.TestCase):
    def _decide(self, message):
        registry = build_default_tool_registry(
            Path("unused.duckdb"),
            openalgo_base_url="http://127.0.0.1:5000",
            openalgo_api_key="test",
        )
        return OfflineOrchestrator().select_tool(message, [], registry)

    def test_buy_market_routes_to_direct_order(self) -> None:
        d = self._decide("Buy 10 RELIANCE at market")
        self.assertEqual(d.tool_name, "prepare_direct_order")
        self.assertEqual(d.arguments["symbol"], "RELIANCE")
        self.assertEqual(d.arguments["quantity"], 10)
        self.assertEqual(d.arguments["side"], "BUY")

    def test_buy_limit_routes_with_price(self) -> None:
        d = self._decide("Buy 5 INFY at limit 1400")
        self.assertEqual(d.tool_name, "prepare_direct_order")
        self.assertEqual(d.arguments["order_type"], "LIMIT")
        self.assertEqual(d.arguments["limit_price"], 1400.0)

    def test_buy_with_risk_id_uses_strategy_path(self) -> None:
        d = self._decide("Prepare paper order for risk_abc123 BUY 1 TCS")
        self.assertNotEqual(d.tool_name, "prepare_direct_order")


if __name__ == "__main__":
    unittest.main()
