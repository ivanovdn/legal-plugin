# Tester Feedback Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the legal team a way to report what the agent got wrong, and record the interactions that make those reports measurable as rates.

**Architecture:** Every `/api/query` response gains a `turn_id` (uuid, minted per request) and the genuine 32-hex OTel `trace_id`, which together make one turn addressable. Two new Postgres tables hang off that key — `feedback` (rare, written, carries a full replayable snapshot, loud on failure) and `interaction_event` (frequent, silent Apply/Discard/failure telemetry, quiet on failure). The Word pane grows a flag on each card, one header button, and an in-pane panel.

**Tech Stack:** Python 3.12 · FastAPI · psycopg3 (autocommit pool) · Postgres 17 · OpenTelemetry · React 18 + TypeScript + Vite · hand-rolled `testAssert` frontend harness

**Spec:** [`docs/superpowers/specs/2026-08-14-tester-feedback-capture-design.md`](../specs/2026-08-14-tester-feedback-capture-design.md) (committed at `5126572`)

## Global Constraints

- **Branch:** `feat/tester-feedback-capture`. Never commit this work to `main`.
- **All imports at the top of the file.** No lazy imports inside functions. (CLAUDE.md hard rule 1.)
- **No backwards-compat shims.** Change call sites instead. (CLAUDE.md hard rule 5.)
- **No prompt text changes, no graph changes, no skill changes.** This feature does not alter a single word the agent says.
- **No path by which feedback changes agent behavior.** Explicit spec non-goal.
- **Tests require Docker** — `tests/conftest.py` spins an ephemeral Postgres per session.
- **`uvicorn` does not auto-reload.** After any backend change, restart with `bash scripts/start.sh` before smoke-testing.
- **`get_settings()` is `@lru_cache`'d.** New config values need a restart to take effect; tests must `get_settings.cache_clear()` or patch the module-level `get_settings` name.
- **Loud/quiet write convention** (inherited, not invented): `feedback` writes propagate exceptions like `review_store.save_review`; `interaction_event` writes swallow everything like the caller-side policy around `conversation_store.append_turn`.
- **Patch-target hazard:** modules do `from memory.db import get_pool`, so the name lives on the *importing* module. Patch `memory.feedback_store.get_pool`, never `memory.db.get_pool`, or the patch silently no-ops and the test passes while testing nothing. Verify by mutation.
- **`scripts/check.sh` pins `EXPECTED_PASS_COUNT=191`.** Any change to the frontend assertion count must update it in the same commit or the gate fails.
- **CLAUDE.md is at exactly 150 lines — its own stated cap.** A new line must displace a lower-value one, never extend the file.

---

### Task 1: `current_trace_id()` — the trace-id accessor

**Files:**
- Modify: `observability/spans.py`
- Test: `tests/test_turn_identity.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: `observability.spans.current_trace_id() -> str` — a 32-lowercase-hex OTel trace id, or `""` when no provider is configured / the span is not recording. Never raises.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_turn_identity.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_turn_identity.py -v`
Expected: FAIL — `ImportError: cannot import name 'current_trace_id' from 'observability.spans'`

- [ ] **Step 3: Implement**

Append to `observability/spans.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_turn_identity.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add observability/spans.py tests/test_turn_identity.py
git commit -m "feat(observability): expose the current OTel trace id

The 32-hex id every trace lookup in this project already uses, so a
captured complaint can name the trace that produced it. Best-effort by
the module's contract: "" with no provider, never raises."
```

---

### Task 2: `turn_id` + `trace_id` in the query payload

**Files:**
- Modify: `api/routes/query.py:74` (`_payload_from_result` signature + 3 return dicts), `:127-205` (`submit_query`), `:206-245` (`resume_query`)
- Test: `tests/test_turn_identity.py` (append)

**Interfaces:**
- Consumes: `observability.spans.current_trace_id() -> str` (Task 1)
- Produces: every `/api/query` and `/api/query/{id}/resume` success payload carries `turn_id: str` (uuid4) and `trace_id: str`. `_payload_from_result(result, session_id, turn_id, trace_id)` — **all four parameters required, no defaults.**

**Trap — do not skip this.** `tests/test_query_memory.py` calls `_payload_from_result(result, "sess-1")` positionally at **five** sites (lines 8, 15, 21, 33, 40). Making the new parameters required breaks all five, and the failure reads like a bug in the new code rather than a signature change.

