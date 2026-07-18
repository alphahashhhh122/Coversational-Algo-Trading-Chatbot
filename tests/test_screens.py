from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.fundamentals_service import (
    FundamentalsService,
)
from iimc_trading_platform.services.screen_service import ScreenService


def _statement(period, period_end, **overrides):
    base = {
        "period": period,
        "period_end": period_end,
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
    }
    base.update(overrides)
    return base


class ScreenServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "screens.duckdb"
        initialize_database(self.db_path)
        fundamentals = FundamentalsService(self.db_path)
        # STRONG: ROE 18%, D/E 0.3, net margin 15%
        fundamentals.import_statements(
            symbol="STRONG",
            currency="INR",
            source="test",
            statements=[
                _statement("FY2025", "2025-03-31"),
                _statement(
                    "FY2026", "2026-03-31",
                    net_income=180.0, total_debt=300.0,
                    revenue=1200.0, operating_profit=260.0,
                ),
            ],
            imported_by="tester",
        )
        # WEAK: heavy debt, thin margins
        fundamentals.import_statements(
            symbol="WEAK",
            currency="INR",
            source="test",
            statements=[
                _statement("FY2025", "2025-03-31"),
                _statement(
                    "FY2026", "2026-03-31",
                    net_income=20.0, total_debt=2500.0,
                ),
            ],
            imported_by="tester",
        )
        self.service = ScreenService(self.db_path)
        self.service.ensure_defaults()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_defaults_are_seeded_once(self) -> None:
        self.service.ensure_defaults()
        screens = self.service.list_definitions()["screens"]
        names = {item["name"] for item in screens}
        self.assertIn("quality", names)
        self.assertIn("growth", names)
        quality = next(s for s in screens if s["name"] == "quality")
        self.assertEqual(quality["latest_version"], 1)

    def test_quality_screen_separates_strong_from_weak(self) -> None:
        result = self.service.run("quality")

        matched = {item["symbol"] for item in result["matches"]}
        excluded = {item["symbol"] for item in result["excluded"]}
        self.assertIn("STRONG", matched)
        self.assertIn("WEAK", excluded)
        self.assertEqual(result["universe_size"], 2)
        weak = next(
            item for item in result["excluded"]
            if item["symbol"] == "WEAK"
        )
        self.assertTrue(weak["failed_criteria"])

    def test_saving_new_version_increments(self) -> None:
        saved = self.service.save_definition(
            name="quality",
            description="Tighter quality screen",
            criteria=[{"metric": "roe", "op": "gte", "value": 0.25}],
            created_by="tester",
        )
        self.assertEqual(saved["version"], 2)
        result = self.service.run("quality")
        self.assertEqual(result["version"], 2)
        self.assertFalse(result["matches"])

    def test_invalid_operator_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.save_definition(
                name="bad",
                description="bad",
                criteria=[{"metric": "roe", "op": "!!", "value": 1}],
                created_by="tester",
            )

    def test_unknown_screen_raises_with_available_list(self) -> None:
        with self.assertRaisesRegex(ValueError, "Available"):
            self.service.run("nonexistent")


class ScreenApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "screens.duckdb"
        initialize_database(self.db_path)
        FundamentalsService(self.db_path).import_statements(
            symbol="STRONG",
            currency="INR",
            source="test",
            statements=[
                _statement("FY2025", "2025-03-31"),
                _statement(
                    "FY2026", "2026-03-31",
                    net_income=180.0, total_debt=300.0,
                    revenue=1200.0, operating_profit=260.0,
                ),
            ],
            imported_by="tester",
        )
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

    def test_list_run_and_chat(self) -> None:
        listing = self.client.get("/screens")
        self.assertEqual(listing.status_code, 200)
        names = {item["name"] for item in listing.json()["screens"]}
        self.assertIn("quality", names)

        run = self.client.get("/screens/quality/run")
        self.assertEqual(run.status_code, 200)
        self.assertEqual(
            run.json()["matches"][0]["symbol"], "STRONG",
        )

        chat = self.client.post(
            "/chat",
            json={"session_id": "s_screen", "message": "Run the quality screen"},
        )
        self.assertEqual(chat.status_code, 200)
        payload = chat.json()
        self.assertEqual(payload["intent"], "run_screen")
        self.assertIn("STRONG", payload["answer"])

    def test_save_new_version_via_api(self) -> None:
        response = self.client.post(
            "/screens",
            json={
                "name": "dividend",
                "description": "Payout-focused screen",
                "criteria": [
                    {"metric": "payout_ratio", "op": "gte", "value": 0.2},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 1)


if __name__ == "__main__":
    unittest.main()
