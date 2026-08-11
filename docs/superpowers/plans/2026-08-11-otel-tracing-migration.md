# OTel Tracing Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Langfuse-SDK instrumentation with vendor-neutral OpenTelemetry + OpenInference so the trace backend is a pure env choice (local Langfuse v3 via OTLP / dedicated Phoenix on the VM), reproducing today's span tree 1:1.

**Architecture:** A single instrumentation seam in `observability/spans.py` (`@traced` decorator + `set_trace_attributes` / `set_gen_attributes` helpers) replaces `@observe` / `langfuse_context`. `observability/otel.py` boots a `TracerProvider` + OTLP/HTTP exporter from config; the outermost `@traced` span is registered as the "trace root" via a contextvar so node-level trace metadata lands on it. Attributes follow OpenInference semantic conventions, which both Langfuse v3 and Phoenix render natively.

**Tech Stack:** Python 3.12, uv, FastAPI, LangGraph; `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `openinference-semantic-conventions`; pytest with `InMemorySpanExporter`.

## Global Constraints

- **Python 3.12**, dependencies via **uv** (`uv pip install -r requirements.txt`, `uv run pytest`).
- **All imports at top of file** — no lazy imports inside functions (hard rule #1). In `otel.py` the OTel SDK imports go at module top; only the *runtime provider construction* is wrapped in try/except.
- **No backwards-compat shims** — delete the old code and change call sites (hard rule #5). No `langfuse_*` aliases survive.
- **Tracing must never break a turn** — disabled or misconfigured ⇒ spans no-op, helpers no-op, real work returns normally. This is the top guardrail; every task preserves it.
- **`uv run pytest` requires Docker** — `tests/conftest.py` spins an ephemeral Postgres via testcontainers. Unaffected by this work, but the suite won't run without Docker up.
- **OpenInference attribute conventions (exact strings):** `openinference.span.kind` (`"LLM"` for generations), `llm.model_name`, `llm.token_count.prompt` / `.completion` / `.total`, `input.value`, `output.value`, `session.id`, `user.id`, `tag.tags` (JSON-encoded list), `metadata` (single JSON-encoded object — **not** per-key attributes).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Branch:** work on the current `feat/per-user-identity` branch (or a fresh `feat/otel-tracing` branch if starting clean); do not push unless asked.

---

## File Structure

**Created:**
- `observability/otel.py` — tracer bootstrap (`init_observability`, `is_enabled`). Replaces `observability/langfuse.py`.
- `observability/spans.py` — the instrumentation seam: `@traced`, `set_trace_attributes`, `set_gen_attributes`, root-span contextvars.

**Modified (real logic):**
- `observability/tracing.py` — keep pure token mappers; rewrite the two GENERATION wrappers onto `spans.py`; drop the `langfuse.model.ModelUsage` import (→ local `TokenUsage` TypedDict).

**Modified (mechanical sweep — 17 files, ~18 decorator sites):**
- `graph/nodes/{intake,intent_router,history_appender,planner,human_review,rag_retriever,output_formatter,llm_caller,skill_dispatcher,memory_writer,risk_assessor}.py`
- `api/routes/query.py` (2 decorators: `query`, `resume`)
- `skills/{compliance_check,legal_research,drafting}.py`, `skills/contract_generation/contract_generation.py`, `skills/contract_review/contract_review.py`

**Modified (wiring / config / deps / tests / infra / docs):**
- `api/main.py` (startup wiring), `config.py`, `requirements.txt`, `requirements-runtime.txt`
- `tests/conftest.py`, `tests/test_observability.py`, `tests/test_config.py`
- `docker-compose.yml`, `docker-compose.remote.yml`, `.env.example`, `.env.remote.example`
- `README.md`, `CLAUDE.md`, `docs/deploy-vm.md`

**Deleted:**
- `observability/langfuse.py`

---

## Task 1: Dependencies + OTel config keys (additive)

Add the new deps and config keys without removing anything yet — the app keeps booting on Langfuse until the cutover in Task 5.

**Files:**
- Modify: `requirements.txt`, `requirements-runtime.txt`
- Modify: `config.py:78-83` (the `# Langfuse` / `# Phoenix` block)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `settings.otel_exporter_otlp_endpoint: str`, `settings.otel_exporter_otlp_headers: str`, `settings.otel_service_name: str`, `settings.tracing_enabled: bool`.

- [ ] **Step 1: Add the failing config test**

Add to `tests/test_config.py`:

```python
def test_otel_settings_defaults():
    from config import Settings
    s = Settings()
    assert s.otel_exporter_otlp_endpoint == "http://localhost:3000/api/public/otel"
    assert s.otel_service_name == "legal-triage"
    assert s.tracing_enabled is True
    assert s.otel_exporter_otlp_headers == ""
```

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/test_config.py::test_otel_settings_defaults -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'otel_exporter_otlp_endpoint'`.

- [ ] **Step 3: Add the config keys**

In `config.py`, immediately after the existing `# Phoenix` block (keep `langfuse_*` and `phoenix_host` for now — they're removed in Task 6):

```python
    # OpenTelemetry tracing (backend chosen by endpoint: local Langfuse v3 OTLP / VM Phoenix)
    otel_exporter_otlp_endpoint: str = "http://localhost:3000/api/public/otel"
    otel_exporter_otlp_headers: str = ""   # "key=value,key2=value2"; local Langfuse needs Authorization=Basic <b64 public:secret>
    otel_service_name: str = "legal-triage"
    tracing_enabled: bool = True
```

