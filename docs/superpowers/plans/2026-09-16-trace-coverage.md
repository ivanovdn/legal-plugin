# Trace Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenTelemetry spans tell the truth about turns that failed or silently degraded, so `status = ERROR` means "the attorney did not get their answer" and `app.outcome != "ok"` finds every turn that went wrong.

**Architecture:** This app degrades rather than raises — 40 `except Exception` sites, nearly all load-bearing — and OTel only marks a span ERROR when an exception *propagates*, so almost no span is ever marked. Two new seam functions (`mark_failed`, `record_degradation`) are called *at the point of each catch*, accumulate reason codes on the request root via a contextvar, and one derived `app.outcome` is stamped on the root by the three request entry points. Plus `httpx` auto-instrumentation for network-layer timing and a handful of structural spans.

**Tech Stack:** Python 3.12 · uv · OpenTelemetry SDK 1.41 · `openinference-semantic-conventions` · pytest + `InMemorySpanExporter` · FastAPI · LangGraph

**Spec:** `docs/superpowers/specs/2026-09-16-trace-coverage-design.md` — read it first. The plan argues from the spec; where they disagree, the spec wins.

## Global Constraints

- **Python 3.12**, `uv` for everything: `uv run pytest`, `uv pip install`. Never 3.13/3.14.
- **All imports at top of file.** No lazy imports inside functions. (CLAUDE.md hard rule #1.)
- **No backwards-compat shims.** Change call sites instead. (CLAUDE.md hard rule #5.)
- **Tracing must never break a turn.** Every new seam helper is wrapped in `try/except Exception: pass` and returns `None`. A helper that raises is a bug that takes down production.
- **Class 4 sites are untouched.** `observability/spans.py:113/153/169`, `observability/otel.py:65`, `graph/nodes/risk_assessor.py:164`, `skills/contract_review/contract_review.py:143/205`, `graph/nodes/memory_writer.py:89`, `memory/feedback_store.py:164`. Tracing must not report its own best-effort catches as application degradations.
- **RAG is out of scope** (descoped 2026-09-16). Do not touch `rag/`, `api/routes/documents.py`, or add reranker/BM25/embedding spans.
- **No new attorney-facing surfaces.** Trace-only. No new pane banners, no payload fields.
- **22 reason codes, closed set.** Every `mark_failed`/`record_degradation` call passes a constant from `observability/degradations.py` — never a string literal. Task 11 enforces this.
- **Tests require Docker** (`tests/conftest.py` spins an ephemeral Postgres via testcontainers).
- **Restart after config changes:** `get_settings` is `@lru_cache`'d — `bash scripts/start.sh` after touching any `otel_*` field.
- **Branch:** all work on `feat/trace-coverage`, cut from `main`. Never implement on `main`.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `observability/degradations.py` | The closed vocabulary. Constants + three frozensets. No logic. |
| `scripts/check_degradation_vocabulary.py` | Two-way assertion that declared codes and used codes agree. |
| `tests/test_degradation_seam.py` | Unit tests for the four new seam functions. |
| `tests/test_turn_outcome.py` | Integration tests: real code paths, injected failures, asserted spans. |
| `docs/testing-observability.md` | The four manual failure drills no gate can reach. |

**Modified:**

| File | Change |
|---|---|
| `observability/spans.py` | `+record_degradation`, `+mark_failed`, `+set_outcome`, `+degradations`, `+_root_degradations` contextvar |
| `observability/tracing.py` | `+OllamaTimings`, `+ollama_timings` |
| `observability/otel.py` | `+_instrument_libraries()` |
| `tests/conftest.py` | Span fixture moves here from `tests/test_observability.py` |
| `config.py` | `+otel_instrument_redis` |
| `requirements.txt` | `+opentelemetry-instrumentation-httpx` |
| `scripts/check.sh` | `+` vocabulary assertion step |
| `api/routes/query.py` | 4 failed codes, `checkpointer_unavailable`, `_derive_outcome`, `set_outcome` ×2 |
| `api/routes/compact.py` | `@traced("compact")`, `compaction_failed`, `set_outcome` |
| `graph/nodes/llm_caller.py` | `llm_call_failed`, `context_truncated`, `timings=` |
| `graph/nodes/intent_router.py` | `intent_classification_failed` |
| `graph/nodes/planner.py` | `planning_failed` |
| `graph/nodes/memory_writer.py` | `audit_write_failed`, `review_persist_failed` |
| `graph/checkpointer.py` | `checkpointer_unavailable` (startup-absent) |
| `skills/legal_research/legal_research.py` | `legal_research_failed` |
| `skills/legal_research/context.py` | 5 codes |
| `skills/contract_generation/contract_generation.py` | `contract_generation_failed` ×2 |
| `skills/contract_review/contract_review.py` | `msa_lookup_failed` |
| `skills/grounding.py` | `preferences_load_failed` |
| `memory/audit.py`, `review_store.py`, `conversation_store.py`, `conversation_summary.py` | `@traced` on 8 functions |
| `docker-compose.remote.yml` | Phoenix port bind |
| `docs/wiki.md`, `CLAUDE.md`, `docs/deploy-vm.md` | Documentation |

---

## Task 1: Move the span test harness to conftest

The `InMemorySpanExporter` currently lives inside `tests/test_observability.py`, so no other test file can assert on spans. Every later task needs it. This task must come first.

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_observability.py:22-40`

**Interfaces:**
- Produces: two session/function fixtures plus a helper other test modules import — `from tests.conftest import spans_by_name`. Signature: `spans_by_name(name: str) -> list[ReadableSpan]`.

- [ ] **Step 1: Add the fixtures and helper to `tests/conftest.py`**

Append to `tests/conftest.py` (keep the existing `os.environ.setdefault("TRACING_ENABLED", "false")` at the top — it is what stops `init_observability()` from fighting this provider):

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_span_exporter = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _otel_test_provider():
    """One recording provider for the whole suite.

    App tracing is off in tests (TRACING_ENABLED=false above), so this is the
    first real set_tracer_provider call and it wins. Spans become real in every
    test file, which is the point: assertions about degradations live wherever
    the code under test lives, not only in test_observability.py.
    """
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_span_exporter))
    trace.set_tracer_provider(provider)
    yield


@pytest.fixture(autouse=True)
def _clear_spans():
    _span_exporter.clear()
    yield
    _span_exporter.clear()


def spans_by_name(name: str):
    """Finished spans with this name, for assertions."""
    return [s for s in _span_exporter.get_finished_spans() if s.name == name]
```

- [ ] **Step 2: Delete the duplicates from `tests/test_observability.py`**

Remove lines 22-40 (the `_exporter`, `_otel_test_provider`, `_clear_spans`, `_spans_by_name` definitions) and replace with:

```python
from tests.conftest import spans_by_name as _spans_by_name
```

Leave every existing test body unchanged — they call `_spans_by_name(...)`, which now resolves to the conftest helper.

- [ ] **Step 3: Run the full suite to prove nothing regressed**

Run: `uv run pytest tests/ -q`
Expected: PASS, same count as before the change. In particular `test_helpers_are_noops_without_provider` must still pass — it asserts behavior *outside* any span, where `INVALID_SPAN` stays non-recording even with a global provider set.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_observability.py
git commit -m "test: move the span exporter fixture to conftest so every test file can assert on spans"
```

---

## Task 2: The closed vocabulary

**Files:**
- Create: `observability/degradations.py`
- Test: `tests/test_degradation_seam.py`

**Interfaces:**
- Produces: 22 module-level `str` constants; `FAILED_REASONS`, `ANNOUNCED_REASONS`, `SILENT_REASONS`, `ALL_REASONS` (all `frozenset[str]`). Every later task imports constants from here.

- [ ] **Step 1: Write the failing test**

Create `tests/test_degradation_seam.py`:

```python
# tests/test_degradation_seam.py
"""The degradation vocabulary and the four seam helpers."""
from __future__ import annotations

import pytest
from opentelemetry.trace import StatusCode

from tests.conftest import spans_by_name


def test_vocabulary_is_closed_and_partitioned():
    import observability.degradations as D

    assert len(D.FAILED_REASONS) == 8
    assert len(D.ANNOUNCED_REASONS) == 7
    assert len(D.SILENT_REASONS) == 7
    assert len(D.ALL_REASONS) == 22
    # The three classes must not overlap — a code is failed, announced or
    # silent, never two of them.
    assert D.FAILED_REASONS & D.ANNOUNCED_REASONS == frozenset()
    assert D.FAILED_REASONS & D.SILENT_REASONS == frozenset()
    assert D.ANNOUNCED_REASONS & D.SILENT_REASONS == frozenset()
    assert D.ALL_REASONS == D.FAILED_REASONS | D.ANNOUNCED_REASONS | D.SILENT_REASONS


def test_every_constant_value_matches_its_name():
    """A constant whose value drifts from its name makes greps lie."""
    import observability.degradations as D

    for attr in dir(D):
        if attr.isupper() and isinstance(getattr(D, attr), str):
            assert getattr(D, attr) == attr.lower(), f"{attr} value does not match its name"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'observability.degradations'`

- [ ] **Step 3: Create the module**

```python
# observability/degradations.py
"""Closed vocabulary of degradation reason codes.

Free-text reasons fragment into things you cannot filter on. Every call to
observability.spans.mark_failed / record_degradation passes one of these
constants — never a string literal — and scripts/check_degradation_vocabulary.py
asserts the declared set and the used set agree in BOTH directions:

  - declared but never used  -> a site someone forgot to wire
  - used but not declared    -> a typo silently creating a new category

A unit test cannot catch either: a green test_record_degradation_emits_event
passes with 21 of 22 sites unwired, because the call sites simply do not exist.

Class 4 (telemetry self-catch) deliberately has NO codes. Tracing reporting its
own best-effort catches as application degradations is how a dashboard becomes
decoration. See docs/superpowers/specs/2026-09-16-trace-coverage-design.md.
"""
from __future__ import annotations

# --- Class 1: FAILED -------------------------------------------------------
# The attorney got no answer, or an error string as the answer.
# Wired with mark_failed() -> span status ERROR.
LLM_CALL_FAILED = "llm_call_failed"
LEGAL_RESEARCH_FAILED = "legal_research_failed"
CONTRACT_GENERATION_FAILED = "contract_generation_failed"
COMPACTION_FAILED = "compaction_failed"
GRAPH_INVOKE_FAILED = "graph_invoke_failed"
STATELESS_FALLBACK_FAILED = "stateless_fallback_failed"
RESUME_STATE_LOAD_FAILED = "resume_state_load_failed"
RESUME_FAILED = "resume_failed"

FAILED_REASONS = frozenset({
    LLM_CALL_FAILED,
    LEGAL_RESEARCH_FAILED,
    CONTRACT_GENERATION_FAILED,
    COMPACTION_FAILED,
    GRAPH_INVOKE_FAILED,
    STATELESS_FALLBACK_FAILED,
    RESUME_STATE_LOAD_FAILED,
    RESUME_FAILED,
})

# --- Class 2: ANNOUNCED ----------------------------------------------------
# Answer lands, quality is worse, and the attorney IS told (banner / notice).
# Wired with record_degradation(announced=True). Never changes span status.
CHECKPOINTER_UNAVAILABLE = "checkpointer_unavailable"
AUDIT_WRITE_FAILED = "audit_write_failed"
REVIEW_PERSIST_FAILED = "review_persist_failed"
PRIOR_REVIEW_LOAD_FAILED = "prior_review_load_failed"
SUMMARY_LOAD_FAILED = "summary_load_failed"
PRIOR_CONVERSATION_LOAD_FAILED = "prior_conversation_load_failed"
CONTEXT_TRUNCATED = "context_truncated"

ANNOUNCED_REASONS = frozenset({
    CHECKPOINTER_UNAVAILABLE,
    AUDIT_WRITE_FAILED,
    REVIEW_PERSIST_FAILED,
    PRIOR_REVIEW_LOAD_FAILED,
    SUMMARY_LOAD_FAILED,
    PRIOR_CONVERSATION_LOAD_FAILED,
    CONTEXT_TRUNCATED,
})

# --- Class 3: SILENT -------------------------------------------------------
# Answer lands, quality is quietly worse, and NOBODY is told. This class is the
# reason this work exists. Wired with record_degradation(announced=False).
CHAT_GROUNDING_FAILED = "chat_grounding_failed"
REVIEW_RECONCILIATION_FAILED = "review_reconciliation_failed"
COMPRESSIBLE_HISTORY_READ_FAILED = "compressible_history_read_failed"
PREFERENCES_LOAD_FAILED = "preferences_load_failed"
MSA_LOOKUP_FAILED = "msa_lookup_failed"
INTENT_CLASSIFICATION_FAILED = "intent_classification_failed"
PLANNING_FAILED = "planning_failed"

SILENT_REASONS = frozenset({
    CHAT_GROUNDING_FAILED,
    REVIEW_RECONCILIATION_FAILED,
    COMPRESSIBLE_HISTORY_READ_FAILED,
    PREFERENCES_LOAD_FAILED,
    MSA_LOOKUP_FAILED,
    INTENT_CLASSIFICATION_FAILED,
    PLANNING_FAILED,
})

ALL_REASONS = FAILED_REASONS | ANNOUNCED_REASONS | SILENT_REASONS
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add observability/degradations.py tests/test_degradation_seam.py
git commit -m "feat(observability): closed vocabulary of 22 degradation reason codes"
```

---

## Task 3: `record_degradation` + `degradations()`

**Files:**
- Modify: `observability/spans.py`
- Test: `tests/test_degradation_seam.py`

**Interfaces:**
- Consumes: `observability.degradations` constants (Task 2).
- Produces: `record_degradation(reason: str, *, announced: bool, detail: str = "") -> None` and `degradations() -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_degradation_seam.py`:

```python
def test_record_degradation_adds_event_and_accumulates_on_root():
    from observability.degradations import CHAT_GROUNDING_FAILED
    from observability.spans import traced, record_degradation, degradations

    seen = {}

    @traced("child")
    def child():
        record_degradation(CHAT_GROUNDING_FAILED, announced=False, detail="qdrant down")

    @traced("root")
    def root():
        child()
        seen["reasons"] = degradations()

    root()
    events = [e for e in spans_by_name("child")[0].events if e.name == "degradation"]
    assert len(events) == 1
    assert events[0].attributes["degradation.reason"] == CHAT_GROUNDING_FAILED
    assert events[0].attributes["degradation.announced"] is False
    assert events[0].attributes["degradation.detail"] == "qdrant down"
    # Recorded on the child span, but accumulated for the ROOT to read back.
    assert seen["reasons"] == [CHAT_GROUNDING_FAILED]


def test_record_degradation_never_changes_span_status():
    """A fallback that worked is not an error. This is the whole class-2/3 point."""
    from observability.degradations import PREFERENCES_LOAD_FAILED
    from observability.spans import traced, record_degradation

    @traced("node")
    def node():
        record_degradation(PREFERENCES_LOAD_FAILED, announced=False)

    node()
    assert spans_by_name("node")[0].status.status_code != StatusCode.ERROR


def test_degradations_do_not_leak_between_turns():
    from observability.degradations import AUDIT_WRITE_FAILED, SUMMARY_LOAD_FAILED
    from observability.spans import traced, record_degradation, degradations

    @traced("turn")
    def turn(reason):
        record_degradation(reason, announced=True)
        return degradations()

    assert turn(AUDIT_WRITE_FAILED) == [AUDIT_WRITE_FAILED]
    assert turn(SUMMARY_LOAD_FAILED) == [SUMMARY_LOAD_FAILED]


def test_record_degradation_deduplicates():
    """A retry loop must not inflate the reason list."""
    from observability.degradations import MSA_LOOKUP_FAILED
    from observability.spans import traced, record_degradation, degradations

    @traced("turn")
    def turn():
        record_degradation(MSA_LOOKUP_FAILED, announced=False)
        record_degradation(MSA_LOOKUP_FAILED, announced=False)
        return degradations()

    assert turn() == [MSA_LOOKUP_FAILED]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: FAIL with `ImportError: cannot import name 'record_degradation' from 'observability.spans'`

- [ ] **Step 3: Implement**

In `observability/spans.py`, add the contextvar next to `_root_metadata` (after line 36):

```python
_root_degradations: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "otel_root_degradations", default=None
)
```

In `traced`'s wrapper, inside the `if _root_span.get() is None:` branch, add a third token — and reset it in the same `finally` as the other two:

```python
                root_token = None
                meta_token = None
                deg_token = None
                if _root_span.get() is None:
                    root_token = _root_span.set(span)
                    meta_token = _root_metadata.set({})
                    deg_token = _root_degradations.set([])
```

```python
                finally:
                    if root_token is not None:
                        _root_span.reset(root_token)
                    if meta_token is not None:
                        _root_metadata.reset(meta_token)
                    if deg_token is not None:
                        _root_degradations.reset(deg_token)
```

Add at the end of the module:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add observability/spans.py tests/test_degradation_seam.py
git commit -m "feat(observability): record_degradation as a span event, accumulated on the request root"
```

---

## Task 4: `mark_failed`

**Files:**
- Modify: `observability/spans.py`
- Test: `tests/test_degradation_seam.py`

**Interfaces:**
- Produces: `mark_failed(reason: str, *, exc: BaseException | None = None, detail: str = "") -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_degradation_seam.py`:

```python
def test_mark_failed_sets_error_status_on_the_current_span():
    """OTel only records exceptions that PROPAGATE. This app catches them, so
    the status has to be set by hand at the point of the catch."""
    from observability.degradations import LLM_CALL_FAILED
    from observability.spans import traced, mark_failed

    @traced("llm_caller")
    def node():
        try:
            raise RuntimeError("ollama refused the connection")
        except RuntimeError as e:
            mark_failed(LLM_CALL_FAILED, exc=e)
        return "Error: LLM call failed"      # what the real node does

    assert node() == "Error: LLM call failed"
    span = spans_by_name("llm_caller")[0]
    assert span.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)


def test_mark_failed_accumulates_like_a_degradation():
    from observability.degradations import LLM_CALL_FAILED
    from observability.spans import traced, mark_failed, degradations

    @traced("root")
    def root():
        mark_failed(LLM_CALL_FAILED)
        return degradations()

    assert root() == [LLM_CALL_FAILED]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: FAIL with `ImportError: cannot import name 'mark_failed'`

- [ ] **Step 3: Implement**

Extend the import at `observability/spans.py:21`:

```python
from opentelemetry.trace import Span, Status, StatusCode
```

Add:

```python
def mark_failed(reason: str, *, exc: BaseException | None = None, detail: str = "") -> None:
    """Set the CURRENT span to ERROR and record `exc`. Never raises.

    For failures this app CATCHES and converts into a degraded answer. OTel
    marks a span ERROR only when an exception propagates out of the `with`
    block; there are 40 `except Exception` sites here and almost none of them
    propagate, so without this call every failed turn looks like a healthy one.
    """
    try:
        _accumulate(reason)
        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return
        if exc is not None:
            span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, detail or reason))
    except Exception:
        pass
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add observability/spans.py tests/test_degradation_seam.py
git commit -m "feat(observability): mark_failed sets ERROR status at the point of a caught failure"
```

---

## Task 5: `set_outcome`

**Files:**
- Modify: `observability/spans.py`
- Test: `tests/test_degradation_seam.py`

**Interfaces:**
- Produces: `set_outcome(outcome: str) -> None`. Sets `app.outcome` and `app.degradations` on the root span; when `outcome == "failed"` also sets root status ERROR.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_degradation_seam.py`:

```python
def test_set_outcome_lands_on_root_from_a_nested_span():
    from observability.spans import traced, set_outcome

    @traced("child")
    def child():
        set_outcome("degraded")

    @traced("root")
    def root():
        child()

    root()
    assert spans_by_name("root")[0].attributes.get("app.outcome") == "degraded"
    assert "app.outcome" not in spans_by_name("child")[0].attributes


def test_failed_outcome_also_sets_root_status_error():
    """Two spans end up ERROR and that is intended: the node says WHERE it
    broke, the root says the attorney got nothing."""
    import json
    from observability.degradations import LLM_CALL_FAILED
    from observability.spans import traced, mark_failed, set_outcome, degradations

    @traced("llm_caller")
    def node():
        mark_failed(LLM_CALL_FAILED)

    @traced("root")
    def root():
        node()
        set_outcome("failed")

    root()
    root_span = spans_by_name("root")[0]
    assert root_span.status.status_code == StatusCode.ERROR
    assert root_span.attributes["app.outcome"] == "failed"
    assert json.loads(root_span.attributes["app.degradations"]) == [LLM_CALL_FAILED]
    assert spans_by_name("llm_caller")[0].status.status_code == StatusCode.ERROR


def test_ok_outcome_leaves_status_alone_and_writes_no_reason_list():
    from observability.spans import traced, set_outcome

    @traced("root")
    def root():
        set_outcome("ok")

    root()
    span = spans_by_name("root")[0]
    assert span.attributes["app.outcome"] == "ok"
    assert span.status.status_code != StatusCode.ERROR
    assert "app.degradations" not in span.attributes
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: FAIL with `ImportError: cannot import name 'set_outcome'`

- [ ] **Step 3: Implement**

```python
def set_outcome(outcome: str) -> None:
    """Stamp app.outcome on the ROOT span. Never raises.

    Called once per request root — submit_query, resume_query, post_compact.
    One derived field so "show me the bad turns" is `app.outcome != "ok"`
    rather than an OR-chain across attributes you have to remember.

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
        if outcome == "failed":
            span.set_status(Status(StatusCode.ERROR, ", ".join(reasons) or "failed"))
    except Exception:
        pass
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_degradation_seam.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add observability/spans.py tests/test_degradation_seam.py
git commit -m "feat(observability): app.outcome rollup on the request root span"
```

---

## Task 6: Ollama timings

`ollama_usage` reads 2 fields from a response carrying 6. `load_duration` is the one that matters — the `ollama_num_ctx` incident was a measured 4.7 s model reload on every call, and the number was in this payload the whole time.

**Files:**
- Modify: `observability/tracing.py`
- Modify: `observability/spans.py` (`set_gen_attributes` gains `timings=`)
- Modify: `graph/nodes/llm_caller.py:166-175`
- Test: `tests/test_observability.py`

**Interfaces:**
- Produces: `OllamaTimings` (`TypedDict, total=False`: `total_ms`, `load_ms`, `prompt_eval_ms`, `eval_ms`) and `ollama_timings(response_json: dict) -> OllamaTimings | None`.
- Produces: `set_gen_attributes(..., timings: dict | None = None)` → span attributes `llm.ollama.<key>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observability.py`:

```python
def test_ollama_timings_converts_nanoseconds_to_milliseconds():
    from observability.tracing import ollama_timings

    timings = ollama_timings({
        "total_duration": 34_659_000_000,
        "load_duration": 4_713_000_000,
        "prompt_eval_duration": 8_902_000_000,
        "eval_duration": 21_044_000_000,
    })
    assert timings == {
        "total_ms": 34_659, "load_ms": 4_713,
        "prompt_eval_ms": 8_902, "eval_ms": 21_044,
    }


def test_ollama_timings_none_when_absent():
    from observability.tracing import ollama_timings
    assert ollama_timings({"message": {"content": "hi"}}) is None


def test_ollama_timings_partial_payload():
    from observability.tracing import ollama_timings
    assert ollama_timings({"load_duration": 4_713_000_000}) == {"load_ms": 4_713}


def test_set_gen_attributes_records_timings_on_the_span():
    from observability.spans import traced, set_gen_attributes

    @traced("gen", kind="LLM")
    def gen():
        set_gen_attributes(model="m", timings={"load_ms": 4713, "eval_ms": 21044})

    gen()
    attrs = _spans_by_name("gen")[0].attributes
    assert attrs["llm.ollama.load_ms"] == 4713
    assert attrs["llm.ollama.eval_ms"] == 21044
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_observability.py -q -k timings`
Expected: FAIL with `ImportError: cannot import name 'ollama_timings'`

- [ ] **Step 3: Implement**

In `observability/tracing.py`, after the `TokenUsage` class:

```python
class OllamaTimings(TypedDict, total=False):
    total_ms: int
    load_ms: int
    prompt_eval_ms: int
    eval_ms: int


_TIMING_FIELDS = (
    ("total_ms", "total_duration"),
    ("load_ms", "load_duration"),
    ("prompt_eval_ms", "prompt_eval_duration"),
    ("eval_ms", "eval_duration"),
)


def ollama_timings(response_json: dict[str, Any]) -> OllamaTimings | None:
    """Ollama's nanosecond duration fields, as milliseconds.

    `load_ms` is why this exists: asking for a num_ctx that differs from the
    resident one reloads the model in EITHER direction, which cost a measured
    4.7 s on every single call against a shared box. The number was in this
    payload all along; ollama_usage simply never read it.
    """
    out: dict[str, int] = {}
    for out_key, src_key in _TIMING_FIELDS:
        ns = response_json.get(src_key)
        if ns is not None:
            out[out_key] = int(ns) // 1_000_000
    return out or None      # type: ignore[return-value]
```

In `observability/spans.py`, add `timings` to `set_gen_attributes`'s signature (after `usage`):

```python
    timings: dict | None = None,
```

and in its body, after the `usage` block:

```python
        if timings:
            for key, value in timings.items():
                span.set_attribute(f"llm.ollama.{key}", int(value))
```

In `graph/nodes/llm_caller.py`, add the import at the top:

```python
from observability.tracing import ollama_timings, ollama_usage
```

and pass it at line ~166:

```python
        set_gen_attributes(
            input=messages,
            output=content,
            model=settings.llm_model,
            usage=ollama_usage(data),
            timings=ollama_timings(data),
            metadata={
                "task_type": state.get("task_type", ""),
                "chunks_count": len(chunks),
                "temperature": 0.0,
            },
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_observability.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add observability/tracing.py observability/spans.py graph/nodes/llm_caller.py tests/test_observability.py
git commit -m "feat(observability): record Ollama load/prefill/decode timings on LLM spans"
```

---

## Task 7: Wire `llm_caller` — RED TEST #1

The first of the three tests the spec requires to be red before any code. `llm_caller` catches the Ollama failure *inside* its own `@traced(kind="LLM")` span and writes an error string into state, so the span closes clean.

**Files:**
- Modify: `graph/nodes/llm_caller.py:180-184`
- Test: `tests/test_turn_outcome.py`

**Interfaces:**
- Consumes: `mark_failed` (Task 4), `LLM_CALL_FAILED` (Task 2).

- [ ] **Step 1: Write the failing test**

Create `tests/test_turn_outcome.py`:

```python
# tests/test_turn_outcome.py
"""Real code paths with injected failures, asserted on spans.

These are the tests the spec requires to be RED before implementation — a green
suite here means nothing unless you have watched them fail first.
"""
from __future__ import annotations

import httpx
import pytest
from opentelemetry.trace import StatusCode

from tests.conftest import spans_by_name


def test_llm_caller_marks_its_span_error_when_ollama_fails(monkeypatch):
    """RED TEST #1. Today the span closes clean with status UNSET, so a turn
    where Ollama timed out is byte-identical in Phoenix to a healthy one."""
    from graph.nodes import llm_caller as mod
    from observability.degradations import LLM_CALL_FAILED

    def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mod.httpx, "post", boom)

    state = {
        "messages": [{"role": "user", "content": "review this"}],
        "task_type": "contract_review",
        "retrieved_chunks": [],
    }
    mod.llm_caller(state)

    assert state["llm_response"].startswith("Error: LLM call failed")
    span = spans_by_name("llm_caller")[0]
    assert span.status.status_code == StatusCode.ERROR
    events = [e for e in span.events if e.name == "degradation"]
    assert not events, "a failure is mark_failed, not record_degradation"
    assert any(e.name == "exception" for e in span.events)
```

- [ ] **Step 2: Run it and WATCH IT FAIL**

Run: `uv run pytest tests/test_turn_outcome.py -q`
Expected: FAIL — `assert <StatusCode.UNSET: 0> == <StatusCode.ERROR: 2>`

Do not proceed until you have seen this exact failure. It is the proof the test is not vacuous.

- [ ] **Step 3: Implement**

In `graph/nodes/llm_caller.py`, extend the imports:

```python
from observability.degradations import CONTEXT_TRUNCATED, LLM_CALL_FAILED
from observability.spans import mark_failed, record_degradation, set_gen_attributes, traced
```

Change the except block at line 180:

```python
    except Exception as e:
        logger.error(
            "[llm_caller] LLM call FAILED after %.1fs: %s", time.monotonic() - started, e
        )
        mark_failed(LLM_CALL_FAILED, exc=e, detail=e.__class__.__name__)
        state["llm_response"] = f"Error: LLM call failed — {e}"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_turn_outcome.py -q`
Expected: PASS

- [ ] **Step 5: Wire `context_truncated` in the same file**

`llm_caller` detects overflow past the headroom (around line 125-131) and builds the `context_truncated` dict. Immediately after that dict is assigned, add:

```python
        record_degradation(
            CONTEXT_TRUNCATED,
            announced=True,
            detail=f"kept {state['context_truncated']['kept_pct']}% of the document",
        )
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add graph/nodes/llm_caller.py tests/test_turn_outcome.py
git commit -m "feat(observability): llm_caller marks ERROR on a caught Ollama failure"
```

---

## Task 8: Wire `api/routes/query.py` and derive the outcome

**Files:**
- Modify: `api/routes/query.py`
- Test: `tests/test_turn_outcome.py`

**Interfaces:**
- Produces: `_derive_outcome(reasons: list[str]) -> str` in `api/routes/query.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_turn_outcome.py`:

```python
def test_derive_outcome_maps_reasons_to_the_three_states():
    from api.routes.query import _derive_outcome
    from observability.degradations import (
        AUDIT_WRITE_FAILED, CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED,
    )

    assert _derive_outcome([]) == "ok"
    assert _derive_outcome([CHAT_GROUNDING_FAILED]) == "degraded"
    assert _derive_outcome([AUDIT_WRITE_FAILED]) == "degraded"
    assert _derive_outcome([GRAPH_INVOKE_FAILED]) == "failed"
    # A failure anywhere in the list wins over any number of degradations.
    assert _derive_outcome([CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED]) == "failed"


def test_submit_query_marks_failed_when_the_graph_raises(monkeypatch):
    import json
    from api.routes import query as mod
    from api.models import QueryRequest
    from observability.degradations import GRAPH_INVOKE_FAILED

    class BoomGraph:
        def invoke(self, *a, **k):
            raise ValueError("node exploded")

    monkeypatch.setattr(mod, "_get_graph", lambda: BoomGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", False)

    resp = mod.submit_query(
        QueryRequest(request="review this"), user_id="u1", user_name="U",
    )
    assert resp.status == "error"

    span = spans_by_name("query")[0]
    assert span.attributes["app.outcome"] == "failed"
    assert span.status.status_code == StatusCode.ERROR
    assert GRAPH_INVOKE_FAILED in json.loads(span.attributes["app.degradations"])


def test_submit_query_is_ok_on_a_clean_turn(monkeypatch):
    from api.routes import query as mod
    from api.models import QueryRequest

    class CleanGraph:
        def invoke(self, state, **k):
            return {**state, "llm_response": "done", "report": {}}

    monkeypatch.setattr(mod, "_get_graph", lambda: CleanGraph())
    monkeypatch.setattr(mod, "refresh_ttl", lambda *a, **k: None)

    resp = mod.submit_query(
        QueryRequest(request="hi"), user_id="u1", user_name="U",
    )
    assert resp.status == "ok"
    span = spans_by_name("query")[0]
    assert span.attributes["app.outcome"] == "ok"
    assert span.status.status_code != StatusCode.ERROR
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_turn_outcome.py -q`
Expected: FAIL with `ImportError: cannot import name '_derive_outcome'`

- [ ] **Step 3: Implement**

Extend the imports in `api/routes/query.py`:

```python
from observability.degradations import (
    CHECKPOINTER_UNAVAILABLE, FAILED_REASONS, GRAPH_INVOKE_FAILED,
    RESUME_FAILED, RESUME_STATE_LOAD_FAILED, STATELESS_FALLBACK_FAILED,
)
from observability.spans import (
    current_trace_id, degradations, mark_failed, record_degradation,
    set_outcome, set_trace_attributes, traced,
)
```

Add the derivation helper near `_memory_degraded`:

```python
def _derive_outcome(reasons: list[str]) -> str:
    """ok | degraded | failed, from the reason codes recorded this request.

    ONE source, read back — not recomputed. `degradations()` is complete by
    construction: every site that sets a report flag (memory_degraded,
    context_truncated, review_persist_error) also records a reason code, so
    the accumulator already sees everything the pane sees. OR-ing the report
    flags in as well would create two sources that can disagree.
    """
    if FAILED_REASONS & set(reasons):
        return "failed"
    if reasons:
        return "degraded"
    return "ok"
```

Rewrite `submit_query`'s try/except (lines 205-236) so every exit stamps an outcome:

```python
    try:
        result = graph.invoke(initial_state, config=config)
        refresh_ttl(session_id)
        set_outcome(_derive_outcome(degradations()))
        return ApiResponse(
            status="ok", data=_payload_from_result(result, session_id, turn_id, trace_id)
        )
    except Exception as e:
        if _checkpointer_active and _is_redis_failure(e):
            logger.error(
                "Checkpointer (Redis) failed mid-invoke (%s) — degrading to a stateless "
                "run; chat_history is lost this turn (memory_degraded=True).", e,
            )
            record_degradation(
                CHECKPOINTER_UNAVAILABLE, announced=True, detail=e.__class__.__name__
            )
            try:
                initial_state["memory_degraded"] = True
                result = _get_stateless_graph().invoke(initial_state)
                report = result.get("report") or {}
                if isinstance(report, dict):
                    report["memory_degraded"] = True
                    result["report"] = report
                set_outcome(_derive_outcome(degradations()))
                return ApiResponse(
                    status="ok", data=_payload_from_result(result, session_id, turn_id, trace_id)
                )
            except Exception as e2:
                logger.exception("Stateless fallback failed after checkpointer outage")
                mark_failed(STATELESS_FALLBACK_FAILED, exc=e2)
                set_outcome("failed")
                return ApiResponse(
                    status="error",
                    errors=[f"Session memory unavailable and fallback failed: {e2}"],
                )
        logger.exception("Graph execution failed")
        mark_failed(GRAPH_INVOKE_FAILED, exc=e)
        set_outcome("failed")
        return ApiResponse(status="error", errors=[str(e)])
```

In `resume_query`, wire both sites. At the `get_state` except (line ~260):

```python
    except Exception as e:
        logger.warning("resume: get_state failed for %s: %s", session_id, e)
        mark_failed(RESUME_STATE_LOAD_FAILED, exc=e, detail=e.__class__.__name__)
        set_outcome("failed")
        return ApiResponse(status="error", errors=["session expired or not found"])
```

At the invoke except (line ~279):

```python
    except Exception as e:
        logger.exception("resume: graph invoke failed for %s", session_id)
        mark_failed(RESUME_FAILED, exc=e)
        set_outcome("failed")
        return ApiResponse(status="error", errors=[str(e)])
```

and on `resume_query`'s success return, before it:

```python
        set_outcome(_derive_outcome(degradations()))
```

> **Note for the reviewer:** `RESUME_STATE_LOAD_FAILED` catches *any* exception and reports "session expired or not found" to the attorney — so a Redis outage during resume currently presents as an expired session. That is a pre-existing product bug. This plan only makes it visible; do not change the message here.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_turn_outcome.py -q`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/query.py tests/test_turn_outcome.py
git commit -m "feat(observability): derive app.outcome on the request root for query and resume"
```

---

## Task 9: Wire the remaining Class 1 (failed) sites

**Files:**
- Modify: `skills/legal_research/legal_research.py:428`
- Modify: `skills/contract_generation/contract_generation.py:77`, `:173`
- Test: `tests/test_turn_outcome.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_turn_outcome.py`:

```python
def test_legal_research_failure_marks_the_skill_span_error(monkeypatch):
    """The Word chat path. An error string reaches the attorney; the span must
    say so."""
    from skills.legal_research import legal_research as lr
    from observability.degradations import LEGAL_RESEARCH_FAILED

    def boom(*a, **k):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(lr, "traced_invoke", boom)

    state = {
        "request": "who signs?",
        "uploaded_docs": [{"text": "AGREEMENT ..."}],
        "task_type": "research",
        "user_id": "u1",
        "document_id": "doc-1",
        "session_id": "s1",
    }
    lr.legal_research(state)

    assert state["llm_response"].startswith("Error: Legal research failed")
    span = spans_by_name("legal_research")[0]
    assert span.status.status_code == StatusCode.ERROR
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_turn_outcome.py -q -k legal_research`
Expected: FAIL — status is `UNSET`

- [ ] **Step 3: Implement**

`skills/legal_research/legal_research.py` — add to imports:

```python
from observability.degradations import LEGAL_RESEARCH_FAILED
from observability.spans import mark_failed
```

At line ~428:

```python
    except Exception as e:
        logger.error("[legal_research] failed: %s", e)
        mark_failed(LEGAL_RESEARCH_FAILED, exc=e, detail=e.__class__.__name__)
        state["llm_response"] = f"Error: Legal research failed — {e}"
        state["proposed_edits"] = []
```

`skills/contract_generation/contract_generation.py` — add to imports:

```python
from observability.degradations import CONTRACT_GENERATION_FAILED
from observability.spans import mark_failed
```

At line ~77 (the direct-LLM revision path):

```python
    except Exception as e:
        logger.error("[contract_generation] revision failed: %s", e)
        mark_failed(CONTRACT_GENERATION_FAILED, exc=e, detail="revision")
        state["llm_response"] = f"Error: Contract revision failed — {e}"
```

At line ~173 (the ReAct agent path):

```python
    except Exception as e:
        logger.error("[contract_generation] agent failed: %s", e)
        mark_failed(CONTRACT_GENERATION_FAILED, exc=e, detail="agent")
        state["llm_response"] = f"Error: Contract generation agent failed — {e}"
```

One code, two paths, distinguished by `detail` — `contract_generation` is a low-traffic surface and two codes would buy filtering nobody needs.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/legal_research/legal_research.py skills/contract_generation/contract_generation.py tests/test_turn_outcome.py
git commit -m "feat(observability): mark legal_research and contract_generation failures on their spans"
```

---

## Task 10: Wire Class 2 (announced) sites

**Files:**
- Modify: `graph/nodes/memory_writer.py:55`, `:72`
- Modify: `graph/checkpointer.py:34`
- Modify: `skills/legal_research/context.py:66`, `:122`, `:136`
- Test: `tests/test_turn_outcome.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_turn_outcome.py`:

```python
def test_audit_write_failure_records_an_announced_degradation(monkeypatch):
    from graph.nodes import memory_writer as mod
    from observability.degradations import AUDIT_WRITE_FAILED

    def boom(**kwargs):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(mod, "write_audit_log", boom)

    out = mod.memory_writer({
        "session_id": "s1", "user_id": "u1", "task_type": "research",
        "request": "q", "llm_response": "a", "report": {},
    })
    # The existing contract is unchanged: the flag travels on the RETURNED report.
    assert out["report"]["memory_degraded"] is True

    events = [e for e in spans_by_name("memory_writer")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [AUDIT_WRITE_FAILED]
    assert events[0].attributes["degradation.announced"] is True
    assert spans_by_name("memory_writer")[0].status.status_code != StatusCode.ERROR
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_turn_outcome.py -q -k audit_write`
Expected: FAIL — `assert [] == ['audit_write_failed']`

- [ ] **Step 3: Implement**

`graph/nodes/memory_writer.py` — add to imports:

```python
from observability.degradations import AUDIT_WRITE_FAILED, REVIEW_PERSIST_FAILED
from observability.spans import record_degradation, traced
```

Line ~55, inside the audit except, after the `logger.error`:

```python
        record_degradation(AUDIT_WRITE_FAILED, announced=True, detail=e.__class__.__name__)
        report_updates["memory_degraded"] = True
```

Line ~72, inside the review except, after the `logger.error`:

```python
        record_degradation(REVIEW_PERSIST_FAILED, announced=True, detail=e.__class__.__name__)
        report_updates["review_persist_error"] = str(e)
```

Leave line ~89 (`append_turn`) alone — it is class 4, best-effort by design.

`graph/checkpointer.py` — add to imports:

```python
from observability.degradations import CHECKPOINTER_UNAVAILABLE
from observability.spans import record_degradation
```

Line ~34:

```python
    except Exception as e:
        logger.warning("Checkpointer unavailable (%s) — running without memory", e)
        record_degradation(CHECKPOINTER_UNAVAILABLE, announced=True, detail="startup")
        return None
```

Leave line ~55 (`refresh_ttl`) alone — considered and deliberately dropped from the vocabulary on 2026-09-16; see the spec's accounting table.

`skills/legal_research/context.py` — add to imports:

```python
from observability.degradations import (
    PRIOR_CONVERSATION_LOAD_FAILED, PRIOR_REVIEW_LOAD_FAILED, SUMMARY_LOAD_FAILED,
)
from observability.spans import record_degradation
```

Add one call inside each of the three excepts, immediately after the existing `logger.error`, keeping every existing line:

| Line | Call |
|---|---|
| `:66` (prior-review load) | `record_degradation(PRIOR_REVIEW_LOAD_FAILED, announced=True, detail=e.__class__.__name__)` |
| `:122` (summary load) | `record_degradation(SUMMARY_LOAD_FAILED, announced=True, detail=e.__class__.__name__)` |
| `:136` (prior-conversation load) | `record_degradation(PRIOR_CONVERSATION_LOAD_FAILED, announced=True, detail=e.__class__.__name__)` |

All three already set `state["memory_degraded"] = True`; do not remove that.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/memory_writer.py graph/checkpointer.py skills/legal_research/context.py tests/test_turn_outcome.py
git commit -m "feat(observability): record the announced degradations the pane already shows"
```

---

## Task 11: Wire Class 3 (silent) sites — RED TEST #2

The class this work exists for. `context.py:195` catches a grounding failure and answers with no playbook and no MSA — a fluent, confident, ungrounded answer with no banner and no record anywhere.

**Files:**
- Modify: `skills/legal_research/context.py:76`, `:195`, `:264`
- Modify: `skills/grounding.py:104`
- Modify: `skills/contract_review/contract_review.py:171`
- Modify: `graph/nodes/intent_router.py:81`
- Modify: `graph/nodes/planner.py:77`
- Test: `tests/test_turn_outcome.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_turn_outcome.py`:

```python
def test_grounding_failure_records_a_SILENT_degradation(monkeypatch):
    """RED TEST #2. The attorney gets a fluent answer with no playbook and no
    MSA, and is told nothing. announced=False is the whole point."""
    from skills.legal_research import context as ctx
    from observability.degradations import CHAT_GROUNDING_FAILED
    from observability.spans import traced, degradations

    def boom(*a, **k):
        raise RuntimeError("qdrant unreachable")

    monkeypatch.setattr(ctx, "load_playbook_bundle", boom)

    @traced("turn")
    def turn():
        playbook, msa = ctx._build_chat_grounding(
            {"filters": {"client_id": "c1"}}, "SOME AGREEMENT TEXT"
        )
        return playbook, msa, degradations()

    playbook, msa, reasons = turn()
    assert playbook == "" and msa == ""          # answers ungrounded, as before
    assert reasons == [CHAT_GROUNDING_FAILED]

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert events[0].attributes["degradation.announced"] is False
```

> If `_build_chat_grounding`'s signature differs, read the real one at `skills/legal_research/context.py:~180` and adjust the call — do not change the function to fit the test.

- [ ] **Step 2: Run it and WATCH IT FAIL**

Run: `uv run pytest tests/test_turn_outcome.py -q -k grounding`
Expected: FAIL — `assert [] == ['chat_grounding_failed']`

- [ ] **Step 3: Implement**

Add one call inside each except, immediately after the existing log line, changing nothing else:

| File:line | Import | Call |
|---|---|---|
| `skills/legal_research/context.py:76` | `REVIEW_RECONCILIATION_FAILED` | `record_degradation(REVIEW_RECONCILIATION_FAILED, announced=False, detail=e.__class__.__name__)` |
| `skills/legal_research/context.py:195` | `CHAT_GROUNDING_FAILED` | `record_degradation(CHAT_GROUNDING_FAILED, announced=False, detail=e.__class__.__name__)` |
| `skills/legal_research/context.py:264` | `COMPRESSIBLE_HISTORY_READ_FAILED` | `record_degradation(COMPRESSIBLE_HISTORY_READ_FAILED, announced=False, detail=e.__class__.__name__)` |
| `skills/grounding.py:104` | `PREFERENCES_LOAD_FAILED` | `record_degradation(PREFERENCES_LOAD_FAILED, announced=False, detail=e.__class__.__name__)` |
| `skills/contract_review/contract_review.py:171` | `MSA_LOOKUP_FAILED` | `record_degradation(MSA_LOOKUP_FAILED, announced=False, detail="sow")` |
| `graph/nodes/intent_router.py:81` | `INTENT_CLASSIFICATION_FAILED` | `record_degradation(INTENT_CLASSIFICATION_FAILED, announced=False, detail="defaulted to research")` |
| `graph/nodes/planner.py:77` | `PLANNING_FAILED` | `record_degradation(PLANNING_FAILED, announced=False, detail=f"fell back to {skill_plan[0]}")` |

Each file needs `from observability.spans import record_degradation` plus the named constant from `observability.degradations`, both at the top of the file.

`contract_review.py:171` catches with a bare `except Exception:` (no `as e`) — keep it that way and use the literal `detail="sow"`, or change to `except Exception as e:` if you prefer the class name. Either is fine; do not change the `logger.exception` call.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/ graph/nodes/intent_router.py graph/nodes/planner.py tests/test_turn_outcome.py
git commit -m "feat(observability): record the silent degradations nobody was ever told about"
```

---

## Task 12: The vocabulary assertion — the hazard tests cannot reach

A perfect `test_record_degradation_adds_event` passes with 21 of 22 sites unwired, because the call sites simply do not exist. Only a static check catches that.

**Files:**
- Create: `scripts/check_degradation_vocabulary.py`
- Modify: `scripts/check.sh`

- [ ] **Step 1: Write the checker**

```python
#!/usr/bin/env python
"""Assert the declared degradation vocabulary and the one actually used agree.

Checked BOTH ways, like evals/baseline.json:
  - declared but never used  -> a site someone forgot to wire
  - used but not declared    -> a typo silently creating a new category

Also rejects string literals at call sites: a literal cannot be checked either
way, so it is a hole in both directions at once.

A unit test structurally cannot do this. test_record_degradation_adds_event
passes with 21 of 22 sites unwired.
"""
from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import observability.degradations as D  # noqa: E402

PACKAGES = ("api", "graph", "skills", "memory")
SEAM_CALLS = {"mark_failed", "record_degradation"}


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def main() -> int:
    used: set[str] = set()
    undeclared: list[str] = []
    literals: list[str] = []

    for package in PACKAGES:
        for path in (REPO / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _called_name(node) not in SEAM_CALLS:
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                where = f"{path.relative_to(REPO)}:{arg.lineno}"
                if isinstance(arg, ast.Constant):
                    literals.append(f"{where}: string literal {arg.value!r}")
                    continue
                name = arg.attr if isinstance(arg, ast.Attribute) else getattr(arg, "id", None)
                if name is None:
                    literals.append(f"{where}: reason is not a named constant")
                    continue
                value = getattr(D, name, None)
                if value is None:
                    undeclared.append(f"{where}: {name} is not declared in observability/degradations.py")
                else:
                    used.add(value)

    never_used = sorted(D.ALL_REASONS - used)
    ok = True
    if literals:
        ok = False
        print("FAIL: reason codes must be constants, not literals:", file=sys.stderr)
        for line in literals:
            print(f"  {line}", file=sys.stderr)
    if undeclared:
        ok = False
        print("FAIL: reason codes used but not declared (typo?):", file=sys.stderr)
        for line in undeclared:
            print(f"  {line}", file=sys.stderr)
    if never_used:
        ok = False
        print("FAIL: reason codes declared but wired at no call site:", file=sys.stderr)
        for code in never_used:
            print(f"  {code}", file=sys.stderr)
        print("      (a site someone forgot to wire — or a code that should be removed)", file=sys.stderr)
    if ok:
        print(f"==> degradation vocabulary: {len(used)}/{len(D.ALL_REASONS)} codes wired")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it — it must pass now that Tasks 7-11 are done**

Run: `uv run python scripts/check_degradation_vocabulary.py`
Expected: `==> degradation vocabulary: 22/22 codes wired`

- [ ] **Step 3: Prove the checker is not vacuous — mutation-verify it**

Temporarily add `UNUSED_CODE = "unused_code"` to `observability/degradations.py` and add it to `SILENT_REASONS`.

Run: `uv run python scripts/check_degradation_vocabulary.py`
Expected: exit 1, `FAIL: reason codes declared but wired at no call site: unused_code`

Then temporarily change one call site to a literal (e.g. `record_degradation("chat_grounding_failed", announced=False)`).
Expected: exit 1, `FAIL: reason codes must be constants, not literals`

**Revert both mutations.** A checker you have not watched fail is a checker you have not tested.

- [ ] **Step 4: Wire it into the gate**

In `scripts/check.sh`, insert after the `backend tests` block (line 14):

```bash
echo "==> degradation vocabulary"
uv run python scripts/check_degradation_vocabulary.py
```

- [ ] **Step 5: Run the full gate**

Run: `bash scripts/check.sh`
Expected: all checks passed

- [ ] **Step 6: Commit**

```bash
git add scripts/check_degradation_vocabulary.py scripts/check.sh
git commit -m "test: two-way gate that every declared reason code is wired at a real call site"
```

---

## Task 13: Compaction root span — RED TEST #3

`/api/compact` is untraced, so `compact_conversation`'s `traced_invoke` becomes its own parentless trace with no user, session or document. The most-debugged subsystem in the repo is the least traceable.

**Files:**
- Modify: `api/routes/compact.py`
- Test: `tests/test_turn_outcome.py`

**Interfaces:**
- Consumes: `_derive_outcome` is *not* importable across route modules cleanly — `post_compact` derives inline with the same two-line rule.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_turn_outcome.py`:

```python
def test_compact_route_gives_the_llm_span_a_parent(monkeypatch):
    """RED TEST #3. Today the compaction LLM span is an orphan root with no
    user, session or document attached to it."""
    from api.routes import compact as mod
    from api.models import CompactRequest

    monkeypatch.setattr(mod, "compact_conversation", lambda *a, **k: {
        "error": "", "reason": "", "segment_id": 1, "freed_chars": 500,
    })

    mod.post_compact(CompactRequest(document_id="doc-1", reclaim_chars=5000), user_id="u1")

    span = spans_by_name("compact")[0]
    assert span.parent is None, "compact is a request root"
    assert span.attributes["app.outcome"] == "ok"


def test_compact_route_marks_failed_when_compaction_errors(monkeypatch):
    import json
    from fastapi import HTTPException
    from api.routes import compact as mod
    from api.models import CompactRequest
    from observability.degradations import COMPACTION_FAILED

    monkeypatch.setattr(mod, "compact_conversation", lambda *a, **k: {
        "error": "the condensed segment could not be saved (OperationalError)",
    })

    with pytest.raises(HTTPException):
        mod.post_compact(CompactRequest(document_id="doc-1", reclaim_chars=5000), user_id="u1")

    span = spans_by_name("compact")[0]
    assert span.attributes["app.outcome"] == "failed"
    assert span.status.status_code == StatusCode.ERROR
    assert COMPACTION_FAILED in json.loads(span.attributes["app.degradations"])
```

- [ ] **Step 2: Run it and WATCH IT FAIL**

Run: `uv run pytest tests/test_turn_outcome.py -q -k compact`
Expected: FAIL — `IndexError: list index out of range` (no span named "compact" exists)

- [ ] **Step 3: Implement**

Rewrite `api/routes/compact.py`'s handler:

```python
from fastapi import APIRouter, Depends, HTTPException

from api.auth import resolve_user_id
from api.models import ApiResponse, CompactRequest
from config import get_settings
from observability.degradations import COMPACTION_FAILED, FAILED_REASONS
from observability.spans import (
    degradations, mark_failed, set_outcome, set_trace_attributes, traced,
)
from skills.legal_research.compaction import compact_conversation

router = APIRouter(prefix="/api")


@router.post("/compact", response_model=ApiResponse)
@traced("compact")
def post_compact(
    body: CompactRequest, user_id: str = Depends(resolve_user_id)
) -> ApiResponse:
    if not get_settings().compaction_enabled:
        raise HTTPException(status_code=403, detail="compaction is disabled")
    document_id = body.document_id.strip()
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")

    set_trace_attributes(
        name="compact",
        user_id=user_id,
        metadata={"document_id": document_id, "reclaim_chars": body.reclaim_chars},
    )

    result = compact_conversation(document_id, user_id, body.reclaim_chars)

    # COMPACTION_FAILED is recorded HERE, not at compaction.py's own excepts:
    # generation is retried twice inside a loop, so recording per-attempt would
    # mark a run failed that then succeeded. Every terminal failure surfaces as
    # result["error"], so this is the one place that cannot double-count.
    if result["error"]:
        mark_failed(COMPACTION_FAILED, detail=result["error"])
        set_outcome("failed")
        raise HTTPException(status_code=500, detail=result["error"])

    # A net-benefit refusal is the guard working correctly, not a degradation —
    # but it disarms the auto latch, so it goes on the span as its own field.
    if result.get("reason"):
        set_trace_attributes(metadata={"compaction.refused_reason": result["reason"]})

    reasons = degradations()
    set_outcome("failed" if FAILED_REASONS & set(reasons) else ("degraded" if reasons else "ok"))
    return ApiResponse(status="ok", data=result)
```

> Decorator order matters: `@router.post` outermost, `@traced` innermost, matching `submit_query` — otherwise FastAPI registers the undecorated function and no span is ever created.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/compact.py tests/test_turn_outcome.py
git commit -m "feat(observability): give /api/compact a request root so its LLM span stops being an orphan"
```

---

## Task 14: Store spans

**Files:**
- Modify: `memory/audit.py:12`, `memory/review_store.py:20`, `:42`, `memory/conversation_store.py:22`, `:39`, `:86`, `memory/conversation_summary.py:48`, `:71`
- Test: `tests/test_turn_outcome.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_turn_outcome.py`:

```python
@pytest.mark.parametrize("span_name", [
    "db.write_audit_log", "db.save_review", "db.load_latest_review",
    "db.append_turn", "db.load_recent", "db.row_lengths_after",
    "db.load_segments", "db.latest_to_id",
])
def test_store_functions_are_named_spans(span_name):
    """Named per operation. Wrapping get_pool().connection() instead would give
    a pile of spans all called 'db'."""
    import memory.audit, memory.conversation_store, memory.conversation_summary, memory.review_store

    fn_name = span_name.removeprefix("db.")
    module = {
        "write_audit_log": memory.audit,
        "save_review": memory.review_store,
        "load_latest_review": memory.review_store,
        "append_turn": memory.conversation_store,
        "load_recent": memory.conversation_store,
        "row_lengths_after": memory.conversation_store,
        "load_segments": memory.conversation_summary,
        "latest_to_id": memory.conversation_summary,
    }[fn_name]
    fn = getattr(module, fn_name)
    assert getattr(fn, "__wrapped__", None) is not None, f"{fn_name} is not @traced"


def test_store_span_is_emitted_on_a_real_call():
    from memory.conversation_summary import latest_to_id

    latest_to_id("doc-nonexistent", "u1")
    assert len(spans_by_name("db.latest_to_id")) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_turn_outcome.py -q -k store`
Expected: FAIL — `assert None is not None` / `write_audit_log is not @traced`

- [ ] **Step 3: Implement**

Add `from observability.spans import traced` to each of the four store modules, then decorate exactly these eight functions (leave every other function in those files undecorated):

| File | Function | Decorator |
|---|---|---|
| `memory/audit.py` | `write_audit_log` | `@traced("db.write_audit_log")` |
| `memory/review_store.py` | `save_review` | `@traced("db.save_review")` |
| `memory/review_store.py` | `load_latest_review` | `@traced("db.load_latest_review")` |
| `memory/conversation_store.py` | `append_turn` | `@traced("db.append_turn")` |
| `memory/conversation_store.py` | `load_recent` | `@traced("db.load_recent")` |
| `memory/conversation_store.py` | `row_lengths_after` | `@traced("db.row_lengths_after")` |
| `memory/conversation_summary.py` | `load_segments` | `@traced("db.load_segments")` |
| `memory/conversation_summary.py` | `latest_to_id` | `@traced("db.latest_to_id")` |

Do **not** decorate `memory/feedback_store.py` — off-turn, has its own store and report script.

> `@traced` uses `functools.wraps`, so `memory_writer`'s existing `monkeypatch.setattr(mod, "write_audit_log", ...)` tests keep working: they replace the module attribute, not the wrapped function.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memory/ tests/test_turn_outcome.py
git commit -m "feat(observability): named spans on the eight store functions on the turn path"
```

---

## Task 15: httpx auto-instrumentation

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py:217` (after `tracing_enabled`)
- Modify: `observability/otel.py`
- Test: `tests/test_otel_init.py`

**Interfaces:**
- Produces: `observability.otel._instrument_libraries() -> None`.
- Produces: config field `otel_instrument_redis: bool = False`.

- [ ] **Step 1: Add the dependency**

In `requirements.txt`, under `# Observability`:

```
opentelemetry-instrumentation-httpx>=0.50b0
```

Then: `uv pip install -r requirements.txt`

> Declared explicitly on purpose. `opentelemetry-instrumentation-*` packages are already in the venv via `chainlit -> literalai -> traceloop-sdk`, but depending on an undeclared transitive is exactly the fragility that sank the "hand off to Traceloop" option in the spec. A chainlit bump could silently blind production.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_otel_init.py`:

```python
def test_instrument_libraries_is_not_called_when_tracing_is_disabled(monkeypatch):
    """Otherwise the test suite would globally patch httpx for every later test."""
    import observability.otel as otel

    called = []
    monkeypatch.setattr(otel, "_instrument_libraries", lambda: called.append(True))
    monkeypatch.setattr(otel, "_initialized", False)
    monkeypatch.setenv("TRACING_ENABLED", "false")
    from config import get_settings
    get_settings.cache_clear()

    otel.init_observability()
    assert called == []


def test_instrument_libraries_never_raises(monkeypatch):
    """One bad instrumentor must not take down the others, or startup."""
    import observability.otel as otel

    class Boom:
        def instrument(self):
            raise RuntimeError("already instrumented")

    monkeypatch.setattr(otel, "HTTPXClientInstrumentor", lambda: Boom())
    otel._instrument_libraries()   # must not raise
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_otel_init.py -q`
Expected: FAIL with `AttributeError: module 'observability.otel' has no attribute '_instrument_libraries'`

- [ ] **Step 4: Implement**

In `config.py`, after `tracing_enabled: bool = True`:

```python
    otel_instrument_redis: bool = False   # the LangGraph checkpointer issues many RediSearch ops per turn; that chatter buries everything else. Flip on only to investigate the checkpointer.
```

In `observability/otel.py`, add to the imports:

```python
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
```

Add the function:

```python
def _instrument_libraries() -> None:
    """Enable the auto-instrumentors we want, each guarded independently.

    httpx is ON: it gives real network timing on every Ollama call (and covers
    qdrant-client's REST calls for free).

    NOT enabled, deliberately:
      - fastapi  — would become the trace root and push app.outcome onto a child
      - langchain/ollama — would emit a second LLM span and a second token count
        per call, against traced_invoke / ollama_usage
    """
    try:
        HTTPXClientInstrumentor().instrument()
        logger.info("httpx instrumentation enabled")
    except Exception as e:
        logger.warning("httpx instrumentation failed: %s", e)

    if get_settings().otel_instrument_redis:
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor
            RedisInstrumentor().instrument()
            logger.info("redis instrumentation enabled")
        except Exception as e:
            logger.warning("redis instrumentation failed: %s", e)
```

> The `RedisInstrumentor` import is inside the `if` on purpose and is the **one** permitted exception to the top-of-file import rule: the package arrives only as an undeclared transitive of chainlit, so a top-level import would make startup depend on it. Note this in the docstring so a future reader does not "fix" it.

Call it in `init_observability()`, immediately after `trace.set_tracer_provider(provider)`:

```python
        trace.set_tracer_provider(provider)
        _instrument_libraries()
        _initialized = True
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Verify against the real stack**

```bash
bash scripts/start.sh
# send one doc-chat turn from the Word pane or Chainlit, then:
open http://localhost:3000     # Langfuse
```

Expected: the `llm_caller` (or `llm`) span has a **child** HTTP CLIENT span for `POST /api/chat`. This settles the one expectation the spec flagged as unverified — that httpx instrumentation catches `llm_caller`'s module-level `httpx.post` (it patches `Client.send`, and `httpx.post` builds a client internally).

If the child span is absent, the module-level shortcut is not covered: wrap the call in an explicit `with httpx.Client() as client: client.post(...)` in `graph/nodes/llm_caller.py` and re-check.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt config.py observability/otel.py tests/test_otel_init.py
git commit -m "feat(observability): httpx auto-instrumentation, redis behind a default-off flag"
```

---

## Task 16: Access fix and documentation

**Files:**
- Modify: `docker-compose.remote.yml`
- Modify: `docs/deploy-vm.md`
- Modify: `docs/wiki.md`
- Modify: `CLAUDE.md`
- Create: `docs/testing-observability.md`

- [ ] **Step 1: Bind Phoenix to loopback**

In `docker-compose.remote.yml`, change the phoenix `ports` entry:

```yaml
    ports:
      # Bound to loopback: Phoenix has NO auth and spans carry full contract
      # text, the governing MSA and the firm's playbook. Reach the UI with
      #   ssh -L 6007:localhost:6007 <vm>   then http://localhost:6007
      - "127.0.0.1:6007:6006"
```

and update the existing `⚠` comment above it to record that the hole is closed rather than open.

- [ ] **Step 2: Document the replacement path**

In `docs/deploy-vm.md`, in the Phoenix/Step 4 section, add prose to this effect
(normal markdown, with the command in its own fenced bash block):

> Phoenix is bound to loopback on the VM — it has no auth and its spans carry
> full contract text, the governing MSA and the firm's playbook. To browse it,
> forward the port over SSH: `ssh -L 6007:localhost:6007 <vm>`, then open
> `http://localhost:6007`.

- [ ] **Step 3: Write the drill doc**

Create `docs/testing-observability.md` covering the four drills, each with exact commands and what to look for:

1. **Ollama down** → `docker stop`/kill the local `ollama serve`, send one review turn. Expect root `status=ERROR`, `app.outcome=failed`, `llm_call_failed` in `app.degradations`, and ERROR on **both** the `query` and `llm_caller` spans.
2. **`app-db` down** → `docker compose stop app-db`, send one turn. Expect `app.outcome=degraded`, an `audit_write_failed` event with `announced=true`, the turn still answers, amber banner in the pane.
3. **Redis down** → **point `OTEL_EXPORTER_OTLP_ENDPOINT` at `http://localhost:6006` (Phoenix) first.** `docker compose stop redis` also takes Langfuse ingestion down — shared Redis — so against Langfuse you would degrade the turn correctly and then have no trace to look at, which reads as broken instrumentation. Expect `app.outcome=degraded`, `checkpointer_unavailable`.
4. **Grounding failure** → temporarily point `QDRANT_URL` at a dead port, send a doc-chat turn on an uploaded SOW. Expect `app.outcome=degraded`, a `chat_grounding_failed` event with `announced=false`, **and nothing whatsoever in the pane**. That last clause is the thesis.

Each drill must state how to restore the stack afterwards.

- [ ] **Step 4: Rewrite the wiki's Observability section**

`docs/wiki.md:335-360` still describes the Langfuse v2 `@observe` SDK and asserts *"Langfuse = agent traces. Phoenix = RAG evals. Don't mix"* — which the OTel migration inverted. Replace with the current architecture (OTel, `observability/spans.py` seam, swappable backend), and add the three new concepts: `app.outcome`, the 22-code vocabulary, and the class-2/3 split.

Add a **Shipped Since Last Update** row, and a **Follow-ups** row for the reranker finding:

> **The reranker has never run.** `RERANKER_ENABLED=true` with `RERANKER_URL=` empty — `rerank()` guards on `reranker_enabled` and empty results but not on an empty URL, so every call reaches `httpx.post("")` → `httpx.UnsupportedProtocol`, caught by the bare `except` at `rag/reranker.py:119` → unreranked results. `.env.remote.example` sets no reranker keys either. Decide whether to configure it or switch it off.

- [ ] **Step 5: Update CLAUDE.md**

The file is at its **150-line cap** and the maintenance rule says consolidate rather than append. The degradation-not-exception lesson **replaces and absorbs** the existing tracing bullet under **Backend**. Draft:

> - **Tracing is OpenTelemetry, not the Langfuse SDK** — seam is `observability/spans.py`, bootstrap `observability/otel.py::init_observability()`, backend swappable via `OTEL_EXPORTER_OTLP_ENDPOINT` (local Langfuse / VM Phoenix). Non-fatal by design. **This app DEGRADES rather than raises — 40 `except Exception` sites — and OTel marks a span ERROR only on an exception that PROPAGATES, so before 2026-09-16 every failed turn looked healthy** (`llm_caller` caught the Ollama failure inside its own LLM span; `submit_query` returned HTTP 200 with `status="error"`). Failures now call `mark_failed`, degradations `record_degradation(announced=…)` — `announced` mirrors `memory_degraded`, i.e. "the attorney was told"; `announced=False` is the silent-quality-loss class. One derived `app.outcome` (ok/degraded/failed) on the request root, from `degradations()` ALONE (one read-back source — never OR in the report flags). Vocabulary is CLOSED (22 codes, `observability/degradations.py`); `scripts/check_degradation_vocabulary.py` checks both directions in `check.sh`, because a green unit test passes with 21 of 22 sites unwired. Class 4 (tracing's own catches) is deliberately uninstrumented. `otel_*` is `@lru_cache`'d ⇒ restart `start.sh`.

- [ ] **Step 6: Run the full gate**

Run: `bash scripts/check.sh`
Expected: all checks passed

- [ ] **Step 7: Run the four drills by hand**

Follow `docs/testing-observability.md` end to end against the local stack. Record the result of each in the doc (date + pass/fail), matching the convention in `docs/testing-compaction.md`.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.remote.yml docs/ CLAUDE.md
git commit -m "docs: trace coverage — drills, wiki rewrite, and bind VM Phoenix to loopback"
```

---

## Done criteria

- [ ] `bash scripts/check.sh` passes, including `22/22 codes wired`
- [ ] The three red tests were each **watched failing** before their implementation
- [ ] The vocabulary checker was mutation-verified in both directions and the mutations reverted
- [ ] All four drills in `docs/testing-observability.md` executed and recorded
- [ ] A turn with Ollama down shows `status=ERROR` + `app.outcome=failed` in a real trace UI
- [ ] A doc-chat turn with Qdrant down shows `chat_grounding_failed` / `announced=false` and **nothing in the pane**