**Update those five call sites** — append `, "t-1", "trace-1"` to each. Do **not** default the parameters to `""`: that is the backwards-compat shim CLAUDE.md rule 5 forbids, and a default on a function that must always receive real values turns a forgotten argument into empty ids instead of an error. (Decided 2026-08-14; the plan originally specified defaults.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_turn_identity.py`:

```python
def _fake_graph(recorder: dict):
    def _invoke(state, config):
        recorder.update(state)
        return {"task_type": "research", "report": {"response": "ok"}}
    return SimpleNamespace(invoke=_invoke)


def _client(monkeypatch, recorder):
    import api.routes.query as q
    from fastapi.testclient import TestClient
    from api.main import app

    monkeypatch.setattr(q, "_get_graph", lambda: _fake_graph(recorder))
    monkeypatch.setattr(q, "refresh_ttl", lambda _s: None)
    return TestClient(app)


def test_query_payload_carries_both_ids(monkeypatch):
    recorder = {}
    c = _client(monkeypatch, recorder)
    data = c.post("/api/query", json={"request": "hi", "task_type": "research"}).json()["data"]
    assert len(data["turn_id"]) == 36, "turn_id should be a uuid4 string"
    assert "trace_id" in data


def test_each_turn_gets_a_distinct_turn_id(monkeypatch):
    """The whole point: one session_id covers many turns, a turn_id covers one."""
    recorder = {}
    c = _client(monkeypatch, recorder)
    body = {"request": "hi", "task_type": "research", "session_id": "same-session"}
    first = c.post("/api/query", json=body).json()["data"]
    second = c.post("/api/query", json=body).json()["data"]
    assert first["session_id"] == second["session_id"] == "same-session"
    assert first["turn_id"] != second["turn_id"]


def test_state_trace_id_is_no_longer_the_session_id(monkeypatch):
    """It used to be `session_id`, which made the field's name a lie."""
    recorder = {}
    c = _client(monkeypatch, recorder)
    data = c.post(
        "/api/query", json={"request": "hi", "task_type": "research", "session_id": "s-1"}
    ).json()["data"]
    assert recorder["trace_id"] == data["trace_id"]
    assert recorder["trace_id"] != "s-1"


def test_interrupt_branch_carries_the_ids():
    """A blocked review is still a turn, and still flaggable."""
    import api.routes.query as q

    class _Interrupt:
        value = {"task_type": "contract_review", "risk_level": "high",
                 "llm_response": "", "risk_flags": [], "review_iterations": 0}

    payload = q._payload_from_result({"__interrupt__": [_Interrupt()]}, "s1", "t-9", "abc")
    assert payload["turn_id"] == "t-9"
    assert payload["trace_id"] == "abc"


def test_legacy_awaiting_review_branch_carries_the_ids():
    import api.routes.query as q
    payload = q._payload_from_result(
        {"awaiting_review": True, "task_type": "contract_review", "report": {}},
        "s1", "t-9", "abc",
    )
    assert payload["turn_id"] == "t-9"
    assert payload["trace_id"] == "abc"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_turn_identity.py -v`
Expected: FAIL — `KeyError: 'turn_id'` on the payload tests, `TypeError: _payload_from_result() takes 2 positional arguments but 4 were given` on the branch tests.

- [ ] **Step 3: Implement**

In `api/routes/query.py`, add to the existing import block:

```python
from observability.spans import current_trace_id
```

Change the signature and all three return dicts:

```python
def _payload_from_result(
    result: dict, session_id: str, turn_id: str, trace_id: str
) -> dict:
```

Then update the five callers in `tests/test_query_memory.py` (lines 8, 15, 21, 33, 40), appending `, "t-1", "trace-1"` to each — e.g.

```python
    payload = q._payload_from_result(
        {"task_type": "research", "report": {}}, "sess-1", "t-1", "trace-1")
```

Add these two keys to **each** of the three returned dicts (the `__interrupt__` branch, the legacy `awaiting_review` branch, and the normal branch), alongside the existing `"session_id": session_id,`:

```python
        "turn_id": turn_id,
        "trace_id": trace_id,
```

In `submit_query`, right after `session_id = body.session_id or str(uuid.uuid4())`:

```python
    # A session_id spans every turn in both Word tabs; a turn_id names one.
    # Feedback and interaction events join on it.
    turn_id = str(uuid.uuid4())
    trace_id = current_trace_id()
```

Add `turn_id` to the trace metadata so a turn is findable in whichever backend is deployed:

```python
        metadata={"user_name": user_name, "turn_id": turn_id},
```

In `initial_state`, replace `"trace_id": session_id,` with:

```python
        "trace_id": trace_id,
```

Update both `_payload_from_result(result, session_id)` call sites in `submit_query` to:

```python
_payload_from_result(result, session_id, turn_id, trace_id)
```

In `resume_query`, mint the same pair immediately after the function's opening (a resume is a distinct turn from the submit that interrupted it, and is separately flaggable):

```python
    turn_id = str(uuid.uuid4())
    trace_id = current_trace_id()
```

and update its `_payload_from_result(result, session_id)` call site the same way.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_turn_identity.py tests/test_query_memory.py -v`
Expected: 13 passed — 8 in `test_turn_identity.py` (3 from Task 1, 5 new) plus the 5 updated `test_query_memory.py` tests.

- [ ] **Step 5: Commit**

```bash
git add api/routes/query.py tests/test_turn_identity.py tests/test_query_memory.py
git commit -m "feat(api): make a turn addressable with turn_id + trace_id

One session_id covers every turn in both Word tabs, so feedback keyed to
it would point at a dozen prompts. Mint a turn_id per request, return it
with the real OTel trace id, and stamp it on the trace.

state[\"trace_id\"] held the session id and was read nowhere — repointed
at the actual trace id so the field stops lying."
```

---

### Task 3: the two stores

**Files:**
- Modify: `memory/db.py` (`_STATEMENTS`)
- Create: `memory/feedback_store.py`
- Modify: `tests/conftest.py:37` (truncate list)
- Test: `tests/test_feedback_store.py` (create)

**Interfaces:**
- Consumes: `memory.db.get_pool`, `memory.db.init_db`
- Produces:
  - `save_feedback(*, turn_id, trace_id, session_id, document_id, attorney_id, user_name, surface, target_kind, target_ref, comment, snapshot: dict | None) -> int` — returns the new row id. **Raises on any failure.**
  - `record_event(*, turn_id, session_id, document_id, attorney_id, surface, action, target_kind="", target_ref="", detail="") -> None` — **never raises.**
  - `record_events(events: list[dict], *, attorney_id: str) -> int` — returns the count written; **never raises.**
  - `recent_feedback(limit: int = 50) -> list[dict]` — newest first; keys `timestamp, turn_id, trace_id, attorney_id, user_name, surface, target_kind, target_ref, comment`.
  - `event_counts() -> list[dict]` — keys `surface, action, count`.
  - `truncate_snapshot(snapshot: dict | None, max_chars: int) -> dict | None` — caps each top-level string value, marking the cut.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feedback_store.py`:

```python
"""Feedback is loud, telemetry is quiet — the same split as review vs conversation.

A lost feedback row is a lie to the attorney, who was told it sent. A lost
interaction event is a rounding error in a rate. They must fail differently.
"""
import pytest

from memory import feedback_store as fs


def _save(**over):
    kw = dict(
        turn_id="t-1", trace_id="abc123", session_id="s-1", document_id="d-1",
        attorney_id="atty-1", user_name="Dana", surface="chat",
        target_kind="edit", target_ref="[__]", comment="wrong field",
        snapshot={"document_text": "NDA ..."},
    )
    kw.update(over)
    return fs.save_feedback(**kw)


def test_feedback_round_trip():
    _save()
    rows = fs.recent_feedback()
    assert len(rows) == 1
    assert rows[0]["comment"] == "wrong field"
    assert rows[0]["trace_id"] == "abc123"
    assert rows[0]["surface"] == "chat"


def test_recent_feedback_is_newest_first():
    _save(comment="older")
    _save(comment="newer")
    assert [r["comment"] for r in fs.recent_feedback()] == ["newer", "older"]


def test_snapshot_survives_the_round_trip():
    _save(snapshot={"document_text": "hello", "request": "who signs?"})
    with fs.get_pool().connection() as conn:
        snap = conn.execute("SELECT snapshot FROM feedback").fetchone()[0]
    assert snap["request"] == "who signs?"


def test_feedback_write_is_loud(monkeypatch):
    """The attorney was told it sent. A silent loss is a lie."""
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(fs, "get_pool", _boom)
    with pytest.raises(RuntimeError):
        _save()


def test_event_round_trip_and_counts():
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_applied")
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_applied")
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_discarded")
    counts = {(c["surface"], c["action"]): c["count"] for c in fs.event_counts()}
    assert counts[("chat", "edit_applied")] == 2
    assert counts[("chat", "edit_discarded")] == 1


def test_event_write_is_quiet(monkeypatch):
    """Telemetry must never break an Apply."""
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(fs, "get_pool", _boom)
    fs.record_event(turn_id="t", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_applied")
    # no exception is the assertion


def test_record_events_batch_counts_what_landed():
    n = fs.record_events(
        [
            {"turn_id": "t", "session_id": "s", "document_id": "d",
             "surface": "chat", "action": "edits_proposed", "detail": "3"},
            {"turn_id": "t", "session_id": "s", "document_id": "d",
             "surface": "chat", "action": "edit_applied"},
        ],
        attorney_id="a",
    )
    assert n == 2
    assert sum(c["count"] for c in fs.event_counts()) == 2


def test_record_events_swallows_a_broken_pool(monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(fs, "get_pool", _boom)
    assert fs.record_events([{"action": "edit_applied"}], attorney_id="a") == 0


def test_truncate_snapshot_caps_and_marks():
    out = fs.truncate_snapshot({"document_text": "x" * 100, "request": "short"}, 10)
    assert out["document_text"].startswith("x" * 10)
    assert "truncated" in out["document_text"]
    assert out["request"] == "short", "short values are untouched"


def test_truncate_snapshot_passes_none_through():
    assert fs.truncate_snapshot(None, 10) is None


def test_tables_are_truncated_between_tests():
    """Guards the conftest trap: a missed table leaks state and greens a lie."""
    assert fs.recent_feedback() == []
    assert fs.event_counts() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_feedback_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.feedback_store'`

- [ ] **Step 3: Add the tables**

In `memory/db.py`, append to `_STATEMENTS`:

```python
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        turn_id TEXT NOT NULL DEFAULT '',
        trace_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        document_id TEXT NOT NULL DEFAULT '',
        attorney_id TEXT NOT NULL,
        user_name TEXT NOT NULL DEFAULT '',
        surface TEXT NOT NULL,
        target_kind TEXT NOT NULL DEFAULT '',
        target_ref TEXT NOT NULL DEFAULT '',
        comment TEXT NOT NULL,
        snapshot JSONB
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_feedback_turn ON feedback (turn_id)",
    """
    CREATE TABLE IF NOT EXISTS interaction_event (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        turn_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        document_id TEXT NOT NULL DEFAULT '',
        attorney_id TEXT NOT NULL,
        surface TEXT NOT NULL,
        action TEXT NOT NULL,
        target_kind TEXT NOT NULL DEFAULT '',
        target_ref TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_turn ON interaction_event (turn_id, action)",
```

Update the `init_db` docstring and `api/main.py`'s lifespan docstring to name five tables instead of three.

- [ ] **Step 4: Fix the conftest trap**

In `tests/conftest.py`, replace the truncate statement:

```python
            "TRUNCATE audit_log, review_store, conversation_store, "
            "feedback, interaction_event RESTART IDENTITY"
```

- [ ] **Step 5: Write the store module**

Create `memory/feedback_store.py`:

```python
"""Postgres stores for tester feedback and interaction telemetry.

Two tables, two failure policies:

- `feedback` holds an attorney's written report plus a replayable input
  snapshot. The write is LOUD (exceptions propagate, like save_review) — the
  attorney is told it sent, so a silent loss is a lie.
- `interaction_event` holds Apply/Discard/failure telemetry. The write is QUIET
  (never raises) — telemetry must never break an Apply.

Two tables rather than one with a `kind` column: ~100:1 volume, a snapshot that
would be NULL on nearly every row, GROUP BY-shaped reads on one and prose reads
on the other, and different lifetimes (events are prunable, feedback is not).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from memory.db import get_pool

logger = logging.getLogger(__name__)

_TRUNCATION_MARK = "\n\n[... truncated at {n} chars ...]"


def truncate_snapshot(snapshot: dict | None, max_chars: int) -> dict | None:
    """Cap each top-level string value, marking the cut. Returns a new dict.

    Per-field rather than per-payload: predictable, and the two fields that can
    actually be large (document_text, assistant_output) are capped independently
    so one does not eat the other's budget.
    """
    if snapshot is None:
        return None
    out: dict = {}
    for key, value in snapshot.items():
        if isinstance(value, str) and max_chars > 0 and len(value) > max_chars:
            out[key] = value[:max_chars] + _TRUNCATION_MARK.format(n=max_chars)
        else:
            out[key] = value
    return out


def save_feedback(
    *,
    turn_id: str,
    trace_id: str,
    session_id: str,
    document_id: str,
    attorney_id: str,
    user_name: str,
    surface: str,
    target_kind: str,
    target_ref: str,
    comment: str,
    snapshot: dict | None,
) -> int:
    """Insert one feedback row and return its id. Raises on any failure."""
    with get_pool().connection() as conn:
        cur = conn.execute(
            """INSERT INTO feedback
               (timestamp, turn_id, trace_id, session_id, document_id, attorney_id,
                user_name, surface, target_kind, target_ref, comment, snapshot)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, trace_id, session_id, document_id, attorney_id,
                user_name, surface, target_kind, target_ref, comment,
                Jsonb(snapshot) if snapshot is not None else None,
            ),
        )
        row_id = cur.fetchone()[0]
    logger.info(
        "Feedback saved: id=%s attorney=%s surface=%s turn=%s trace=%s",
        row_id, attorney_id, surface, turn_id, trace_id,
    )
    return row_id


def _insert_event(
    *,
    turn_id: str,
    session_id: str,
    document_id: str,
    attorney_id: str,
    surface: str,
    action: str,
    target_kind: str,
    target_ref: str,
    detail: str,
) -> None:
    """Raw insert — RAISES. The quiet policy lives in the two callers below, so
    record_events can count what actually landed instead of what it attempted."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO interaction_event
               (timestamp, turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail,
            ),
        )


def record_event(
    *,
    turn_id: str,
    session_id: str,
    document_id: str,
    attorney_id: str,
    surface: str,
    action: str,
    target_kind: str = "",
    target_ref: str = "",
    detail: str = "",
) -> None:
    """Insert one interaction event. NEVER raises — telemetry is not load-bearing."""
    try:
        _insert_event(
            turn_id=turn_id, session_id=session_id, document_id=document_id,
            attorney_id=attorney_id, surface=surface, action=action,
            target_kind=target_kind, target_ref=target_ref, detail=detail,
        )
    except Exception as e:
        logger.warning("interaction_event write failed (%s): %s", action, e)


def record_events(events: list[dict], *, attorney_id: str) -> int:
    """Insert a batch, returning how many actually landed. NEVER raises."""
    written = 0
    for event in events:
        try:
            _insert_event(
                turn_id=event.get("turn_id", ""),
                session_id=event.get("session_id", ""),
                document_id=event.get("document_id", ""),
                attorney_id=attorney_id,
                surface=event.get("surface", ""),
                action=event.get("action", ""),
                target_kind=event.get("target_kind", ""),
                target_ref=event.get("target_ref", ""),
                detail=event.get("detail", ""),
            )
            written += 1
        except Exception as e:
            logger.warning("interaction_event batch row failed: %s", e)
    return written


def recent_feedback(limit: int = 50) -> list[dict]:
    """Most recent feedback rows, newest first. Snapshot deliberately omitted."""
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT timestamp, turn_id, trace_id, attorney_id, user_name,
                      surface, target_kind, target_ref, comment
               FROM feedback ORDER BY id DESC LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    keys = ("timestamp", "turn_id", "trace_id", "attorney_id", "user_name",
            "surface", "target_kind", "target_ref", "comment")
    return [dict(zip(keys, r)) for r in rows]


def event_counts() -> list[dict]:
    """Interaction counts grouped by surface and action — the denominators."""
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT surface, action, COUNT(*) FROM interaction_event
               GROUP BY surface, action ORDER BY surface, action"""
        )
        rows = cur.fetchall()
    return [{"surface": r[0], "action": r[1], "count": r[2]} for r in rows]
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_feedback_store.py -v`
Expected: 11 passed

- [ ] **Step 7: Verify the conftest fix by mutation**

Temporarily remove `feedback, interaction_event` from the conftest TRUNCATE, then run `uv run pytest tests/test_feedback_store.py -v`.
Expected: `test_feedback_round_trip` or `test_recent_feedback_is_newest_first` FAILS on a leaked row. **Restore the conftest line and re-run to green.**

This step is not optional. A green suite with a silently-ignored patch or a missed truncation is the exact failure mode that produced three vacuous tests on 2026-08-13.

- [ ] **Step 8: Commit**

```bash
git add memory/db.py memory/feedback_store.py tests/conftest.py tests/test_feedback_store.py api/main.py
git commit -m "feat(memory): feedback + interaction_event stores

Feedback writes are loud (the attorney was told it sent); event writes are
quiet (telemetry must never break an Apply) — the same split review_store
and conversation_store already use.

Two tables rather than one with a kind column: ~100:1 volume, a snapshot
NULL on nearly every row, GROUP BY reads on one and prose reads on the
other, and different lifetimes.

conftest's TRUNCATE list is hardcoded; both tables added there, verified
by mutation."
```

---

### Task 4: config, request models, and the two routes

**Files:**
- Modify: `config.py` (two settings), `api/models.py` (two request models), `api/main.py` (register router)
- Create: `api/routes/feedback.py`
- Test: `tests/test_feedback_api.py` (create)

**Interfaces:**
- Consumes: `memory.feedback_store.{save_feedback, record_events, truncate_snapshot}` (Task 3); `api.auth.{resolve_user_id, resolve_user_name}`
- Produces:
  - `POST /api/feedback` → `{"status":"ok","data":{"saved":true,"id":<int>}}`; **500** on a store failure; **403** when disabled; **400** on an empty comment.
  - `POST /api/events` → `{"status":"ok","data":{"recorded":<int>}}`; **always 200.**
  - `config.Settings.feedback_enabled: bool`, `config.Settings.feedback_snapshot_max_chars: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feedback_api.py`:

```python
"""The route's job is identity and failure policy — the store does the rest.

attorney_id must come from the identity seam and never from the body, so the
SSO cutover reaches feedback for free. Feedback failures must reach the
attorney; event failures must not reach anyone.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app
from memory import feedback_store as fs


@pytest.fixture
def client(monkeypatch):
    fake = SimpleNamespace(feedback_enabled=True, feedback_snapshot_max_chars=50)
    monkeypatch.setattr("api.routes.feedback.get_settings", lambda: fake)
    # raise_server_exceptions=False so a 500 arrives as a response, not a raise.
    return TestClient(app, raise_server_exceptions=False), fake


def _body(**over):
    b = {"turn_id": "t-1", "trace_id": "abc", "session_id": "s-1",
         "document_id": "d-1", "surface": "chat", "target_kind": "edit",
         "target_ref": "[__]", "comment": "filled the wrong field",
         "snapshot": {"document_text": "NDA"}}
    b.update(over)
    return b


def test_feedback_round_trip(client):
    c, _ = client
    r = c.post("/api/feedback", json=_body(), headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200
    assert r.json()["data"]["saved"] is True
    rows = fs.recent_feedback()
    assert rows[0]["comment"] == "filled the wrong field"
    assert rows[0]["attorney_id"] == "atty-1"


def test_attorney_id_comes_from_the_seam_not_the_body(client):
    """Otherwise SSO would not reach feedback, and a client could spoof."""
    c, _ = client
    c.post("/api/feedback", json=_body(attorney_id="somebody-else"),
           headers={"X-User-ID": "atty-real"})
    assert fs.recent_feedback()[0]["attorney_id"] == "atty-real"


def test_user_name_is_captured(client):
    c, _ = client
    c.post("/api/feedback", json=_body(),
           headers={"X-User-ID": "atty-1", "X-User-Name": "Dana"})
    assert fs.recent_feedback()[0]["user_name"] == "Dana"


def test_empty_comment_rejected(client):
    c, _ = client
    r = c.post("/api/feedback", json=_body(comment="   "), headers={"X-User-ID": "a"})
    assert r.status_code == 400


def test_snapshot_is_truncated_at_the_configured_cap(client):
    c, _ = client
    c.post("/api/feedback", json=_body(snapshot={"document_text": "x" * 500}),
           headers={"X-User-ID": "a"})
    with fs.get_pool().connection() as conn:
        snap = conn.execute("SELECT snapshot FROM feedback").fetchone()[0]
    assert len(snap["document_text"]) < 500
    assert "truncated" in snap["document_text"]


def test_feedback_failure_is_loud(client, monkeypatch):
    """The attorney must learn it did not send."""
    c, _ = client
    def _boom(**_kw):
        raise RuntimeError("db down")
    monkeypatch.setattr("api.routes.feedback.save_feedback", _boom)
    assert c.post("/api/feedback", json=_body(), headers={"X-User-ID": "a"}).status_code == 500


def test_feedback_disabled_is_403(client):
    c, fake = client
    fake.feedback_enabled = False
    assert c.post("/api/feedback", json=_body(), headers={"X-User-ID": "a"}).status_code == 403


def test_events_round_trip(client):
    c, _ = client
    r = c.post("/api/events", json={"events": [
        {"turn_id": "t", "session_id": "s", "document_id": "d",
         "surface": "chat", "action": "edits_proposed", "detail": "3"},
        {"turn_id": "t", "session_id": "s", "document_id": "d",
         "surface": "chat", "action": "edit_discarded"},
    ]}, headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200 and r.json()["data"]["recorded"] == 2
    assert sum(x["count"] for x in fs.event_counts()) == 2


def test_events_failure_is_quiet(client, monkeypatch):
    """A telemetry outage must be invisible to the pane."""
    c, _ = client
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("memory.feedback_store.get_pool", _boom)
    r = c.post("/api/events", json={"events": [{"action": "edit_applied"}]},
               headers={"X-User-ID": "a"})
    assert r.status_code == 200


def test_events_disabled_is_still_200(client):
    c, fake = client
    fake.feedback_enabled = False
    r = c.post("/api/events", json={"events": [{"action": "edit_applied"}]},
               headers={"X-User-ID": "a"})
    assert r.status_code == 200 and r.json()["data"]["recorded"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_feedback_api.py -v`
Expected: FAIL — every test 404s (`assert 404 == 200`), because the router does not exist.

- [ ] **Step 3: Add config**

In `config.py`, after the preferences block:

```python
    # Tester feedback capture — written reports + interaction telemetry
    feedback_enabled: bool = True               # False = /api/feedback 403s, /api/events discards
    feedback_snapshot_max_chars: int = 200000   # per-field cap on the replay snapshot, truncation-marked
```

- [ ] **Step 4: Add request models**

In `api/models.py`, after `PreferencesUpdate`:

```python
class FeedbackSubmission(BaseModel):
    """An attorney's written report about one turn.

    attorney_id is deliberately absent — identity comes from the auth seam.
    """
    turn_id: str = Field("", description="The turn being reported on")
    trace_id: str = Field("", description="OTel trace id for that turn, when tracing is on")
    session_id: str = Field("", description="Pane session the turn belongs to")
    document_id: str = Field("", description="Stable document id")
    surface: str = Field("general", description="findings | chat | general")
    target_kind: str = Field("", description="finding | edit | reply; empty for unattached feedback")
    target_ref: str = Field("", description="Issue id, clause, or target-text excerpt")
    comment: str = Field(..., description="The attorney's own words")
    snapshot: dict | None = Field(None, description="Replayable input context; capped server-side")


class InteractionEvent(BaseModel):
    """One recorded interaction with an assistant suggestion."""
    turn_id: str = ""
    session_id: str = ""
    document_id: str = ""
    surface: str = ""
    action: str
    target_kind: str = ""
    target_ref: str = ""
    detail: str = Field("", description="Error text on failures; the count on per-turn counters")


class InteractionEventBatch(BaseModel):
    """A burst of interactions in one request."""
    events: list[InteractionEvent] = Field(default_factory=list)
```

- [ ] **Step 5: Write the router**

Create `api/routes/feedback.py`:

```python
"""Tester feedback endpoints — written reports and interaction telemetry.

Identity comes from resolve_user_id/resolve_user_name, never the request body,
so the O365 SSO cutover reaches feedback with no change here.

The two endpoints fail differently on purpose. /api/feedback surfaces a store
failure as a 500 because the attorney was told their report sent. /api/events
always returns 200 because a telemetry outage must be invisible in the pane.
"""
from fastapi import APIRouter, Depends, HTTPException

from api.auth import resolve_user_id, resolve_user_name
from api.models import ApiResponse, FeedbackSubmission, InteractionEventBatch
from config import get_settings
from memory.feedback_store import record_events, save_feedback, truncate_snapshot

router = APIRouter(prefix="/api")


@router.post("/feedback", response_model=ApiResponse)
def post_feedback(
    body: FeedbackSubmission,
    user_id: str = Depends(resolve_user_id),
    user_name: str = Depends(resolve_user_name),
) -> ApiResponse:
    settings = get_settings()
    if not settings.feedback_enabled:
        raise HTTPException(status_code=403, detail="feedback is disabled")
    if not body.comment.strip():
        raise HTTPException(status_code=400, detail="comment is empty")
    # No try/except: a store failure must reach the attorney as a 500.
    row_id = save_feedback(
        turn_id=body.turn_id,
        trace_id=body.trace_id,
        session_id=body.session_id,
        document_id=body.document_id,
        attorney_id=user_id,
        user_name=user_name,
        surface=body.surface,
        target_kind=body.target_kind,
        target_ref=body.target_ref,
        comment=body.comment,
        snapshot=truncate_snapshot(body.snapshot, settings.feedback_snapshot_max_chars),
    )
    return ApiResponse(status="ok", data={"saved": True, "id": row_id})


@router.post("/events", response_model=ApiResponse)
def post_events(
    body: InteractionEventBatch,
    user_id: str = Depends(resolve_user_id),
) -> ApiResponse:
    if not get_settings().feedback_enabled:
        return ApiResponse(status="ok", data={"recorded": 0})
    written = record_events([e.model_dump() for e in body.events], attorney_id=user_id)
    return ApiResponse(status="ok", data={"recorded": written})
```

- [ ] **Step 6: Register the router**

In `api/main.py`, alongside the other route imports and `include_router` calls:

```python
from api.routes.feedback import router as feedback_router
```
```python
app.include_router(feedback_router)
```

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/test_feedback_api.py -v`
Expected: 10 passed

- [ ] **Step 8: Run the whole backend suite**

Run: `uv run pytest tests/ -q`
Expected: all green — 439 pre-existing + 29 new = 468 passed.

- [ ] **Step 9: Commit**

```bash
git add config.py api/models.py api/routes/feedback.py api/main.py tests/test_feedback_api.py
git commit -m "feat(api): POST /api/feedback and POST /api/events

Identity from the auth seam, never the body — so SSO reaches feedback for
free and a client cannot spoof an attorney id.

The two endpoints fail differently on purpose: feedback 500s so the
attorney learns it did not send, events always 200 so a telemetry outage
is invisible in the pane."
```

---

### Task 5: the client API module

**Files:**
- Create: `clients/word/src/feedback.ts`, `clients/word/src/feedback.test.ts`
- Modify: `clients/word/src/api.ts:10-24` (`QueryResponse.data`), `scripts/check.sh` (`EXPECTED_PASS_COUNT`)

**Interfaces:**
- Consumes: `attorneyIdentity.userHeaders()`
- Produces:
  - `interface TurnRef { turnId: string; traceId: string; sessionId: string; documentId: string }`
  - `interface FlagTarget { turn: TurnRef; surface: string; targetKind: string; targetRef: string; snapshot: Record<string, unknown> }`
  - `sendFeedback(target: FlagTarget, comment: string): Promise<void>` — **throws** on a non-ok response.
  - `recordEvent(turn: TurnRef, surface: string, action: string, extra?: {targetKind?: string; targetRef?: string; detail?: string}): void` — returns `void`, **never throws**, fire-and-forget.
  - `buildSnapshot(parts: {documentText?: string; assistantOutput?: string; request?: string; contractType?: string; target?: unknown}): Record<string, unknown>`
  - `requestFlag(target: FlagTarget): void` / `onFlagRequested(fn: ((t: FlagTarget) => void) | null): void`

- [ ] **Step 1: Write the failing test**

Create `clients/word/src/feedback.test.ts`:

```ts
// Feedback client contracts. Run with: npx tsx src/feedback.test.ts
import { buildSnapshot, onFlagRequested, recordEvent, requestFlag, sendFeedback } from "./feedback";
import { pass } from "./testAssert";

(globalThis as { localStorage?: unknown }).localStorage = {
  getItem: () => null,
  setItem: () => {},
};

const TURN = { turnId: "t-1", traceId: "abc", sessionId: "s-1", documentId: "d-1" };
const TARGET = { turn: TURN, surface: "chat", targetKind: "edit", targetRef: "[__]", snapshot: {} };

// --- recordEvent must never break the pane -------------------------------
(globalThis as { fetch?: unknown }).fetch = () => Promise.reject(new Error("offline"));
let threw = false;
try {
  recordEvent(TURN, "chat", "edit_applied");
} catch {
  threw = true;
}
pass(!threw, "recordEvent swallows a rejected fetch");

(globalThis as { fetch?: unknown }).fetch = () => {
  throw new Error("fetch itself exploded");
};
threw = false;
try {
  recordEvent(TURN, "chat", "edit_applied");
} catch {
  threw = true;
}
pass(!threw, "recordEvent swallows a synchronous fetch throw");

// --- sendFeedback must surface failure ------------------------------------
(globalThis as { fetch?: unknown }).fetch = async () => ({
  ok: false,
  status: 500,
  statusText: "Server Error",
});
let caught = false;
await sendFeedback(TARGET, "wrong field").catch(() => {
  caught = true;
});
pass(caught, "sendFeedback propagates a non-ok response");

let sentBody: Record<string, unknown> = {};
(globalThis as { fetch?: unknown }).fetch = async (_u: string, init: { body: string }) => {
  sentBody = JSON.parse(init.body);
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};
await sendFeedback(TARGET, "wrong field");
pass(sentBody.turn_id === "t-1" && sentBody.comment === "wrong field", "sendFeedback posts the turn id and comment");

// --- buildSnapshot ---------------------------------------------------------
const snap = buildSnapshot({ documentText: "NDA", request: "who signs?" });
pass(snap.document_text === "NDA" && snap.request === "who signs?", "buildSnapshot uses snake_case keys");
pass(!("assistant_output" in snap), "buildSnapshot omits absent parts");

// --- the flag channel ------------------------------------------------------
let received: string | null = null;
onFlagRequested((t) => {
  received = t.targetRef;
});
requestFlag(TARGET);
pass(received === "[__]", "requestFlag reaches the registered handler");

onFlagRequested(null);
received = null;
requestFlag(TARGET);
pass(received === null, "requestFlag is a no-op with no handler registered");
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd clients/word && npx tsx src/feedback.test.ts`
Expected: FAIL — `Cannot find module './feedback'`

- [ ] **Step 3: Implement**

Create `clients/word/src/feedback.ts`:

```ts
// Tester feedback client: a written report, and silent interaction telemetry.
//
// recordEvent returns void rather than a Promise on purpose — a caller cannot
// accidentally await it, and a telemetry outage can never surface in the pane
// or break an Apply. sendFeedback is the opposite: it throws, because the
// attorney is watching for confirmation that their report sent.
import { userHeaders } from "./attorneyIdentity";

export interface TurnRef {
  turnId: string;
  traceId: string;
  sessionId: string;
  documentId: string;
}

export interface FlagTarget {
  turn: TurnRef;
  /** "findings" | "chat" | "general" */
  surface: string;
  /** "finding" | "edit" | "reply" | "" */
  targetKind: string;
  targetRef: string;
  snapshot: Record<string, unknown>;
}

export const EMPTY_TURN: TurnRef = { turnId: "", traceId: "", sessionId: "", documentId: "" };

/** Assemble the replayable context. Absent parts are omitted, not sent empty. */
export function buildSnapshot(parts: {
  documentText?: string;
  assistantOutput?: string;
  request?: string;
  contractType?: string;
  target?: unknown;
}): Record<string, unknown> {
  const snap: Record<string, unknown> = {};
  if (parts.documentText) snap.document_text = parts.documentText;
  if (parts.assistantOutput) snap.assistant_output = parts.assistantOutput;
  if (parts.request) snap.request = parts.request;
  if (parts.contractType) snap.contract_type_detected = parts.contractType;
  if (parts.target !== undefined) snap.target = parts.target;
  return snap;
}

export async function sendFeedback(target: FlagTarget, comment: string): Promise<void> {
  const res = await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify({
      turn_id: target.turn.turnId,
      trace_id: target.turn.traceId,
      session_id: target.turn.sessionId,
      document_id: target.turn.documentId,
      surface: target.surface,
      target_kind: target.targetKind,
      target_ref: target.targetRef,
      comment,
      snapshot: target.snapshot,
    }),
  });
  if (!res.ok) {
    if (res.status === 403) throw new Error("Feedback is currently disabled on the server.");
    throw new Error(`Backend returned ${res.status} ${res.statusText}`);
  }
}

export function recordEvent(
  turn: TurnRef,
  surface: string,
  action: string,
  extra: { targetKind?: string; targetRef?: string; detail?: string } = {},
): void {
  try {
    void fetch("/api/events", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...userHeaders() },
      body: JSON.stringify({
        events: [
          {
            turn_id: turn.turnId,
            session_id: turn.sessionId,
            document_id: turn.documentId,
            surface,
            action,
            target_kind: extra.targetKind ?? "",
            target_ref: (extra.targetRef ?? "").slice(0, 200),
            detail: (extra.detail ?? "").slice(0, 500),
          },
        ],
      }),
    }).catch(() => {
      /* telemetry must never surface */
    });
  } catch {
    /* fetch itself unavailable — still never surface */
  }
}

