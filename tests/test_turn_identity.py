"""A turn must be addressable.

Before this, api/routes/query.py put the SESSION id in state["trace_id"] and
returned nothing turn-scoped, while one session_id covers every turn in both
Word tabs. Feedback captured against that would point at a dozen prompts.
"""
from types import SimpleNamespace

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from observability.spans import current_trace_id


def test_trace_id_is_empty_when_the_span_is_not_recording(monkeypatch):
    """Tracing off is a supported configuration, not an error."""
    monkeypatch.setattr(otel_trace, "get_current_span", lambda: otel_trace.INVALID_SPAN)
    assert current_trace_id() == ""


def test_trace_id_is_32_hex_inside_a_real_span():
    """The format every trace lookup in this project already uses."""
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("t"):
        tid = current_trace_id()
    assert len(tid) == 32
    assert int(tid, 16) != 0
    assert tid == tid.lower()


def test_trace_id_never_raises(monkeypatch):
    """Same contract as every other helper in spans.py — tracing never breaks a turn."""
    def _boom():
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(otel_trace, "get_current_span", _boom)
    assert current_trace_id() == ""
