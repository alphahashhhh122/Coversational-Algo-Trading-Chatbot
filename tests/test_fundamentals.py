from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import OfflineOrchestrator
from iimc_trading_platform.services.fundamentals_service import (
    FundamentalsService,
)
from iimc_trading_platform.tools.registry import build_default_tool_registry

_STATEMENTS = [
    {
        "period": "FY2025",
        "period_end": "2025-03-31",
        "revenue": 1000.0,
        "operating_profit": 200.0,
        "net_income": 150.0,
        "total_assets": 2000.0,
        "total_equity": 1000.0,
        "total_debt": 400.0,
        "current_assets": 600.0,
        "current_liabilities": 300.0,
        "operating_cash_flow": 220.0,
        "capital_expenditure": 70.0,
        "shares_outstanding": 100.0,
        "dividends_paid": 50.0,
    },
    {
        "period": "FY2026",
        "period_end": "2026-03-31",
        "revenue": 1200.0,
        "operating_profit": 264.0,
        "net_income": 180.0,
        "total_assets": 2300.0,
        "total_equity": 1150.0,
        "total_debt": 380.0,
        "current_assets": 690.0,
        "current_liabilities": 300.0,
        "operating_cash_flow": 260.0,
        "capital_expenditure": 80.0,
        "shares_outstanding": 100.0,
        "dividends_paid": 60.0,
    },
]


class FundamentalsServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "fund.duckdb"
        initialize_database(self.db_path)
        self.service = FundamentalsService(self.db_path)
        self.service.import_statements(
            symbol="acme",
            currency="INR",
            source="annual_report_fy2026",
            statements=_STATEMENTS,
            imported_by="tester",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _ratio(self, analysis, name):
        return next(
            item for item in analysis["ratios"] if item["name"] == name
        )

    def test_ratios_are_deterministic_with_formulas(self) -> None:
        analysis = self.service.analyze("ACME", market_price=36.0)

        self.assertEqual(analysis["latest_period"], "FY2026")
        self.assertAlmostEqual(
            self._ratio(analysis, "revenue_growth")["value"], 0.2,
        )
        self.assertAlmostEqual(
            self._ratio(analysis, "operating_margin")["value"], 0.22,
        )
        self.assertAlmostEqual(
            self._ratio(analysis, "roe")["value"], 180.0 / 1150.0, places=4,
        )
        self.assertAlmostEqual(
            self._ratio(analysis, "current_ratio")["value"], 2.3,
        )
        self.assertAlmostEqual(
            self._ratio(analysis, "free_cash_flow")["value"], 180.0,
        )
        eps = self._ratio(analysis, "eps")
        self.assertAlmostEqual(eps["value"], 1.8)
        self.assertEqual(
            eps["formula"], "net_income / shares_outstanding",
        )
        self.assertAlmostEqual(
            self._ratio(analysis, "pe_ratio")["value"], 20.0,
        )
        self.assertTrue(analysis["no_synthetic_fallback"])

    def test_missing_inputs_produce_warnings_not_values(self) -> None:
        self.service.import_statements(
            symbol="sparse",
            currency="INR",
            source="partial",
            statements=[{
                "period": "FY2026",
                "period_end": "2026-03-31",
                "revenue": 500.0,
            }],
            imported_by="tester",
        )

        analysis = self.service.analyze("SPARSE")

        names = {item["name"] for item in analysis["ratios"]}
        self.assertNotIn("roe", names)
        self.assertTrue(
            any("missing" in warning for warning in analysis["warnings"])
        )

    def test_unknown_symbol_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.service.analyze("NOPE")


class FundamentalsApiAndRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "fund.duckdb"
        initialize_database(self.db_path)
        self.client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=root / "artifacts",
                    openalgo_root=root,
                )
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_import_then_analyze_via_api_and_chat(self) -> None:
        imported = self.client.post(
            "/fundamentals/statements",
            json={
                "symbol": "ACME",
                "currency": "INR",
                "source": "annual_report",
                "statements": _STATEMENTS,
            },
        )
        self.assertEqual(imported.status_code, 200)
        self.assertTrue(imported.json()["audit_id"].startswith("audit_"))

        analysis = self.client.get(
            "/fundamentals/ACME/analysis", params={"market_price": 36.0},
        )
        self.assertEqual(analysis.status_code, 200)
        payload = analysis.json()
        self.assertEqual(payload["symbol"], "ACME")
        self.assertTrue(payload["ratios"])

        chat = self.client.post(
            "/chat",
            json={
                "session_id": "s_fund",
                "message": "Analyze ACME fundamentally",
            },
        )
        self.assertEqual(chat.status_code, 200)
        chat_payload = chat.json()
        self.assertEqual(chat_payload["intent"], "analyze_fundamentals")
        self.assertIn("revenue_growth", chat_payload["answer"])

    def test_router_routes_fundamental_analysis_phrase(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        decision = OfflineOrchestrator().select_tool(
            "Run a fundamental analysis of TCS", [], registry,
        )
        self.assertEqual(decision.tool_name, "analyze_fundamentals")
        self.assertEqual(decision.arguments["symbol"], "TCS")


if __name__ == "__main__":
    unittest.main()
