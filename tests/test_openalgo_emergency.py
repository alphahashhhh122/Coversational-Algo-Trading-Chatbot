from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.openalgo_service import (
    OpenAlgoSnapshotService,
)


class FakeEmergencyBroker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def cancel_all_orders(self, strategy: str = "iimc_platform") -> dict:
        self.calls.append(f"cancel_all:{strategy}")
        return {"status": "success", "canceled_orders": ["oid_1", "oid_2"]}

    def close_all_positions(self, strategy: str = "iimc_platform") -> dict:
        self.calls.append(f"close_all:{strategy}")
        return {"status": "success", "message": "All positions squared off"}

    def account_snapshot(self, snapshot_type: str) -> dict:
        return {"data": {"holdings": [{"symbol": "RELIANCE", "quantity": 5}]}}


class OpenAlgoEmergencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "emergency.duckdb"
        initialize_database(self.db_path)
        self.broker = FakeEmergencyBroker()
        with patch(
            "iimc_trading_platform.api.OpenAlgoClient",
            return_value=self.broker,
        ), patch(
            "iimc_trading_platform.tools.registry.OpenAlgoClient",
            return_value=self.broker,
        ):
            self.client = TestClient(
                create_app(
                    AppConfig(
                        database_path=self.db_path,
                        artifacts_dir=root / "artifacts",
                        openalgo_root=root,
                        openalgo_api_key="configured",
                    )
                )
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cancel_all_orders_is_audited(self) -> None:
        response = self.client.post("/openalgo/emergency/cancel_all_orders")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["action"], "cancel_all_orders")
        self.assertTrue(payload["audit_id"].startswith("audit_"))
        self.assertIn("cancel_all:iimc_platform", self.broker.calls)
        con = connect(self.db_path)
        try:
            stored = con.execute(
                "SELECT COUNT(*) FROM openalgo_snapshots "
                "WHERE snapshot_type = 'emergency_cancel_all_orders'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(stored, 1)

    def test_square_off_positions_works(self) -> None:
        response = self.client.post(
            "/openalgo/emergency/square_off_positions"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("close_all:iimc_platform", self.broker.calls)

    def test_unknown_action_rejected(self) -> None:
        response = self.client.post("/openalgo/emergency/delete_everything")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.broker.calls, [])

    def test_holdings_snapshot_supported(self) -> None:
        response = self.client.get("/openalgo/holdings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["snapshot_type"], "holdings")
        self.assertEqual(
            payload["data"]["holdings"][0]["symbol"], "RELIANCE",
        )

    def test_emergency_requires_credentials(self) -> None:
        bare_root = Path(self.temp_dir.name) / "bare"
        bare_root.mkdir()
        bare_db = bare_root / "bare.duckdb"
        initialize_database(bare_db)
        bare_client = TestClient(
            create_app(
                AppConfig(
                    database_path=bare_db,
                    artifacts_dir=bare_root / "artifacts",
                    openalgo_root=bare_root,
                )
            )
        )

        response = bare_client.post(
            "/openalgo/emergency/cancel_all_orders"
        )

        self.assertEqual(response.status_code, 503)

    def test_service_rejects_unknown_action(self) -> None:
        service = OpenAlgoSnapshotService(self.db_path, self.broker)
        with self.assertRaises(ValueError):
            service.emergency_action("format_disk", actor="tester")


if __name__ == "__main__":
    unittest.main()
