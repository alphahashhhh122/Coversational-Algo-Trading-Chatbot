from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import (
    ParentBased,
    TraceIdRatioBased,
)
from opentelemetry.trace import Span, Tracer

from .config import AppConfig


@dataclass(frozen=True)
class TraceContext:
    trace_id: str | None
    span_id: str | None


@dataclass(frozen=True)
class TelemetryRuntime:
    provider: TracerProvider
    tracer: Tracer
    exporter: SpanExporter


_tracer: Tracer = trace.get_tracer("iimc_trading_platform")


def configure_telemetry(
    config: AppConfig,
    app: FastAPI,
    *,
    span_exporter: SpanExporter | None = None,
) -> TelemetryRuntime | None:
    """Configure one application's HTTP and service tracing."""
    global _tracer

    if not config.otel_enabled:
        return None
    if span_exporter is None and not config.otel_exporter_otlp_endpoint:
        raise RuntimeError(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is required when "
            "IIMC_OTEL_ENABLED=true"
        )

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": config.otel_service_name,
                "service.version": "0.2.0",
                "deployment.environment.name": config.environment,
            }
        ),
        sampler=ParentBased(
            TraceIdRatioBased(config.otel_sample_ratio)
        ),
    )
    exporter = span_exporter or OTLPSpanExporter(
        endpoint=config.otel_exporter_otlp_endpoint
    )
    processor = (
        SimpleSpanProcessor(exporter)
        if span_exporter is not None
        else BatchSpanProcessor(exporter)
    )
    provider.add_span_processor(processor)
    _tracer = provider.get_tracer(
        "iimc_trading_platform",
        "0.2.0",
    )
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=config.otel_excluded_urls,
    )
    return TelemetryRuntime(
        provider=provider,
        tracer=_tracer,
        exporter=exporter,
    )


def start_span(
    name: str,
    attributes: dict[str, Any] | None = None,
):
    clean_attributes = {
        key: value
        for key, value in (attributes or {}).items()
        if value is not None
    }
    return _tracer.start_as_current_span(
        name,
        attributes=clean_attributes,
    )


def current_trace_context(span: Span | None = None) -> TraceContext:
    active_span = span or trace.get_current_span()
    context = active_span.get_span_context()
    if not context.is_valid:
        return TraceContext(trace_id=None, span_id=None)
    return TraceContext(
        trace_id=f"{context.trace_id:032x}",
        span_id=f"{context.span_id:016x}",
    )