// A one-slot channel from any card to the single FeedbackPanel that App owns.
// Deliberately a module singleton rather than React context or prop drilling:
// there is exactly one pane and one panel, the cards sit four levels down, and
// this codebase already uses module-level singletons for identity.
let flagHandler: ((t: FlagTarget) => void) | null = null;

export function onFlagRequested(fn: ((t: FlagTarget) => void) | null): void {
  flagHandler = fn;
}

export function requestFlag(target: FlagTarget): void {
  flagHandler?.(target);
}
```

- [ ] **Step 4: Add the response fields**

In `clients/word/src/api.ts`, inside `QueryResponse["data"]`, after `session_id`:

```ts
    turn_id?: string;
    trace_id?: string;
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd clients/word && npx tsx src/feedback.test.ts && npx tsc --noEmit`
Expected: 8 `PASS:` lines, no type errors.

- [ ] **Step 6: Update the assertion gate**

In `scripts/check.sh`, change `EXPECTED_PASS_COUNT=191` to:

```bash
EXPECTED_PASS_COUNT=199
```

(191 existing + 8 new.)

- [ ] **Step 7: Run the full gate**

Run: `bash scripts/check.sh`
Expected: `all checks passed`, with `199/199 PASS`.

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/feedback.ts clients/word/src/feedback.test.ts clients/word/src/api.ts scripts/check.sh
git commit -m "feat(word): feedback client with a never-throwing event path

recordEvent returns void, not a Promise — a caller cannot await it and a
telemetry outage cannot surface in the pane. sendFeedback throws, because
the attorney is watching for confirmation.

The flag channel is a module singleton: one pane, one panel, cards four
levels down, and identity already works this way here."
```

