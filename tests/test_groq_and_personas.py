from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app
from iimc_trading_platform.config import AppConfig, public_config
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import (
    OfflineOrchestrator,
    build_orchestrator,
)
from iimc_trading_platform.services.persona_service import PersonaService
from iimc_trading_platform.tools.registry import build_default_tool_registry


class GroqAndPersonaTest(unittest.TestCase):
    def test_personas_are_seeded_as_governed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)

            result = PersonaService(db_path).list()

        persona_ids = {
            persona["persona_id"]
            for persona in result["personas"]
        }
        self.assertIn("conservative_value", persona_ids)
        self.assertIn("intraday_momentum", persona_ids)
        self.assertGreaterEqual(len(persona_ids), 4)

    def test_persona_api_routes_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(database_path=Path(tmp) / "platform.duckdb")
            client = TestClient(create_app(config))

            list_response = client.get("/personas")
            detail_response = client.get("/personas/conservative_value")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(
            detail_response.json()["persona"]["persona_id"],
            "conservative_value",
        )

    def test_persona_tools_are_registered_and_routeable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.duckdb"
            initialize_database(db_path)
            registry = build_default_tool_registry(db_path)

            tools = {tool["name"] for tool in registry.list_tools()}
            decision = OfflineOrchestrator().select_tool(
                "Show me the Warren Buffett style persona",
                [],
                registry,
            )
            result = registry.call(decision.tool_name, decision.arguments)

        self.assertIn("list_strategy_personas", tools)
        self.assertIn("get_strategy_persona", tools)
        self.assertEqual(decision.tool_name, "get_strategy_persona")
        self.assertEqual(result["persona"]["persona_id"], "conservative_value")

    def test_public_config_exposes_provider_flags_without_secrets(self) -> None:
        visible = public_config(
            AppConfig(
                llm_provider="groq",
                groq_api_key="secret-groq",
                openai_api_key="secret-openai",
            )
        )

        self.assertEqual(visible["llm_provider"], "groq")
        self.assertTrue(visible["groq_api_key_configured"])
        self.assertEqual(
            visible["groq_fallback_model"],
            "llama-3.1-8b-instant",
        )
        self.assertTrue(visible["openai_api_key_configured"])
        self.assertNotIn("secret-groq", visible.values())
        self.assertNotIn("secret-openai", visible.values())

    def test_real_llm_required_refuses_missing_groq_key(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY"):
            build_orchestrator(
                provider="groq",
                groq_api_key=None,
                require_real_llm=True,
            )
