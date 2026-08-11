# observability/otel.py
"""OpenTelemetry tracer bootstrap. Replaces observability/langfuse.py.

init_observability() wires a global TracerProvider + OTLP/HTTP span exporter from
config. Best-effort: disabled or misconfigured → no provider is set, OTel's default
no-op tracer takes over, and instrumentation elsewhere becomes a transparent
pass-through (a turn is never broken by tracing).
"""
import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from config import get_settings

logger = logging.getLogger(__name__)

_initialized = False


def _normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if not endpoint.endswith("/v1/traces"):
        endpoint = endpoint + "/v1/traces"
    return endpoint


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        headers[key.strip()] = value.strip()
    return headers


def init_observability() -> None:
    """Configure the global OTel TracerProvider. Call once at startup."""
    global _initialized
    if _initialized:
        return

    settings = get_settings()
    if not settings.tracing_enabled or not settings.otel_exporter_otlp_endpoint:
        logger.info("Tracing disabled (tracing_enabled off or no OTEL endpoint) — spans are no-ops")
        return

    try:
        endpoint = _normalize_endpoint(settings.otel_exporter_otlp_endpoint)
        headers = _parse_headers(settings.otel_exporter_otlp_headers)
        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers or None))
        )
        trace.set_tracer_provider(provider)
        _initialized = True
        logger.info("OTel tracing initialized → %s", endpoint)
    except Exception as e:  # best-effort: tracing must never break startup
        logger.warning("OTel init failed: %s — tracing disabled", e)


def is_enabled() -> bool:
    return _initialized