---

### Task 6: turn retention and event instrumentation

**Files:**
- Modify: `clients/word/src/components/ChatTab.tsx`, `FindingsTab.tsx`, `FindingCard.tsx`, `EditProposalCard.tsx`, `PreferenceSuggestionCard.tsx`

**Interfaces:**
- Consumes: `feedback.{TurnRef, EMPTY_TURN, recordEvent}` (Task 5); `docIdentity.resolveDocumentId()`
- Produces: `ChatMessage` gains `turn?: TurnRef`. `FindingCard` and `EditProposalCard` gain a required `turn: TurnRef` prop. `PreferenceSuggestionCard` gains `turn: TurnRef`.

**No unit tests.** The frontend harness (`testAssert` + `tsx`) runs plain modules — there is no DOM or React renderer, and adding one is out of scope. This task is gated by `npx tsc --noEmit` and the sideload smoke in Task 8. Keep logic out of the components: anything worth testing belongs in `feedback.ts`.

- [ ] **Step 1: Thread the turn through ChatTab**

In `clients/word/src/components/ChatTab.tsx`:

Add to the imports:
```ts
import { buildSnapshot, recordEvent, requestFlag, type TurnRef, EMPTY_TURN } from "../feedback";
import { resolveDocumentId } from "../docIdentity";
```

