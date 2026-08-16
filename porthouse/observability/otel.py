"""Optional OpenTelemetry export for API and worker processes."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

_logger = logging.getLogger(__name__)
_configured_services: set[str] = set()


def _enabled() -> bool:
    value = str(os.getenv("PORTHOUSE_OTEL_ENABLED") or "").strip().lower()
    if value:
        return value in {"1", "true", "yes", "on"}
    return bool(str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip())


def configure_telemetry(*, service_name: str, app: Any = None) -> bool:
    """Configure OTLP tracing when explicitly enabled.

    The dependency set is optional so a local installation does not silently
    gain a network exporter. A production deployment that enables telemetry
    but omits the extra dependencies fails during startup.
    """
    if not _enabled():
        return False
    if service_name in _configured_services:
        return True
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise RuntimeError(
            "OpenTelemetry is enabled but observability dependencies are missing; "
            "install porthouse[observability]"
        ) from exc

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": str(os.getenv("PORTHOUSE_VERSION") or "0.1.2"),
            "deployment.environment.name": str(
                os.getenv("PORTHOUSE_ENVIRONMENT") or "development"
            ),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    PsycopgInstrumentor().instrument()
    if app is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="/healthz,/readyz,/metrics",
        )
    _configured_services.add(service_name)
    _logger.info("OpenTelemetry enabled for %s", service_name)
    return True


def current_trace_carrier() -> dict[str, str]:
    """Return W3C trace headers for hand-off to a durable asynchronous Run."""
    if not _enabled():
        return {}
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return {}
    carrier: dict[str, str] = {}
    inject(carrier)
    return {key: value for key, value in carrier.items() if key in {"traceparent", "tracestate"}}


def _attributes(values: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (values or {}).items():
        if value is None or isinstance(value, (str, bool, int, float)):
            result[str(key)] = value
        elif isinstance(value, (list, tuple)):
            result[str(key)] = [str(item) for item in value[:100]]
        else:
            result[str(key)] = str(value)
    return result


@contextmanager
def telemetry_span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    carrier: Mapping[str, str] | None = None,
) -> Iterator[Any]:
    """Create one process span, continuing a persisted W3C parent when present."""
    if not _enabled():
        yield None
        return
    try:
        from opentelemetry import propagate, trace
    except ImportError:
        yield None
        return
    context = propagate.extract(dict(carrier or {})) if carrier else None
    tracer = trace.get_tracer("porthouse.runtime")
    with tracer.start_as_current_span(
        name,
        context=context,
        attributes=_attributes(attributes),
        record_exception=True,
        set_status_on_exception=True,
    ) as span:
        yield span