- [ ] **Step 4: Add the OTel deps**

In **both** `requirements.txt` and `requirements-runtime.txt`, next to the existing `langfuse>=2.0,<3.0` line (leave that line for now), add:

```
opentelemetry-sdk>=1.27
opentelemetry-exporter-otlp-proto-http>=1.27
openinference-semantic-conventions>=0.1.9
```

- [ ] **Step 5: Install**

Run: `uv pip install -r requirements.txt`
Expected: the three packages install (plus `opentelemetry-api` as a transitive dep).

- [ ] **Step 6: Run the config test, verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt requirements-runtime.txt config.py tests/test_config.py
git commit -m "$(printf 'feat(otel): add OTel deps + config keys (additive)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: `observability/spans.py` — the instrumentation seam

The heart of the migration. `@traced` replaces `@observe`; `set_trace_attributes` / `set_gen_attributes` replace `langfuse_context.*`. Tests assert against an in-memory span exporter.

**Files:**
- Create: `observability/spans.py`
- Modify: `tests/conftest.py` (force tracing off for the suite)
- Test: `tests/test_observability.py` (add the exporter fixture + spans tests)

**Interfaces:**
- Produces:
  - `traced(name: str, kind: str | None = None)` — decorator for sync functions; `kind="LLM"` marks a generation span; the outermost traced span becomes the trace root.
  - `set_trace_attributes(*, name=None, user_id=None, session_id=None, input=None, tags=None, metadata=None) -> None` — stamps root-span trace attrs; best-effort, never raises.
  - `set_gen_attributes(*, name=None, input=None, output=None, model=None, usage=None, metadata=None) -> None` — stamps current-span generation attrs; best-effort, never raises. `usage` is the `{input, output, total, unit}` dict from `observability.tracing`.

- [ ] **Step 1: Force tracing OFF for the test suite**

`set_tracer_provider` is set-once per process. Tests install their own in-memory provider, so the app's real provider must never win the race. At the **very top** of `tests/conftest.py` (before any `import config` / app import), add:

```python
import os
os.environ.setdefault("TRACING_ENABLED", "false")
```

If `tests/conftest.py` already imports config/app modules at top, place these two lines above those imports.

- [ ] **Step 2: Add the in-memory exporter fixture + first failing test**

