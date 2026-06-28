from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.readiness_service import (
    production_readiness,
)


class SecurityTest(unittest.TestCase):
    def test_security_headers_and_request_id_are_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "security.duckdb"
            artifacts = root / "artifacts"
            artifacts.mkdir()
            initialize_database(db_path)
            client = TestClient(
                create_app(
                    AppConfig(
                        database_path=db_path,
                        artifacts_dir=artifacts,
                        openalgo_root=root,
                    )
                )
            )

            response = client.get(
                "/health",
                headers={"X-Request-ID": "request-test-1"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers["x-request-id"],
                "request-test-1",
            )
            self.assertEqual(
                response.headers["x-content-type-options"],
                "nosniff",
            )
            self.assertEqual(response.headers["x-frame-options"], "DENY")
            self.assertIn(
                "frame-ancestors 'none'",
                response.headers["content-security-policy"],
            )

    def test_rate_limit_and_body_limit_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "limits.duckdb"
            artifacts = root / "artifacts"
            artifacts.mkdir()
            initialize_database(db_path)
            client = TestClient(
                create_app(
                    AppConfig(
                        database_path=db_path,
                        artifacts_dir=artifacts,
                        openalgo_root=root,
                        rate_limit_per_minute=2,
                        max_request_bytes=100,
                    )
                )
            )

            self.assertEqual(client.get("/datasets").status_code, 200)
            self.assertEqual(client.get("/datasets").status_code, 200)
            limited = client.get("/datasets")
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.headers["retry-after"], "60")

            oversized = client.post(
                "/chat",
                content=b'{"message":"' + b"x" * 200 + b'"}',
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(oversized.status_code, 413)

    def test_production_readiness_requires_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "ready.duckdb"
            artifacts = root / "artifacts"
            artifacts.mkdir()
            initialize_database(db_path)

            result = production_readiness(
                AppConfig(
                    environment="production",
                    database_path=db_path,
                    artifacts_dir=artifacts,
                    openalgo_root=root,
                    auth_required=False,
                )
            )

            self.assertEqual(result["status"], "not_ready")
            self.assertFalse(result["checks"]["production_auth_required"])
            self.assertFalse(
                result["checks"]["production_openai_configured"]
            )

    def test_liveness_does_not_depend_on_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "live.duckdb"
            artifacts = root / "artifacts"
            artifacts.mkdir()
            initialize_database(db_path)
            client = TestClient(
                create_app(
                    AppConfig(
                        environment="production",
                        database_path=db_path,
                        artifacts_dir=artifacts,
                        openalgo_root=root,
                        auth_required=True,
                        auth_secret=(
                            "production-test-secret-longer-than-32-chars"
                        ),
                    )
                )
            )

            live = client.get("/live")
            ready = client.get("/ready")

            self.assertEqual(live.status_code, 200)
            self.assertEqual(live.json(), {"status": "alive"})
            self.assertEqual(ready.status_code, 503)


if __name__ == "__main__":
    unittest.main()