Add to `ChatMessage`:
```ts
  /** Identifies the backend turn that produced this reply — for flags and events. */
  turn?: TurnRef;
```

Inside `send()`, after `const res = await chatQuery(...)` and the error check:
```ts
      const turn: TurnRef = {
        turnId: res.data?.turn_id ?? "",
        traceId: res.data?.trace_id ?? "",
        sessionId,
        documentId: await resolveDocumentId(),
      };
```

Set it on the appended assistant message (`turn,`), and immediately after `setMessages`, record the per-turn denominators:
```ts
      // Denominators. Without these you only ever see cards the attorney acted
      // on, and "ignored" is a different signal from "discarded".
      recordEvent(turn, "chat", "edits_proposed", { detail: String(proposedEdits.length) });
      recordEvent(turn, "chat", "preferences_suggested", {
        detail: String(proposedPreferences.length),
      });
```

These sit in the `send()` handler, not in render, so React StrictMode double-rendering cannot double-count them.

Pass the turn down when rendering cards:
```ts
            {m.proposedEdits?.map((proposal, j) => (
              <EditProposalCard key={`${i}-${j}`} proposal={proposal} turn={m.turn ?? EMPTY_TURN} />
            ))}
            {m.proposedPreferences?.map((p, j) => (
              <PreferenceSuggestionCard
                key={`pref-${i}-${j}`}
                text={p}
                turn={m.turn ?? EMPTY_TURN}
                onAdded={onPreferenceAdded}
              />
            ))}
```