Rewrite the top of `tests/test_observability.py` to add the fixtures and the first `spans` test (keep the existing pure-function tests below — they're untouched until Task 4):

```python
import json
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _otel_test_provider():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(provider)   # first real set wins (app tracing is off in tests)
    yield


@pytest.fixture(autouse=True)
def _clear_spans():
    _exporter.clear()
    yield
    _exporter.clear()


def _spans_by_name(name):
    return [s for s in _exporter.get_finished_spans() if s.name == name]


def test_traced_creates_span_and_returns_value():
    from observability.spans import traced

    @traced("unit_node")
    def node(x):
        return x + 1

    assert node(41) == 42
    spans = _spans_by_name("unit_node")
    assert len(spans) == 1


def test_traced_llm_kind_sets_openinference_span_kind():
    from observability.spans import traced

    @traced("gen_node", kind="LLM")
    def gen():
        return "ok"

    gen()
    span = _spans_by_name("gen_node")[0]
    assert span.attributes.get("openinference.span.kind") == "LLM"


def test_set_trace_attributes_lands_on_root_from_nested_span():
    from observability.spans import traced, set_trace_attributes

    @traced("child")
    def child():
        # a deep node stamps trace-wide facts; they must hit the ROOT span
        set_trace_attributes(user_id="u1", session_id="s1", tags=["research"])

    @traced("root")
    def root():
        child()

    root()
    root_span = _spans_by_name("root")[0]
    child_span = _spans_by_name("child")[0]
    assert root_span.attributes.get("user.id") == "u1"
    assert root_span.attributes.get("session.id") == "s1"
    assert json.loads(root_span.attributes.get("tag.tags")) == ["research"]
    assert "user.id" not in child_span.attributes


def test_set_trace_attributes_merges_metadata_on_root():
    from observability.spans import traced, set_trace_attributes

    @traced("root")
    def root():
        set_trace_attributes(metadata={"contract_type_detected": "nda"})
        set_trace_attributes(metadata={"review_risk_level": "red"})

    root()
    root_span = _spans_by_name("root")[0]
    md = json.loads(root_span.attributes.get("metadata"))
    assert md == {"contract_type_detected": "nda", "review_risk_level": "red"}


def test_set_gen_attributes_sets_llm_attrs_on_current_span():
    from observability.spans import traced, set_gen_attributes

    @traced("gen", kind="LLM")
    def gen():
        set_gen_attributes(
            name="doc_chat",
            input=[{"role": "user", "content": "q"}],
            output="the answer",
            model="qwen3.6:latest",
            usage={"input": 90, "output": 10, "total": 100, "unit": "TOKENS"},
        )

    gen()
    # name override renames the span
    span = _spans_by_name("doc_chat")[0]
    assert span.attributes.get("llm.model_name") == "qwen3.6:latest"
    assert span.attributes.get("llm.token_count.prompt") == 90
    assert span.attributes.get("llm.token_count.completion") == 10
    assert span.attributes.get("llm.token_count.total") == 100
    assert span.attributes.get("output.value") == "the answer"
    assert json.loads(span.attributes.get("input.value")) == [{"role": "user", "content": "q"}]


def test_helpers_no_op_and_never_raise_without_active_span():
    from observability.spans import set_trace_attributes, set_gen_attributes
    # called outside any @traced span → current span is invalid/non-recording
    set_trace_attributes(user_id="u", metadata={"k": "v"})
    set_gen_attributes(model="m", usage={"input": 1, "output": 2, "total": 3, "unit": "TOKENS"})
    # no exception == pass


def test_traced_is_noop_under_noop_tracer(monkeypatch):
    import observability.spans as mod
    from opentelemetry.trace import NoOpTracer

    monkeypatch.setattr(mod, "_tracer", NoOpTracer())

    @mod.traced("dark", kind="LLM")
    def fn():
        mod.set_gen_attributes(model="m")
        return "value"

    assert fn() == "value"                 # returns normally
    assert _spans_by_name("dark") == []    # nothing exported
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `uv run pytest tests/test_observability.py -v -k "traced or set_trace or set_gen or helpers"`
Expected: FAIL — `ModuleNotFoundError: No module named 'observability.spans'`.

- [ ] **Step 4: Implement `observability/spans.py`**

```python
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
```

- [ ] **Step 5: Verify the OpenInference constant names resolve**

The plan assumes these `SpanAttributes` constants exist. Confirm before relying on them:

Run:
```bash
uv run python -c "from openinference.semconv.trace import SpanAttributes as S, OpenInferenceSpanKindValues as K; print(S.OPENINFERENCE_SPAN_KIND, S.LLM_MODEL_NAME, S.LLM_TOKEN_COUNT_PROMPT, S.LLM_TOKEN_COUNT_COMPLETION, S.LLM_TOKEN_COUNT_TOTAL, S.INPUT_VALUE, S.OUTPUT_VALUE, S.SESSION_ID, S.USER_ID, S.METADATA, S.TAG_TAGS, K.LLM.value)"
```
Expected: `openinference.span.kind llm.model_name llm.token_count.prompt llm.token_count.completion llm.token_count.total input.value output.value session.id user.id metadata tag.tags LLM`
If any constant raises `AttributeError`, run `uv run python -c "from openinference.semconv.trace import SpanAttributes; print([a for a in dir(SpanAttributes) if not a.startswith('_')])"` and map to the correct name.

- [ ] **Step 6: Run the spans tests, verify they pass**

Run: `uv run pytest tests/test_observability.py -v -k "traced or set_trace or set_gen or helpers or noop"`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add observability/spans.py tests/test_observability.py tests/conftest.py
git commit -m "$(printf 'feat(otel): span seam (@traced + trace/gen attribute helpers)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: `observability/otel.py` — tracer bootstrap

Boots the global TracerProvider + OTLP exporter from config. Best-effort. Tests verify the enable/disable decision without mutating the global provider.

**Files:**
- Create: `observability/otel.py`
- Test: `tests/test_otel_init.py`

**Interfaces:**
- Consumes: `settings.tracing_enabled`, `settings.otel_exporter_otlp_endpoint`, `settings.otel_exporter_otlp_headers`, `settings.otel_service_name`.
- Produces: `init_observability() -> None`, `is_enabled() -> bool`, `_normalize_endpoint(str) -> str`, `_parse_headers(str) -> dict[str, str]`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_otel_init.py`:

```python
import observability.otel as otel


def test_normalize_endpoint_appends_v1_traces():
    assert otel._normalize_endpoint("http://langfuse-web:3000/api/public/otel") == \
        "http://langfuse-web:3000/api/public/otel/v1/traces"
    assert otel._normalize_endpoint("http://phoenix:6006") == "http://phoenix:6006/v1/traces"
    assert otel._normalize_endpoint("http://phoenix:6006/v1/traces/") == "http://phoenix:6006/v1/traces"


def test_parse_headers():
    assert otel._parse_headers("Authorization=Basic abc123") == {"Authorization": "Basic abc123"}
    assert otel._parse_headers("a=1,b=2") == {"a": "1", "b": "2"}
    assert otel._parse_headers("") == {}


def test_init_disabled_sets_no_provider(monkeypatch):
    monkeypatch.setattr(otel, "_initialized", False)
    from config import get_settings
    monkeypatch.setenv("TRACING_ENABLED", "false")
    get_settings.cache_clear()
    called = {}
    monkeypatch.setattr(otel.trace, "set_tracer_provider", lambda p: called.setdefault("set", p))

    otel.init_observability()

    assert "set" not in called
    assert otel.is_enabled() is False
    get_settings.cache_clear()


def test_init_enabled_sets_provider(monkeypatch):
    monkeypatch.setattr(otel, "_initialized", False)
    from config import get_settings
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://phoenix:6006")
    get_settings.cache_clear()
    called = {}
    monkeypatch.setattr(otel.trace, "set_tracer_provider", lambda p: called.setdefault("set", p))

    otel.init_observability()

    assert "set" in called
    assert otel.is_enabled() is True
    get_settings.cache_clear()


def test_init_is_best_effort_on_failure(monkeypatch):
    monkeypatch.setattr(otel, "_initialized", False)
    from config import get_settings
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://phoenix:6006")
    get_settings.cache_clear()

    def boom(*a, **k):
        raise RuntimeError("exporter blew up")
    monkeypatch.setattr(otel, "OTLPSpanExporter", boom)

    otel.init_observability()   # must NOT raise
    assert otel.is_enabled() is False
    get_settings.cache_clear()
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_otel_init.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'observability.otel'`.

- [ ] **Step 3: Implement `observability/otel.py`**

Imports at top (hard rule #1); try/except wraps only the runtime provider construction:

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_otel_init.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add observability/otel.py tests/test_otel_init.py
git commit -m "$(printf 'feat(otel): TracerProvider + OTLP exporter bootstrap (best-effort)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: Rewrite `observability/tracing.py` onto the seam

Keep the pure token/model mappers byte-for-byte; drop the `langfuse.model.ModelUsage` import; rewrite the two GENERATION wrappers to use `@traced` + `set_gen_attributes`.

**Files:**
- Modify: `observability/tracing.py`
- Test: `tests/test_observability.py` (rewrite only `test_traced_invoke_records_generation_and_returns_response`; keep the `ollama_usage` / `message_usage` tests unchanged)

**Interfaces:**
- Consumes: `observability.spans.traced`, `observability.spans.set_gen_attributes`.
- Produces (unchanged signatures): `ollama_usage(dict) -> TokenUsage | None`, `message_usage(Any) -> TokenUsage | None`, `traced_invoke(llm, messages, *, name="llm") -> Any`, `traced_agent_invoke(agent, payload, *, name="agent") -> Any`. New: `TokenUsage` TypedDict `{input, output, total, unit}`.

- [ ] **Step 1: Rewrite the traced_invoke test to assert exported span attrs**

In `tests/test_observability.py`, replace the body of `test_traced_invoke_records_generation_and_returns_response` (it currently monkeypatches `mod.langfuse_context`):

```python
def test_traced_invoke_records_generation_and_returns_response():
    from observability.tracing import traced_invoke

    class _FakeMessage:
        def __init__(self, content, usage_metadata, response_metadata):
            self.content = content
            self.usage_metadata = usage_metadata
            self.response_metadata = response_metadata

    resp = _FakeMessage(
        content="the answer",
        usage_metadata={"input_tokens": 90, "output_tokens": 10, "total_tokens": 100},
        response_metadata={"model": "qwen3.6:latest"},
    )

    class FakeLLM:
        def invoke(self, messages):
            return resp

    out = traced_invoke(FakeLLM(), [{"role": "user", "content": "q"}], name="doc_chat")

    assert out is resp                                   # response passed through
    span = _spans_by_name("doc_chat")[0]
    assert span.attributes.get("openinference.span.kind") == "LLM"
    assert span.attributes.get("llm.token_count.prompt") == 90
    assert span.attributes.get("llm.token_count.completion") == 10
    assert span.attributes.get("llm.token_count.total") == 100
    assert span.attributes.get("llm.model_name") == "qwen3.6:latest"
    assert span.attributes.get("output.value") == "the answer"
```

(Delete the module-scoped `_FakeMessage` class at the old location only if it becomes unused; the `message_usage` tests below still reference their own local `_FakeMessage` — leave those intact.)

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_observability.py::test_traced_invoke_records_generation_and_returns_response -v`
Expected: FAIL — the current `tracing.py` still imports langfuse and calls `langfuse_context`, so no OTel span named `doc_chat` is exported.

- [ ] **Step 3: Rewrite `observability/tracing.py`**

Keep `ollama_usage`, `message_usage`, `_message_model` bodies exactly as they are today; only change the imports/return type and the two wrappers:

```python
# observability/tracing.py
"""Helpers for GENERATION spans + token usage on LLM calls.

Two call styles exist, neither using LangChain's callback integration (that
imports the full ``langchain`` package, not a dependency here). So we instrument
manually:
- Raw httpx POSTs to Ollama (graph nodes) → ``ollama_usage`` maps the response.
- LangChain ``.invoke()`` (skills) → ``traced_invoke`` / ``traced_agent_invoke``
  run the call inside an LLM span and record model + token usage.

Tracing must never break the real call: with no provider the span is a no-op and
``set_gen_attributes`` is a no-op, so every helper returns the underlying result.
"""
from __future__ import annotations

from typing import Any, TypedDict

from observability.spans import set_gen_attributes, traced


class TokenUsage(TypedDict):
    input: int | None
    output: int | None
    total: int | None
    unit: str


def ollama_usage(response_json: dict[str, Any]) -> TokenUsage | None:
    """Map a non-streaming Ollama /api/chat response to token usage."""
    pin = response_json.get("prompt_eval_count")
    pout = response_json.get("eval_count")
    if pin is None and pout is None:
        return None
    return {
        "input": pin,
        "output": pout,
        "total": (pin or 0) + (pout or 0),
        "unit": "TOKENS",
    }


def message_usage(message: Any) -> TokenUsage | None:
    """Token usage from a LangChain AIMessage (usage_metadata, else response_metadata)."""
    um = getattr(message, "usage_metadata", None)
    if isinstance(um, dict):
        pin = um.get("input_tokens")
        pout = um.get("output_tokens")
        total = um.get("total_tokens")
    else:
        rm = getattr(message, "response_metadata", None)
        rm = rm if isinstance(rm, dict) else {}
        pin = rm.get("prompt_eval_count")
        pout = rm.get("eval_count")
        total = None
    if pin is None and pout is None:
        return None
    return {
        "input": pin,
        "output": pout,
        "total": total if total is not None else (pin or 0) + (pout or 0),
        "unit": "TOKENS",
    }


def _message_model(message: Any) -> str | None:
    rm = getattr(message, "response_metadata", None)
    return rm.get("model") if isinstance(rm, dict) else None


@traced("llm", kind="LLM")
def traced_invoke(llm: Any, messages: Any, *, name: str = "llm") -> Any:
    """Invoke a LangChain chat model as an LLM span (model + token usage)."""
    response = llm.invoke(messages)
    set_gen_attributes(
        name=name,
        input=messages,
        output=getattr(response, "content", None) or str(response),
        model=_message_model(response),
        usage=message_usage(response),
    )
    return response


@traced("agent", kind="LLM")
def traced_agent_invoke(agent: Any, payload: Any, *, name: str = "agent") -> Any:
    """Invoke a LangGraph/LangChain agent, summarizing the run as one LLM span."""
    result = agent.invoke(payload)
    messages = result.get("messages", []) if isinstance(result, dict) else []
    final = messages[-1] if messages else None

    tin = tout = 0
    have_usage = False
    for m in messages:
        u = message_usage(m)
        if u:
            have_usage = True
            tin += u["input"] or 0
            tout += u["output"] or 0
    usage: TokenUsage | None = (
        {"input": tin, "output": tout, "total": tin + tout, "unit": "TOKENS"}
        if have_usage
        else None
    )

    set_gen_attributes(
        name=name,
        input=payload,
        output=(getattr(final, "content", None) or str(final)) if final else None,
        model=_message_model(final) if final is not None else None,
        usage=usage,
    )
    return result
```

- [ ] **Step 4: Run the tracing tests, verify pass**

Run: `uv run pytest tests/test_observability.py -v -k "usage or traced_invoke"`
Expected: PASS — the `ollama_usage` / `message_usage` tests (unchanged) plus the rewritten `traced_invoke` test.

- [ ] **Step 5: Commit**

```bash
git add observability/tracing.py tests/test_observability.py
git commit -m "$(printf 'refactor(otel): port generation wrappers to the span seam\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: Atomic cutover — sweep all call sites + wire startup + rewrite node tests

Flip every `@observe`/`langfuse_context` call site to the new seam, point startup at `observability.otel`, and rewrite the node generation-usage tests. After this task, no application code imports `langfuse` except the soon-to-be-deleted `observability/langfuse.py`.

**Files:**
- Modify: `api/main.py:12,22`
- Modify (sweep): `graph/nodes/{intake,intent_router,history_appender,planner,human_review,rag_retriever,output_formatter,llm_caller,skill_dispatcher,memory_writer,risk_assessor}.py`, `api/routes/query.py`, `skills/{compliance_check,legal_research,drafting}.py`, `skills/contract_generation/contract_generation.py`, `skills/contract_review/contract_review.py`
- Test: `tests/test_observability.py` (rewrite the 4 node tests)

**Interfaces:**
- Consumes: `observability.spans.{traced, set_trace_attributes, set_gen_attributes}`, `observability.otel.init_observability`.

### Transformation rules (apply mechanically)

**A. Import line.** Replace `from langfuse.decorators import observe, langfuse_context` (or `from langfuse.decorators import observe`) with an import from `observability.spans`. Import exactly the names each file uses (per the table below). Leave any `from observability.tracing import ollama_usage` lines intact.

**B. Decorators.**
- `@observe(name="X")` → `@traced("X")`
- `@observe(name="X", as_type="generation")` → `@traced("X", kind="LLM")`
- `@observe(name="X", capture_input=False, capture_output=False)` → `@traced("X")` (capture flags are obsolete — we set input/output explicitly)

**C. Trace metadata.** `langfuse_context.update_current_trace(<kwargs>)` → `set_trace_attributes(<same kwargs>)`. The kwargs `name`, `user_id`, `session_id`, `input`, `tags`, `metadata` map one-to-one. Keep any surrounding `try/except` exactly as-is.

**D. Observation metadata.** `langfuse_context.update_current_observation(<kwargs>)` → `set_gen_attributes(<same kwargs>)`. The kwargs `name`, `input`, `output`, `model`, `usage`, `metadata` map one-to-one.

### Per-file import map

| File | Import from `observability.spans` |
|---|---|
| `graph/nodes/intake.py` | `traced, set_trace_attributes` |
| `graph/nodes/intent_router.py` | `traced, set_trace_attributes, set_gen_attributes` |
| `graph/nodes/planner.py` | `traced, set_gen_attributes` |
| `graph/nodes/llm_caller.py` | `traced, set_gen_attributes` |
| `graph/nodes/risk_assessor.py` | `traced, set_trace_attributes` |
| `api/routes/query.py` | `traced, set_trace_attributes` |
| `skills/contract_review/contract_review.py` | `traced, set_trace_attributes` |
| `graph/nodes/history_appender.py` | `traced` |
| `graph/nodes/human_review.py` | `traced` |
| `graph/nodes/rag_retriever.py` | `traced` |
| `graph/nodes/output_formatter.py` | `traced` |
| `graph/nodes/skill_dispatcher.py` | `traced` |
| `graph/nodes/memory_writer.py` | `traced` |
| `skills/compliance_check.py` | `traced` |
| `skills/legal_research.py` | `traced` |
| `skills/drafting.py` | `traced` |
| `skills/contract_generation/contract_generation.py` | `traced` |

### Worked examples (one per variant)

- [ ] **Step 1: Wire startup (`api/main.py`)**

Line 12: `from observability.langfuse import init_observability` → `from observability.otel import init_observability`. Leave the `init_observability()` call at line 22 unchanged.

- [ ] **Step 2: Sweep — plain node (example: `graph/nodes/rag_retriever.py`)**

```python
# before
from langfuse.decorators import observe
@observe(name="rag_retriever")
def rag_retriever(state):
    ...

# after
from observability.spans import traced
@traced("rag_retriever")
def rag_retriever(state):
    ...
```

- [ ] **Step 3: Sweep — generation node with observation (example: `graph/nodes/llm_caller.py`)**

```python
# before
from langfuse.decorators import observe, langfuse_context
@observe(name="llm_caller", as_type="generation")
...
        langfuse_context.update_current_observation(
            input=messages, output=content, model=settings.llm_model,
            usage=ollama_usage(data),
            metadata={"task_type": state.get("task_type", ""), "chunks_count": len(chunks), "temperature": 0.0},
        )

# after
from observability.spans import traced, set_gen_attributes
@traced("llm_caller", kind="LLM")
...
        set_gen_attributes(
            input=messages, output=content, model=settings.llm_model,
            usage=ollama_usage(data),
            metadata={"task_type": state.get("task_type", ""), "chunks_count": len(chunks), "temperature": 0.0},
        )
```

- [ ] **Step 4: Sweep — node with trace metadata (example: `graph/nodes/intake.py`)**

```python
# before
from langfuse.decorators import observe, langfuse_context
@observe(name="intake")
...
    langfuse_context.update_current_trace(
        user_id=user_id, session_id=state.get("session_id", ""),
        tags=[state.get("task_type") or "unclassified"],
    )

# after
from observability.spans import traced, set_trace_attributes
@traced("intake")
...
    set_trace_attributes(
        user_id=user_id, session_id=state.get("session_id", ""),
        tags=[state.get("task_type") or "unclassified"],
    )
```

- [ ] **Step 5: Sweep — route with both decorators (`api/routes/query.py`)**

`@observe(name="query")` → `@traced("query")`; `@observe(name="resume")` → `@traced("resume")`; both `langfuse_context.update_current_trace(...)` → `set_trace_attributes(...)` (kwargs unchanged, including `metadata={"user_name": user_name}`).

- [ ] **Step 6: Sweep — skills with capture flags (example: `skills/contract_review/contract_review.py`)**

`@observe(name="contract_review", capture_input=False, capture_output=False)` → `@traced("contract_review")`; both `langfuse_context.update_current_trace(metadata={...})` inside the existing `try/except` → `set_trace_attributes(metadata={...})`. Apply the plain form (`@traced("<name>")`) to `compliance_check.py`, `legal_research.py`, `drafting.py`, `contract_generation/contract_generation.py`. Apply the generation form to `intent_router.py`/`planner.py` (`kind="LLM"` + `set_gen_attributes`, plus `set_trace_attributes(tags=...)` in intent_router).

- [ ] **Step 7: Verify the sweep is complete**

Run: `grep -rn "langfuse" --include="*.py" . | grep -v ".venv" | grep -v "tests/"`
Expected: ONLY `observability/langfuse.py` lines remain (deleted in Task 6). No `graph/`, `api/`, `skills/` hits.

- [ ] **Step 8: Rewrite the 4 node tests in `tests/test_observability.py`**

Replace the three generation-usage node tests and the num_ctx test (they monkeypatch `mod.langfuse_context`, which no longer exists on the node modules). New versions assert on the exported span:

```python
def test_llm_caller_reports_generation_usage(monkeypatch):
    from graph.nodes import llm_caller as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": "answer"}, "prompt_eval_count": 200, "eval_count": 50}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    state = {"request": "q", "retrieved_chunks": [], "messages": [], "task_type": "research"}
    mod.llm_caller(state)

    span = _spans_by_name("llm_caller")[0]
    assert span.attributes.get("llm.token_count.prompt") == 200
    assert span.attributes.get("llm.token_count.completion") == 50
    assert span.attributes.get("llm.token_count.total") == 250
    assert span.attributes.get("output.value") == "answer"
    assert span.attributes.get("llm.model_name")


def test_planner_reports_generation_usage(monkeypatch):
    from graph.nodes import planner as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": '{"task_type":"research","skill_plan":["research"]}'},
                    "prompt_eval_count": 30, "eval_count": 12}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    state = {"request": "review then research", "skill_plan": ["contract_review", "research"]}
    mod.planner(state)

    span = _spans_by_name("planner")[0]
    assert span.attributes.get("llm.token_count.total") == 42
    assert span.attributes.get("llm.model_name")


def test_intent_router_reports_generation_usage(monkeypatch):
    from graph.nodes import intent_router as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": '{"task_type":"research"}'},
                    "prompt_eval_count": 18, "eval_count": 4}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    state = {"request": "what is an NDA?"}
    mod.intent_router(state)

    span = _spans_by_name("intent_router")[0]
    assert span.attributes.get("llm.token_count.total") == 22
    assert span.attributes.get("llm.model_name")


def test_llm_caller_sends_num_ctx_in_options(monkeypatch):
    """num_ctx must be in the options posted to Ollama (unrelated to tracing)."""
    from graph.nodes import llm_caller as mod
    from config import get_settings

    captured_json: dict = {}

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": "answer"}, "prompt_eval_count": 10, "eval_count": 5}

    def fake_post(url, *, json=None, timeout=None):
        captured_json.update(json or {})
        return FakeResp()

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    get_settings.cache_clear()

    state = {"request": "q", "retrieved_chunks": [], "messages": [], "task_type": "research"}
    mod.llm_caller(state)

    assert captured_json["options"].get("num_ctx") == 16384
    get_settings.cache_clear()
```

Add a trace-attribute test to lock the root-span identity mapping through a real node:

```python
def test_intake_stamps_identity_on_root(monkeypatch):
    from graph.nodes import intake as mod
    from observability.spans import traced

    @traced("query")                      # simulate the route root span
    def run():
        return mod.intake({"user_id": "u42", "session_id": "s7", "uploaded_docs": [], "task_type": "research"})

    run()
    root = _spans_by_name("query")[0]
    assert root.attributes.get("user.id") == "u42"
    assert root.attributes.get("session.id") == "s7"
```

(If `intake` requires more state keys to run, pass the minimal set its body reads — check `graph/nodes/intake.py` and add only what's dereferenced before the `set_trace_attributes` call.)

- [ ] **Step 9: Run the full observability suite + a graph smoke**

Run: `uv run pytest tests/test_observability.py tests/test_otel_init.py -v`
Expected: PASS.
Run: `uv run pytest tests/ -q`
Expected: PASS (whole suite green; confirms no call site broke).

- [ ] **Step 10: Commit**

```bash
git add api/main.py graph/ api/routes/query.py skills/ tests/test_observability.py
git commit -m "$(printf 'refactor(otel): cut all call sites over to the span seam\n\nSwap @observe/langfuse_context for @traced/set_trace_attributes/set_gen_attributes\nacross 11 nodes, 2 routes, 5 skills; point startup at observability.otel.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: Remove Langfuse

Delete the dead module, drop the dependency, remove the old config keys.

**Files:**
- Delete: `observability/langfuse.py`
- Modify: `requirements.txt`, `requirements-runtime.txt` (remove `langfuse`)
- Modify: `config.py` (remove `langfuse_host/public/secret` + `phoenix_host`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Add a test asserting the old keys are gone**

Add to `tests/test_config.py`:

```python
def test_legacy_tracing_keys_removed():
    from config import Settings
    s = Settings()
    for attr in ("langfuse_host", "langfuse_public_key", "langfuse_secret_key", "phoenix_host"):
        assert not hasattr(s, attr), f"{attr} should be removed"
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_config.py::test_legacy_tracing_keys_removed -v`
Expected: FAIL — the keys still exist.

- [ ] **Step 3: Delete `observability/langfuse.py`**

Run: `git rm observability/langfuse.py`

- [ ] **Step 4: Remove the old config keys**

In `config.py`, delete the `# Langfuse` block (`langfuse_host`, `langfuse_public_key`, `langfuse_secret_key`) and the `# Phoenix` block (`phoenix_host`).

- [ ] **Step 5: Drop the langfuse dependency**

Remove the `langfuse>=2.0,<3.0` line from **both** `requirements.txt` and `requirements-runtime.txt`. Then:

Run: `uv pip uninstall langfuse`

- [ ] **Step 6: Verify no langfuse references remain anywhere**

Run: `grep -rn "langfuse\|phoenix_host" --include="*.py" . | grep -v ".venv"`
Expected: no output.

- [ ] **Step 7: Run the full suite + boot the app**

Run: `uv run pytest tests/ -q`
Expected: PASS.
Run: `uv run python -c "import api.main"`
Expected: imports cleanly (no ModuleNotFoundError for langfuse).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "$(printf 'chore(otel): remove langfuse SDK, module, and legacy config keys\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: Compose + env examples

Point the local backend at Langfuse OTLP; add a dedicated Phoenix service to the VM overlay.

**Files:**
- Modify: `docker-compose.yml` (backend env), `docker-compose.remote.yml` (backend env + new `phoenix` service + volume)
- Modify: `.env.example`, `.env.remote.example`

- [ ] **Step 1: Local backend → Langfuse OTLP**

In `docker-compose.yml`, under the backend service `environment:` block, add:

```yaml
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://langfuse-web:3000/api/public/otel
      - OTEL_EXPORTER_OTLP_HEADERS=${OTEL_EXPORTER_OTLP_HEADERS}
```

(`environment` keys map to the pydantic settings `otel_exporter_otlp_endpoint` / `otel_exporter_otlp_headers` by name.)

- [ ] **Step 2: `.env.example` — the local Basic-auth header**

Add to `.env.example`:

```
# OTel tracing → local Langfuse v3 OTLP. The header is Basic auth for the Langfuse
# public:secret keypair. Regenerate if you change the local keys:
#   echo -n 'pk-lf-local:sk-lf-local' | base64
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic cGstbGYtbG9jYWw6c2stbGYtbG9jYWw=
```

Verify the base64 is current:
Run: `echo -n 'pk-lf-local:sk-lf-local' | base64`
Expected: `cGstbGYtbG9jYWw6c2stbGYtbG9jYWw=` (if different, use the printed value).

- [ ] **Step 3: VM overlay — dedicated Phoenix service**

In `docker-compose.remote.yml`, add a new service (sibling of `backend`/`caddy`):

```yaml
  phoenix:
    image: arizephoenix/phoenix:latest
    restart: unless-stopped
    volumes:
      - phoenix_data:/mnt/data
    # UI on 6006; OTLP/HTTP ingest on the same port at /v1/traces. Internal-only;
    # expose the UI over the VPN/Caddy if you want to browse traces remotely.
    expose:
      - "6006"
```

And add to the overlay's `volumes:` block (create the block if absent):

```yaml
volumes:
  phoenix_data:
```

- [ ] **Step 4: VM overlay — point backend at Phoenix, drop the dead Langfuse line**

In `docker-compose.remote.yml` backend `environment:`, remove `- LANGFUSE_HOST=http://langfuse-web:3000` and add:

```yaml
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006
      # no OTEL_EXPORTER_OTLP_HEADERS — Phoenix needs no auth
```

Add `phoenix` to the backend `depends_on:` block:

```yaml
      phoenix:
        condition: service_started
```

- [ ] **Step 5: `.env.remote.example`**

Add a note (Phoenix needs no header):

```
# OTel tracing on the VM goes to the dedicated Phoenix service (docker-compose.remote.yml).
# No auth header needed. Leave OTEL_EXPORTER_OTLP_HEADERS unset.
OTEL_EXPORTER_OTLP_HEADERS=
```

- [ ] **Step 6: Validate both compose configs render**

Run: `docker compose -f docker-compose.yml config >/dev/null && echo LOCAL_OK`
Run: `docker compose -f docker-compose.yml -f docker-compose.remote.yml config >/dev/null && echo REMOTE_OK`
Expected: `LOCAL_OK` and `REMOTE_OK` (no YAML/interpolation errors).

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml docker-compose.remote.yml .env.example .env.remote.example
git commit -m "$(printf 'feat(otel): local Langfuse OTLP + dedicated Phoenix on the VM overlay\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 8: Docs + live smokes

Update the docs and run the two live smokes (local now; Phoenix at VM deploy).

**Files:**
- Modify: `README.md` (observability section), `CLAUDE.md` (backend note), `docs/deploy-vm.md` (Phoenix smoke step)

- [ ] **Step 1: README observability section**

Replace any Langfuse-specific observability text with: one OTel instrumentation; backend chosen by `OTEL_EXPORTER_OTLP_ENDPOINT` (local Langfuse v3 OTLP / VM Phoenix); `tracing_enabled=false` (or unset endpoint) disables tracing with zero effect on behavior; `bash scripts/start.sh` traces to local Langfuse out of the box.

- [ ] **Step 2: CLAUDE.md backend note**

In the Backend section of `CLAUDE.md`, replace the Langfuse mentions with the OTel model: instrumentation seam is `observability/spans.py` (`@traced` / `set_trace_attributes` / `set_gen_attributes`); provider bootstrap in `observability/otel.py`; backend swappable by env; tracing is non-fatal (no provider ⇒ no-op spans). Note the `start.sh` restart requirement for `otel_*`/`tracing_enabled` config changes (they're `@lru_cache`'d in `get_settings`).

- [ ] **Step 3: `docs/deploy-vm.md` Phoenix smoke**

Add a step: after `docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d`, bring up Phoenix and verify a trace lands:
```bash
# run one query through the add-in (or curl /api/query), then browse Phoenix
# (proxy 6006 over the VPN or add a Caddy route) — the trace tree should show
# query → intent_router/contract_review → generation spans with token counts.
```

- [ ] **Step 4: Live local smoke**

Run: `bash scripts/start.sh` (ensure `docker compose up -d` has Langfuse running), submit a query (Word add-in or `curl -s localhost:8000/api/query -H 'Content-Type: application/json' -H 'X-User-ID: smoke' -d '{"request":"what is an NDA?","task_type":"research"}'`), then open the local Langfuse UI (http://localhost:3000).
Expected: a `query:research` trace with `user.id=smoke`, a session id, nested node spans, and token counts on the generation spans. If the trace is missing, check `OTEL_EXPORTER_OTLP_HEADERS` (a wrong Basic header silently 401s).

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md docs/deploy-vm.md
git commit -m "$(printf 'docs(otel): observability model + Phoenix VM smoke step\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Self-Review (completed during authoring)

**Spec coverage:**
- One instrumentation, backend by env → Tasks 3 (otel.py), 7 (compose). ✓
- Port-only, no auto-instrument → deps in Task 1 pull `openinference-semantic-conventions` only (no `-instrumentation-langchain`). ✓
- `@traced` / `set_trace_attributes` / `set_gen_attributes` seam → Task 2. ✓
- Root-span contextvar for trace-level attrs → Task 2 (`_root_span`, tested `test_set_trace_attributes_lands_on_root_from_nested_span`). ✓
- Attribute mapping table (incl. single-JSON `metadata`, corrected from the spec's per-key note) → Task 2 helpers + tests. ✓
- Opaque-header auth, no vendor config keys → Task 1 (`otel_*`) + Task 6 (drop `langfuse_*`/`phoenix_host`). ✓
- Non-fatal contract → Task 2 (`test_helpers_no_op...`, `test_traced_is_noop_under_noop_tracer`), Task 3 (`test_init_is_best_effort_on_failure`). ✓
- DoD: in-memory-exporter unit tests (Tasks 2–5), local Langfuse smoke (Task 8 Step 4), VM Phoenix smoke as deploy step (Task 8 Step 3). ✓
- 17-file / ~18-site sweep → Task 5 (per-file import map + variant examples). ✓

**Placeholder scan:** no TBD/TODO; every code step shows real code; sweep uses explicit rules + a per-file table + one worked example per variant (not "similar to Task N").

**Type consistency:** `TokenUsage` `{input, output, total, unit}` is produced by `ollama_usage`/`message_usage` (Task 4) and consumed as `usage` by `set_gen_attributes` (Task 2), which reads `input`/`output`/`total`. `traced`/`set_trace_attributes`/`set_gen_attributes` signatures match between definition (Task 2) and all call sites (Tasks 4–5). `_spans_by_name` helper defined once (Task 2) and reused. ✓
