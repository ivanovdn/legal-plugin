# observability/spans.py
"""OpenTelemetry span helpers — the single instrumentation seam.

Replaces the Langfuse SDK: `@traced` replaces `@observe`; `set_trace_attributes`
replaces `langfuse_context.update_current_trace`; `set_gen_attributes` replaces
`langfuse_context.update_current_observation`. Attributes follow the OpenInference
semantic conventions so both Langfuse v3 (OTLP) and Phoenix render them natively.

Tracing must never break a turn: with no TracerProvider configured OTel hands out
non-recording spans and every helper is a cheap no-op. Helpers guard non-recording
spans and never raise; `@traced` never swallows the wrapped function's exceptions.
"""
from __future__ import annotations

import contextvars
import functools
import json
from typing import Any, Callable

from opentelemetry import trace
from opentelemetry.trace import Span
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes

_tracer = trace.get_tracer("legal-triage")

# The outermost @traced span of a request is the "trace root". Deep nodes call
# set_trace_attributes() to stamp trace-wide facts (user/session/tags/metadata)
# onto this root — matching Langfuse's update_current_trace semantics. Safe because
# the graph runs synchronously in the request thread (one root per request context).
_root_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "otel_root_span", default=None
)
_root_metadata: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "otel_root_metadata", default=None
)


def _as_attr(value: Any) -> Any:
    """Coerce to an OTel-attribute-safe type; JSON-encode anything else."""
    if isinstance(value, (str, bool, int, float)):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


def traced(name: str, kind: str | None = None) -> Callable:
    """Run the wrapped (sync) function inside a span named `name`.

    kind="LLM" marks it as an OpenInference LLM (GENERATION) span. The outermost
    traced span registers itself as the trace root for set_trace_attributes().
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _tracer.start_as_current_span(name) as span:
                root_token = None
                meta_token = None
                if _root_span.get() is None:
                    root_token = _root_span.set(span)
                    meta_token = _root_metadata.set({})
                if kind == "LLM":
                    span.set_attribute(
                        SpanAttributes.OPENINFERENCE_SPAN_KIND,
                        OpenInferenceSpanKindValues.LLM.value,
                    )
                try:
                    return fn(*args, **kwargs)
                finally:
                    if root_token is not None:
                        _root_span.reset(root_token)
                    if meta_token is not None:
                        _root_metadata.reset(meta_token)
        return wrapper
    return decorator


def set_trace_attributes(
    *,
    name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    input: Any = None,
    tags: Any = None,
    metadata: dict | None = None,
) -> None:
    """Stamp trace-wide facts onto the root span. Best-effort; never raises."""
    try:
        span = _root_span.get() or trace.get_current_span()
        if span is None or not span.is_recording():
            return
        if name is not None:
            span.update_name(name)
        if user_id is not None:
            span.set_attribute(SpanAttributes.USER_ID, user_id)
        if session_id is not None:
            span.set_attribute(SpanAttributes.SESSION_ID, session_id)
        if input is not None:
            span.set_attribute(SpanAttributes.INPUT_VALUE, _as_attr(input))
        if tags is not None:
            span.set_attribute(
                SpanAttributes.TAG_TAGS, json.dumps(list(tags), ensure_ascii=False)
            )
        if metadata:
            acc = _root_metadata.get()
            if acc is None:                       # called outside a traced root
                acc = dict(metadata)
            else:
                acc.update(metadata)              # accumulate across node calls
            span.set_attribute(
                SpanAttributes.METADATA, json.dumps(acc, default=str, ensure_ascii=False)
            )
    except Exception:
        pass


def set_gen_attributes(
    *,
    name: str | None = None,
    input: Any = None,
    output: Any = None,
    model: str | None = None,
    usage: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Record GENERATION attributes on the CURRENT span. Best-effort; never raises.

    `usage` is the {input, output, total, unit} dict from observability.tracing.
    """
    try:
        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return
        if name is not None:
            span.update_name(name)
        if model is not None:
            span.set_attribute(SpanAttributes.LLM_MODEL_NAME, model)
        if input is not None:
            span.set_attribute(SpanAttributes.INPUT_VALUE, _as_attr(input))
        if output is not None:
            span.set_attribute(SpanAttributes.OUTPUT_VALUE, _as_attr(output))
        if usage:
            if usage.get("input") is not None:
                span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_PROMPT, int(usage["input"]))
            if usage.get("output") is not None:
                span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION, int(usage["output"]))
            if usage.get("total") is not None:
                span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_TOTAL, int(usage["total"]))
        if metadata:
            span.set_attribute(
                SpanAttributes.METADATA, json.dumps(metadata, default=str, ensure_ascii=False)
            )
    except Exception:
        pass