Add the flag control to each assistant message, just after `<div className="chat-content">`:
```ts
            {m.role === "assistant" && (
              <button
                className="flag-button"
                title="Report a problem with this reply"
                onClick={() =>
                  requestFlag({
                    turn: m.turn ?? EMPTY_TURN,
                    surface: "chat",
                    targetKind: "reply",
                    targetRef: m.content.slice(0, 120),
                    snapshot: buildSnapshot({
                      assistantOutput: m.rawResponse ?? m.content,
                      request: messages[i - 1]?.content,
                    }),
                  })
                }
              >
                ⚑
              </button>
            )}
```

- [ ] **Step 2: Thread the turn through FindingsTab**

In `clients/word/src/components/FindingsTab.tsx`:

```ts
import { recordEvent, type TurnRef, EMPTY_TURN } from "../feedback";
import { resolveDocumentId } from "../docIdentity";
```

Add local state:
```ts
  const [turn, setTurn] = useState<TurnRef>(EMPTY_TURN);
```

In `onReview()`, after `setResult(parsed)`:
```ts
      const reviewTurn: TurnRef = {
        turnId: res.data?.turn_id ?? "",
        traceId: res.data?.trace_id ?? "",
        sessionId,
        documentId: await resolveDocumentId(),
      };
      setTurn(reviewTurn);
      recordEvent(reviewTurn, "findings", "findings_rendered", {
        detail: String(parsed.findings.length),
      });
```

Pass `turn={turn}` to every `<FindingCard ... />`.

- [ ] **Step 3: Instrument EditProposalCard**

In `clients/word/src/components/EditProposalCard.tsx`:

```ts
import { buildSnapshot, recordEvent, requestFlag, type TurnRef } from "../feedback";
```

Change the props to `{ proposal, turn }: { proposal: EditProposal; turn: TurnRef }`.

In `onApply`, after the result:
```ts
    const ref = proposal.target_text ?? proposal.anchor_text ?? "";
    if (res.ok) {
      setStatus({ kind: "applied" });
      recordEvent(turn, "chat", "edit_applied", { targetKind: proposal.action, targetRef: ref });
    } else {
      setStatus({ kind: "error", message: res.error });
      // The error string is the point: this is the only field measurement of
      // body.search matching we have ever had.
      recordEvent(turn, "chat", "edit_failed", {
        targetKind: proposal.action, targetRef: ref, detail: res.error,
      });
    }
```

In `onDiscard`, before `setStatus`:
```ts
    recordEvent(turn, "chat", "edit_discarded", {
      targetKind: proposal.action,
      targetRef: proposal.target_text ?? proposal.anchor_text ?? "",
    });
```

In `onJump`, inside the `if (!res.ok)` branch:
```ts
      if (res.notFound) {
        recordEvent(turn, "chat", "edit_jump_notfound", { targetRef: jumpTarget });
      }
```

