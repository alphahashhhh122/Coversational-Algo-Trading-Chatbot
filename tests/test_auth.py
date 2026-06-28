from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.auth_service import AuthService


SECRET = "test-auth-secret-that-is-longer-than-thirty-two-characters"
PASSWORD = "StrongPassword123"


class AuthenticationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "auth.duckdb"
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        initialize_database(self.db_path)
        self.auth = AuthService(self.db_path, secret=SECRET)
        self.auth.create_user(
            username="viewer",
            password=PASSWORD,
            role="viewer",
        )
        self.auth.create_user(
            username="researcher",
            password=PASSWORD,
            role="researcher",
        )
        self.auth.create_user(
            username="approver",
            password=PASSWORD,
            role="approver",
        )
        self.auth.create_user(
            username="admin",
            password=PASSWORD,
            role="admin",
        )
        self.client = TestClient(
            create_app(
                AppConfig(
                    database_path=self.db_path,
                    artifacts_dir=self.artifacts,
                    openalgo_root=root,
                    auth_required=True,
                    auth_secret=SECRET,
                )
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_protected_endpoint_requires_bearer_token(self) -> None:
        response = self.client.get("/datasets")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.headers["www-authenticate"],
            "Bearer",
        )

    def test_login_me_logout_and_revocation(self) -> None:
        token = self._login("viewer")
        headers = {"Authorization": f"Bearer {token}"}
        me = self.client.get("/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "viewer")
        self.assertEqual(me.json()["role"], "viewer")

        logout = self.client.post("/auth/logout", headers=headers)
        self.assertEqual(logout.status_code, 200)
        revoked = self.client.get("/datasets", headers=headers)
        self.assertEqual(revoked.status_code, 401)

    def test_role_authorization_blocks_viewer_mutation(self) -> None:
        viewer = self._login("viewer")
        response = self.client.post(
            "/backtests",
            headers={"Authorization": f"Bearer {viewer}"},
            json={
                "strategy_name": "ema_crossover",
                "parameters": {},
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("researcher", response.json()["detail"]["message"])

    def test_viewer_chat_cannot_invoke_mutating_backtest_tool(self) -> None:
        token = self._login("viewer")

        response = self.client.post(
            "/chat",
            json={"message": "Run an EMA backtest"},
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "unsupported")

    def test_viewer_cannot_start_robustness_experiment(self) -> None:
        token = self._login("viewer")
        response = self.client.post(
            "/experiments/robustness",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "strategy_name": "ema_crossover",
                "dataset_id": "dataset_missing",
                "parameter_grid": [
                    {
                        "fast_period": 9,
                        "slow_period": 21,
                        "stop_loss_pct": 0.02,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_approver_can_access_pending_approvals(self) -> None:
        researcher = self._login("researcher")
        denied = self.client.get(
            "/approvals/pending",
            headers={"Authorization": f"Bearer {researcher}"},
        )
        self.assertEqual(denied.status_code, 403)

        approver = self._login("approver")
        allowed = self.client.get(
            "/approvals/pending",
            headers={"Authorization": f"Bearer {approver}"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json(), {"approvals": []})

    def test_invalid_password_is_rejected(self) -> None:
        response = self.client.post(
            "/auth/login",
            json={"username": "viewer", "password": "incorrect"},
        )
        self.assertEqual(response.status_code, 401)

    def test_retention_execution_requires_admin(self) -> None:
        approver = self._login("approver")
        denied = self.client.post(
            "/operations/retention/execute",
            headers={"Authorization": f"Bearer {approver}"},
            json={
                "confirmation": "PURGE_EXPIRED_OPERATIONAL_DATA",
                "policy_names": ["expired_auth_sessions"],
            },
        )
        self.assertEqual(denied.status_code, 403)

        admin = self._login("admin")
        allowed = self.client.post(
            "/operations/retention/execute",
            headers={"Authorization": f"Bearer {admin}"},
            json={
                "confirmation": "PURGE_EXPIRED_OPERATIONAL_DATA",
                "policy_names": ["expired_auth_sessions"],
            },
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["mode"], "execute")

    def _login(self, username: str) -> str:
        response = self.client.post(
            "/auth/login",
            json={"username": username, "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["access_token"]


if __name__ == "__main__":
    unittest.main()
