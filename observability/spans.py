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
from opentelemetry.trace import Span, Status, StatusCode
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes

from observability.degradations import OUTCOME_FAILED

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
_root_degradations: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "otel_root_degradations", default=None
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
                deg_token = None
                if _root_span.get() is None:
                    root_token = _root_span.set(span)
                    meta_token = _root_metadata.set({})
                    deg_token = _root_degradations.set([])
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
                    if deg_token is not None:
                        _root_degradations.reset(deg_token)
        # Exposed for tests to assert WHICH name a function was decorated with —
        # functools.wraps alone only proves "wrapped by something", not "wrapped
        # with this span name". Nothing in production reads this; don't delete it
        # as unused.
        wrapper.span_name = name
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
            if isinstance(tags, str):
                tags = [tags]
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
    timings: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Record GENERATION attributes on the CURRENT span. Best-effort; never raises.

    `usage` is the {input, output, total, unit} dict from observability.tracing.
    `timings` is the {total_ms, load_ms, prompt_eval_ms, eval_ms} dict from
    observability.tracing.ollama_timings.
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
        if timings:
            for key, value in timings.items():
                span.set_attribute(f"llm.ollama.{key}", int(value))
        if metadata:
            span.set_attribute(
                SpanAttributes.METADATA, json.dumps(metadata, default=str, ensure_ascii=False)
            )
    except Exception:
        pass


def current_trace_id() -> str:
    """32-hex trace id for the active span, or "" when tracing is off.

    Best-effort like every helper here. A feedback item is joined to its turn by
    `turn_id`, which is minted independently of tracing — the trace id only
    speeds up the lookup, so it must never be the thing that breaks a turn.
    """
    try:
        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return ""
        return format(ctx.trace_id, "032x")
    except Exception:
        return ""


def _accumulate(reason: str) -> None:
    """Append to the root's reason list, de-duplicated.

    Runs BEFORE any is_recording() guard on purpose: the accumulator is set by
    `traced` whether or not a provider exists, so outcome derivation behaves
    identically with tracing on and off. A turn's verdict must not depend on
    whether anyone was watching."""
    acc = _root_degradations.get()
    if acc is not None and reason not in acc:
        acc.append(reason)


def record_degradation(reason: str, *, announced: bool, detail: str = "") -> None:
    """Record a degradation as an EVENT on the current span. Never raises.

    Does NOT touch span status — a fallback that worked is not an error.

    `announced` has no default on purpose: whether the attorney was told is
    exactly the thing you must not get wrong by accident. True mirrors the
    existing `memory_degraded` convention (a banner reaches the pane); False is
    the silent class this instrumentation exists to expose.

    An EVENT rather than an attribute because one span can degrade twice —
    attributes overwrite, events keep both, in order, with timestamps.
    """
    try:
        _accumulate(reason)
        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return
        attributes: dict[str, Any] = {
            "degradation.reason": reason,
            "degradation.announced": announced,
        }
        if detail:
            attributes["degradation.detail"] = detail
        span.add_event("degradation", attributes=attributes)
    except Exception:
        pass


def degradations() -> list[str]:
    """Reason codes accumulated on this request, for the rollup to read back."""
    return list(_root_degradations.get() or [])


def mark_failed(reason: str, *, exc: BaseException | None = None, detail: str = "") -> None:
    """Set the CURRENT span to ERROR and record `exc`. Never raises.

    For failures this app CATCHES and converts into a degraded answer. OTel
    marks a span ERROR only when an exception propagates out of the `with`
    block; there are 40 `except Exception` sites here and almost none of them
    propagate, so without this call every failed turn looks like a healthy one.

    `reason` (and `detail`, when given) are also stamped as attributes —
    `degradation.reason` / `degradation.detail`, the same keys record_degradation
    puts on its event — so a failed span names WHICH of the eight failure
    reasons it was, not just that it failed. Without this a failed span carried
    only the exception class, and reason was unreadable from the span itself.
    """
    try:
        _accumulate(reason)
        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return
        span.set_attribute("degradation.reason", reason)
        if detail:
            span.set_attribute("degradation.detail", detail)
        if exc is not None:
            span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, detail or reason))
    except Exception:
        pass


def set_outcome(outcome: str) -> None:
    """Stamp app.outcome on the ROOT span. Never raises.

    Called once per request root — submit_query, resume_query, post_compact.
    One derived field so "show me the bad turns" is `app.outcome != "ok"`
    rather than an OR-chain across attributes you have to remember.

    Must be called INSIDE the root traced function — outside it, the
    accumulator is already reset, so this silently stamps a fresh, empty
    reason list rather than the request's real one.

    When outcome == "failed" the root status is set to ERROR as well, which is
    the point of the whole exercise: afterwards, ERROR means exactly "the
    attorney did not get their answer".
    """
    try:
        span = _root_span.get() or trace.get_current_span()
        if span is None or not span.is_recording():
            return
        span.set_attribute("app.outcome", outcome)
        reasons = degradations()
        if reasons:
            span.set_attribute("app.degradations", json.dumps(reasons, ensure_ascii=False))
        if outcome == OUTCOME_FAILED:
            span.set_status(Status(StatusCode.ERROR, ", ".join(reasons) or OUTCOME_FAILED))
    except Exception:
        pass