Add a flag button into `.card-actions`:
```ts
        <button
          className="flag-button"
          title="Report a problem with this edit"
          onClick={() =>
            requestFlag({
              turn,
              surface: "chat",
              targetKind: "edit",
              targetRef: proposal.target_text ?? proposal.anchor_text ?? "",
              snapshot: buildSnapshot({ target: proposal }),
            })
          }
        >
          ⚑
        </button>
```

- [ ] **Step 4: Instrument FindingCard**

In `clients/word/src/components/FindingCard.tsx`:

```ts
import { buildSnapshot, recordEvent, requestFlag, type TurnRef } from "../feedback";
```

Change the props to `{ finding, turn }: { finding: Finding; turn: TurnRef }`.

In `onAccept`, after the result:
```ts
    if (res.ok) {
      setRedline({ kind: "done", message: "Applied ✓ — see Track Changes" });
      recordEvent(turn, "findings", "redline_applied", { targetRef: finding.issueId || finding.clause });
    } else {
      setRedline({ kind: "error", message: res.error });
      recordEvent(turn, "findings", "redline_failed", {
        targetRef: finding.issueId || finding.clause, detail: res.error,
      });
    }
```

In `onShow`, on success:
```ts
      recordEvent(turn, "findings", "finding_commented", {
        targetRef: finding.issueId || finding.clause,
      });
```

In `onJump`, when `res.notFound`:
```ts
      recordEvent(turn, "findings", "finding_jump_notfound", {
        targetRef: finding.issueId || finding.clause, detail: res.error,
      });
```

Add a flag button to the card's action row:

```tsx
        <button
          className="flag-button"
          title="Report a problem with this finding"
          onClick={() =>
            requestFlag({
              turn,
              surface: "findings",
              targetKind: "finding",
              targetRef: finding.issueId || finding.clause,
              snapshot: buildSnapshot({ target: finding }),
            })
          }
        >
          ⚑
        </button>
```

- [ ] **Step 5: Instrument PreferenceSuggestionCard**

In `clients/word/src/components/PreferenceSuggestionCard.tsx`, add `turn: TurnRef` to `Props`, import `recordEvent`, and in `add()` after `setState("added")`:

```ts
      recordEvent(turn, "chat", "preference_added", { targetRef: text.slice(0, 200) });
```

There is no dismiss button on this card, so an ignored suggestion is only visible as `preferences_suggested` (Step 1) with no matching `preference_added`. That counter is the sole source of the signal, not a redundancy.

- [ ] **Step 6: Typecheck**

Run: `cd clients/word && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add clients/word/src/components/
git commit -m "feat(word): record what attorneys do with our suggestions

Apply, Discard, and every failure, keyed to the turn that proposed them,
plus per-turn counters so 'ignored' is distinguishable from 'discarded'.

edit_failed carries the error string. findClauseRange, searchCandidates,
the 85% guard, wildcard escaping, tab-segment reduction and multi-line
collapse were all built against anecdotes; this is the first field
measurement of whether the matcher actually finds things."
```

---

### Task 7: the feedback panel

**Files:**
- Create: `clients/word/src/components/FeedbackPanel.tsx`
- Modify: `clients/word/src/App.tsx`, `clients/word/src/styles.css`

**Interfaces:**
- Consumes: `feedback.{FlagTarget, onFlagRequested, sendFeedback, buildSnapshot, EMPTY_TURN}` (Task 5)
- Produces: `<FeedbackPanel target={target} onClose={() => void} />`

- [ ] **Step 1: Write the panel**

Create `clients/word/src/components/FeedbackPanel.tsx`:

```tsx
import { useState } from "react";
import { sendFeedback, type FlagTarget } from "../feedback";

// In-pane, deliberately NOT Office.context.ui.displayDialogAsync: a dialog is a
// separate window with its own origin and message passing, and this codebase
// already records that the Mac webview is unreliable for that class of thing
// (see the window.confirm note on FinalizeBar).
type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "sent" }
  | { kind: "error"; message: string };

const WHAT_IS_ATTACHED: Record<string, string> = {
  finding: "this finding",
  edit: "this proposed edit",
  reply: "this reply",
};

export default function FeedbackPanel({
  target,
  onClose,
}: {
  target: FlagTarget;
  onClose: () => void;
}) {
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const onSend = async () => {
    if (!comment.trim() || status.kind === "sending") return;
    setStatus({ kind: "sending" });
    try {
      await sendFeedback(target, comment.trim());
      setStatus({ kind: "sent" });
      setTimeout(onClose, 1200);
    } catch (e) {
      setStatus({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };

  const item = WHAT_IS_ATTACHED[target.targetKind] ?? "this document";

  return (
    <div className="feedback-panel">
      <div className="feedback-header">
        <strong>What went wrong?</strong>
        <button className="link" onClick={onClose} aria-label="Close feedback">
          ✕
        </button>
      </div>

      <textarea
        className="feedback-input"
        rows={4}
        autoFocus
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder={
          target.targetKind
            ? "e.g. this filled the counterparty's name into our signature block"
            : "e.g. it never flagged the assignment clause"
        }
        disabled={status.kind === "sending" || status.kind === "sent"}
      />

      {/* The attorney must never send something they didn't know they sent. */}
      <div className="feedback-attached">
        Sends your note plus {item}, the document text, and this turn's id so the
        developer can reproduce it.
      </div>

      <div className="feedback-actions">
        <button
          className="primary"
          onClick={onSend}
          disabled={!comment.trim() || status.kind === "sending" || status.kind === "sent"}
        >
          {status.kind === "sending" ? "Sending…" : status.kind === "sent" ? "Sent ✓" : "Send"}
        </button>
        <button className="secondary" onClick={onClose} disabled={status.kind === "sending"}>
          Cancel
        </button>
      </div>

      {status.kind === "error" && (
        <div className="status error">Didn't send: {status.message}</div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire it into App**

In `clients/word/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";
import FeedbackPanel from "./components/FeedbackPanel";
import { buildSnapshot, onFlagRequested, EMPTY_TURN, type FlagTarget } from "./feedback";
import { readBody } from "./word";
```

Add state and registration inside `App`:

```tsx
  const [flagTarget, setFlagTarget] = useState<FlagTarget | null>(null);

  // One panel for the whole pane; cards reach it through the feedback module's
  // one-slot channel rather than four levels of prop drilling.
  useEffect(() => {
    onFlagRequested(async (t) => {
      // Attach the document at flag time, not at card-render time — the doc may
      // have changed since the turn, and what we want is what they are looking at.
      const documentText = await readBody().catch(() => "");
      setFlagTarget({ ...t, snapshot: { ...t.snapshot, ...buildSnapshot({ documentText }) } });
    });
    return () => onFlagRequested(null);
  }, []);
```

Add the header button, after the `<p className="subtitle">` line:

```tsx
        <button
          className="secondary feedback-open"
          onClick={() =>
            setFlagTarget({
              turn: EMPTY_TURN,
              surface: "general",
              targetKind: "",
              targetRef: "",
              snapshot: {},
            })
          }
        >
          Send feedback
        </button>
```

This unattached entry point exists for the **misses**. "It never flagged the assignment clause" has no card to hang off, and a miss is the one failure class invisible to both the developer and any harness.

Render the panel above `<FinalizeBar />`:

```tsx
      {flagTarget && (
        <FeedbackPanel target={flagTarget} onClose={() => setFlagTarget(null)} />
      )}
```

Add the disclosure line at the end of the `<header>`:

```tsx
        <p className="disclosure">
          During testing, your use of the assistant's suggestions is recorded so we
          can measure what it gets wrong.
        </p>
