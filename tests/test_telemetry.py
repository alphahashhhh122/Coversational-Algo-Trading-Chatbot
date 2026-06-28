from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from iimc_trading_platform.config import AppConfig
from iimc_trading_platform.infrastructure import (
    DuckDBAuditRepository,
    DuckDBToolCallRepository,
    initialize_database,
)
from iimc_trading_platform.middleware import RequestContextMiddleware
from iimc_trading_platform.services import AuditService, ToolExecutionService
from iimc_trading_platform.telemetry import configure_telemetry


class TelemetryTest(unittest.TestCase):
    def test_trace_context_links_http_tool_and_audit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "telemetry.duckdb"
            initialize_database(db_path)
            tool_repository = DuckDBToolCallRepository(db_path)
            audit_service = AuditService(DuckDBAuditRepository(db_path))
            tool_service = ToolExecutionService(
                tool_repository,
                audit_service,
            )
            exporter = InMemorySpanExporter()
            app = FastAPI()
            app.add_middleware(RequestContextMiddleware)

            @app.get("/trace-test")
            def trace_test() -> dict[str, object]:
                tool_call_id, result = tool_service.execute(
                    tool_name="trace_test_tool",
                    request={"value": 7},
                    handler=lambda: {"value": 7},
                    session_id="session_trace",
                )
                return {
                    "tool_call_id": tool_call_id,
                    "result": result,
                }

            runtime = configure_telemetry(
                AppConfig(
                    database_path=db_path,
                    otel_enabled=True,
                    otel_sample_ratio=1.0,
                ),
                app,
                span_exporter=exporter,
            )
            self.assertIsNotNone(runtime)
            try:
                response = TestClient(app).get("/trace-test")
                self.assertEqual(response.status_code, 200)
                tool_call_id = response.json()["tool_call_id"]
                stored = tool_repository.get(tool_call_id)
                self.assertIsNotNone(stored)
                self.assertEqual(len(stored.trace_id), 32)
                self.assertEqual(len(stored.span_id), 16)
                self.assertEqual(
                    response.headers["x-trace-id"],
                    stored.trace_id,
                )

                history = audit_service.history(
                    "tool_call",
                    tool_call_id,
                )
                self.assertEqual(
                    {event.payload["trace_id"] for event in history},
                    {stored.trace_id},
                )
                runtime.provider.force_flush()
                spans = exporter.get_finished_spans()
                self.assertIn(
                    "tool.execute",
                    {span.name for span in spans},
                )
                tool_span = next(
                    span
                    for span in spans
                    if span.name == "tool.execute"
                )
                self.assertEqual(
                    f"{tool_span.context.trace_id:032x}",
                    stored.trace_id,
                )
                self.assertEqual(
                    tool_span.attributes["tool.name"],
                    "trace_test_tool",
                )
            finally:
                FastAPIInstrumentor.uninstrument_app(app)
                runtime.provider.shutdown()

    def test_enabled_telemetry_requires_an_export_destination(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        ):
            configure_telemetry(
                AppConfig(otel_enabled=True),
                FastAPI(),
            )

    def test_sampling_ratio_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            AppConfig(otel_sample_ratio=1.1)


if __name__ == "__main__":
    unittest.main()