```

Silent is not hidden, and a legal team is the worst possible audience to discover undisclosed logging.

- [ ] **Step 3: Add the styles**

Append to `clients/word/src/styles.css`:

```css
/* Feedback capture */
.flag-button {
  background: none;
  border: none;
  color: #8a8886;
  cursor: pointer;
  font-size: 13px;
  padding: 2px 6px;
}
.flag-button:hover { color: #a4262c; }

.feedback-panel {
  border: 1px solid #c8c6c4;
  border-radius: 4px;
  background: #faf9f8;
  padding: 10px;
  margin: 8px 0;
}
.feedback-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.feedback-input {
  width: 100%;
  box-sizing: border-box;
  font-family: inherit;
  font-size: 13px;
  padding: 6px;
}
.feedback-attached {
  font-size: 11px;
  color: #605e5c;
  margin: 6px 0;
}
.feedback-actions { display: flex; gap: 6px; }
.feedback-open { margin-top: 6px; }
.disclosure { font-size: 11px; color: #605e5c; margin-top: 4px; }
```

- [ ] **Step 4: Typecheck and run the gate**

Run: `cd clients/word && npx tsc --noEmit` then `bash scripts/check.sh`
Expected: no type errors; `all checks passed`.

- [ ] **Step 5: Commit**

```bash
git add clients/word/src/components/FeedbackPanel.tsx clients/word/src/App.tsx clients/word/src/styles.css
git commit -m "feat(word): in-pane feedback panel with two entry points

A flag on each card, and one header button for the misses — 'it never
flagged the assignment clause' has no card to hang off, and a miss is the
one failure class invisible to both the developer and any harness.

In-pane rather than displayDialogAsync: a dialog is a separate window with
its own origin, and the Mac webview is already on record as unreliable for
that class of thing.

The panel states what it attaches, and the header carries a disclosure
line. Silent is not hidden."
```

---

### Task 8: read-back script, docs, and the live smoke

**Files:**
- Create: `scripts/feedback_report.py`
- Modify: `docs/wiki.md`, `CLAUDE.md`

- [ ] **Step 1: Write the report script**

Create `scripts/feedback_report.py`:

```python
#!/usr/bin/env python3
"""Read back tester feedback and interaction telemetry.

    uv run python -m scripts.feedback_report

Feedback nobody reads is worse than none — it costs the attorney something and
returns nothing. This is the whole reporting story; there is deliberately no UI.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.db import init_db
from memory.feedback_store import event_counts, recent_feedback


def main() -> None:
    init_db()

    rows = recent_feedback(limit=50)
    print(f"=== Feedback ({len(rows)}) ===\n")
    if not rows:
        print("  (none yet)\n")
    for r in rows:
        who = r["user_name"] or r["attorney_id"]
        target = f" · {r['target_kind']}: {r['target_ref'][:60]}" if r["target_kind"] else ""
        print(f"  {r['timestamp'][:19]}  {who}  [{r['surface']}]{target}")
        print(f"    {r['comment']}")
        if r["trace_id"]:
            print(f"    trace: {r['trace_id']}  turn: {r['turn_id']}")
        print()

    counts = event_counts()
    print(f"=== Interaction events ({sum(c['count'] for c in counts)}) ===\n")
    if not counts:
        print("  (none yet)\n")
        return
    width = max((len(c["action"]) for c in counts), default=10)
    for c in counts:
        print(f"  {c['surface']:<10} {c['action']:<{width}}  {c['count']:>6}")
    print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs against a live DB**

Run: `docker compose up -d app-db && uv run python -m scripts.feedback_report`
Expected: both sections print with `(none yet)` — no traceback, no missing-table error.

- [ ] **Step 3: Backend smoke**

Restart the backend (`uvicorn` does not auto-reload) and exercise both endpoints:

```bash
bash scripts/start.sh   # in a separate terminal, leave running
```

```bash
curl -s -X POST http://localhost:8000/api/feedback \
  -H 'Content-Type: application/json' -H 'X-User-ID: smoke-1' -H 'X-User-Name: Smoke' \
  -d '{"comment":"smoke test","surface":"general","snapshot":{"document_text":"hi"}}'

curl -s -X POST http://localhost:8000/api/events \
  -H 'Content-Type: application/json' -H 'X-User-ID: smoke-1' \
  -d '{"events":[{"action":"edit_applied","surface":"chat"}]}'

uv run python -m scripts.feedback_report
```

Expected: `{"status":"ok","data":{"saved":true,"id":1}}`, `{"recorded":1}`, and both rows in the report.

- [ ] **Step 4: Word sideload smoke**

`npx tsc --noEmit` is not enough for add-in changes (CLAUDE.md). In `clients/word`, run `npm run dev`, sideload in Word for Mac, and confirm:

1. Run a review → `findings_rendered` appears in the report with the finding count.
2. Click `⚑` on a finding → the panel opens, states what it attaches, sends, and the row appears with a trace id.
3. Ask a chat question → `edits_proposed` appears with a count (`0` for a pure question).
4. Apply an edit → `edit_applied`. Discard one → `edit_discarded`.
5. Force a failure (flag an edit whose target text you have since altered) → `edit_failed` with the error string in `detail`.
6. Use the header **Send feedback** button with no card → a `general` row with an empty `target_kind`.
7. Stop `app-db` (`docker compose stop app-db`), Apply an edit → **the Apply still succeeds and the pane shows nothing**. Restart it.

Step 7 is the one that matters most: it proves telemetry is not load-bearing.

- [ ] **Step 5: Update the wiki**

Add a row to `## Shipped Since Last Update` in `docs/wiki.md` covering: the `turn_id`/`trace_id` join key (and that `state["trace_id"]` was the session id and read nowhere); the two tables and their loud/quiet split; the two endpoints; the panel's two entry points and why the header one exists; the disclosure line; and `scripts/feedback_report.py`.

Then in `## Follow-ups / Roadmap`:
- Add: **promote a feedback row to an eval fixture** (Phase A), **derive the taxonomy from the first ~30 items**, **`interaction_event` retention** (bundle with the existing `conversation_store` retention row), and **the attorney's own tracked changes as ground truth** with its baseline-ambiguity caveat.
- Annotate the six rate-blocked rows (spurious edits, fabrication guard, `chat_history` residual, improvisation rate, Layer 2 recall, relaxing preferences) with a pointer that the denominators now exist.

- [ ] **Step 6: Update CLAUDE.md**

**The file is at exactly 150 lines — its stated cap.** Displace a lower-value line rather than appending. Add under **Backend**:

```markdown
- **Feedback capture is keyed to `turn_id`, not `session_id`** — one session covers every turn in both Word tabs. `POST /api/query` returns `turn_id` (uuid, per request) + the real 32-hex OTel `trace_id` (`observability/spans.py::current_trace_id`). `feedback` writes are LOUD (the attorney was told it sent), `interaction_event` writes are QUIET (telemetry must never break an Apply) — same split as review vs conversation. Read it with `uv run python -m scripts.feedback_report`. `tests/conftest.py` truncates a HARDCODED table list — add any new store table there or it leaks state and greens a lie.
```

- [ ] **Step 7: Final gate**

Run: `bash scripts/check.sh`
Expected: `all checks passed`; 468 backend tests, 199/199 frontend assertions.

- [ ] **Step 8: Commit**

```bash
git add scripts/feedback_report.py docs/wiki.md CLAUDE.md
git commit -m "feat(scripts): feedback read-back + docs

Feedback nobody reads is worse than none. One script, no UI.

Wiki records the feature and annotates the six rate-blocked roadmap rows
now that the denominators exist."
```

---

## Definition of done

- `bash scripts/check.sh` green: 468 backend tests, 199/199 frontend assertions.
- Word sideload smoke complete, **including step 7** — an Apply still works with `app-db` stopped.
- `uv run python -m scripts.feedback_report` shows both sections populated from the smoke.
- Nothing in `graph/`, `skills/`, or any prompt file was touched. Verify with `git diff --stat main`.
- Branch pushed to both remotes (`origin`, `ado`) only after the merge decision.
