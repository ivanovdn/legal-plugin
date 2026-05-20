# Resume After Interrupt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `human_review.interrupt()` actually pause the graph, implement `POST /api/query/{session_id}/resume`, and add an iterative "Request Changes" loop (cap 3) that re-runs the skill with attorney notes injected.

**Architecture:** `human_review` becomes a 4-way decision point on the resume value (approve / revise / loop-back with notes / cap-hit). A new `route_review` conditional edge from `human_review` routes terminal verdicts to `output_formatter` and loop-back to `skill_dispatcher`. Skills read `state["attorney_notes"]` and inject it into their LLM prompt. Pending checkpoints get a Redis EXPIRE of 24h, refreshed on each invoke/resume.

**Tech Stack:** langgraph (`interrupt`, `Command`), langgraph-checkpoint-redis, fastapi, chainlit, pytest, redis-py

**Spec:** `docs/superpowers/specs/2026-05-20-resume-after-interrupt-design.md`

---

## File Structure

```
legal-plugin/
├── config.py                                # MODIFY — add 2 settings, flip 1 default
├── graph/
│   ├── state.py                             # MODIFY — add 2 fields
│   ├── graph.py                             # MODIFY — conditional edge + route_review
│   ├── checkpointer.py                      # MODIFY — add refresh_ttl helper
│   └── nodes/
│       ├── human_review.py                  # REWRITE function body — 4-way verdict
│       └── output_formatter.py              # MODIFY — include report_notes_unincorporated
├── api/
│   └── routes/query.py                      # MODIFY — interrupt payload, resume_query, refresh_ttl
├── skills/
│   ├── contract_generation/
│   │   └── contract_generation.py           # MODIFY — inject attorney_notes
│   ├── legal_research.py                    # MODIFY — same
│   ├── contract_review/
│   │   └── contract_review.py               # MODIFY — same (plain-skill pattern)
│   ├── compliance_check.py                  # MODIFY — same
│   └── drafting.py                          # MODIFY — same
├── frontend/
│   ├── api_client.py                        # MODIFY — add resume_query
│   └── app.py                               # MODIFY — wire 3 callbacks to resume
└── tests/
    ├── test_config.py                       # MODIFY — 3 new tests
    ├── test_state.py                        # MODIFY — 1 new test (default for new field)
    ├── test_nodes.py                        # MODIFY — ~7 new tests
    ├── test_graph.py                        # MODIFY — ~5 new tests
    ├── test_checkpointer.py                 # MODIFY — 1 new test
    ├── test_api.py                          # MODIFY — 3 new tests
    └── test_skills.py                       # MODIFY — 5 new tests (one per skill)
```

---

## Task 1: Add 3 config settings

Add `max_review_iterations`, `checkpoint_ttl_seconds`; flip `interrupt_enabled` default to `True`.

**Files:**
- Modify: `config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_settings_default_interrupt_enabled_true(monkeypatch):
    """Default interrupt_enabled is True (resume is now wired)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.interrupt_enabled is True


def test_settings_default_max_review_iterations(monkeypatch):
    """Default max_review_iterations is 3."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.max_review_iterations == 3


def test_settings_default_checkpoint_ttl_seconds(monkeypatch):
    """Default checkpoint_ttl_seconds is 86400 (24 hours)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.checkpoint_ttl_seconds == 86400
```

Note: there's already a passing test `test_settings_default_interrupt_enabled_false` from the prior memory feature. That test expects `False` — we need to UPDATE it to expect `True`, since the default is flipping. The existing test's function body changes; the new test above replaces it logically. Open `tests/test_config.py`, find `test_settings_default_interrupt_enabled_false`, and rename it to `test_settings_default_interrupt_enabled_true` with assertion `assert settings.interrupt_enabled is True`. Do NOT keep both — that would conflict.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_config.py -v -k "interrupt_enabled or max_review_iterations or checkpoint_ttl_seconds"`

Expected: 3 FAILs (the renamed `_true` test fails because default is still False; the other two fail with `AttributeError`).

- [ ] **Step 3: Update the settings**

In `config.py`, find the `# Memory / checkpointer` section added in the prior memory feature:

```python
    # Memory / checkpointer
    checkpointer_enabled: bool = True
    interrupt_enabled: bool = False
    chat_history_n_turns: int = 5
    chat_history_trim_chars: int = 300
```

Replace it with:

```python
    # Memory / checkpointer
    checkpointer_enabled: bool = True
    interrupt_enabled: bool = True
    chat_history_n_turns: int = 5
    chat_history_trim_chars: int = 300
    max_review_iterations: int = 3
    checkpoint_ttl_seconds: int = 86400
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_config.py -v`

Expected: all `test_config.py` tests PASS.

Then full suite as regression check: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`. Existing tests must still pass.

**Important:** Some prior tests for `human_review` may have relied on `interrupt_enabled=False` as the default. Run the full suite. If a test like `test_human_review_skips_interrupt_when_disabled` now fails because the global default is True, it'll need an explicit `monkeypatch.setenv("INTERRUPT_ENABLED", "false")` + `get_settings.cache_clear()`. Fix those tests in this step by adding the env override. List the test names that needed adjustment in the commit message.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py tests/test_nodes.py
git commit -m "feat(config): flip interrupt_enabled default True; add review-loop settings"
```

---

## Task 2: Add `review_iterations` and `report_notes_unincorporated` to state

Two new fields, no reducers needed (last-write-wins).

**Files:**
- Modify: `graph/state.py`
- Modify: `tests/test_nodes.py` (`_make_state` helper)
- Modify: `tests/test_graph.py` (`_make_state` helper + inline literal in `test_legal_agent_state_can_be_created`)
- Modify: `tests/test_skills.py` (`_make_state` helper if present)

- [ ] **Step 1: Write a failing test**

Append to `tests/test_state.py`:

```python
def test_state_has_review_iterations_field(monkeypatch):
    """LegalAgentState supports review_iterations and report_notes_unincorporated keys."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    from graph.state import LegalAgentState
    s: LegalAgentState = {
        "request": "r",
        "user_id": "u",
        "uploaded_docs": [],
        "task_type": "",
        "skill_plan": [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {},
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "",
        "checkpoint_ref": "",
        "trace_id": "",
        "chat_history": [],
        "review_iterations": 0,
        "report_notes_unincorporated": "",
    }
    assert s["review_iterations"] == 0
    assert s["report_notes_unincorporated"] == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_state.py -v -k "review_iterations"`

Expected: TypedDict allows extra keys at runtime, so this might PASS already since TypedDict is structural. However, the type checker would error. **Run anyway**; if it passes proceed to Step 3 (the goal is to lock in the schema for typing, not runtime). If it fails (because `LegalAgentState` is checked stricter), proceed normally.

- [ ] **Step 3: Add the fields to the TypedDict**

In `graph/state.py`, add two fields to the `LegalAgentState` class, after `chat_history`:

```python
    chat_history: Annotated[list[dict], _history_reducer]
    review_iterations: int                 # NEW — counts loop-backs; capped at max_review_iterations
    report_notes_unincorporated: str       # NEW — attorney notes the loop couldn't incorporate (set on cap)
```

- [ ] **Step 4: Update test helpers**

In `tests/test_nodes.py`, modify the `_make_state` helper to include the two new fields with default `0` and `""`:

```python
def _make_state(**overrides):
    base = {
        # ... all existing fields ...
        "chat_history": [],
        "review_iterations": 0,                 # NEW
        "report_notes_unincorporated": "",      # NEW
    }
    base.update(overrides)
    return base
```

Apply the same additions to `_make_state` in `tests/test_graph.py` AND to the inline state literal in `test_legal_agent_state_can_be_created` (search the file for `"chat_history": []` — add the two new keys immediately after that line in each occurrence).

In `tests/test_skills.py`, if a `_make_state` helper exists, apply the same. If `test_skills.py` imports `_make_state` from `test_nodes`, no change is needed there.

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add graph/state.py tests/test_state.py tests/test_nodes.py tests/test_graph.py tests/test_skills.py
git commit -m "feat(state): add review_iterations and report_notes_unincorporated fields"
```

---

## Task 3: Implement `human_review` 4-way verdict logic

`human_review` becomes the decision point. Mock `interrupt()` in tests so it returns the resume value directly without raising.

**Files:**
- Modify: `graph/nodes/human_review.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nodes.py`:

```python
def test_human_review_approved_sets_awaiting_review_false(monkeypatch):
    """Resume with approved=True clears awaiting_review and keeps llm_response."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.human_review.interrupt", return_value={
        "approved": True, "notes": "looks good", "revised_response": "",
    }):
        state = _make_state(task_type="contract_generation", llm_response="DRAFT")
        result = human_review(state)

    assert result["awaiting_review"] is False
    assert result["attorney_notes"] == "looks good"
    assert result["llm_response"] == "DRAFT"  # unchanged
    assert result["review_iterations"] == 0  # unchanged


def test_human_review_revised_replaces_llm_response(monkeypatch):
    """Resume with revised_response set uses the revised text and exits."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.human_review.interrupt", return_value={
        "approved": False, "notes": "rewrote it", "revised_response": "ATTORNEY-EDITED DRAFT",
    }):
        state = _make_state(task_type="contract_generation", llm_response="LLM DRAFT")
        result = human_review(state)

    assert result["awaiting_review"] is False
    assert result["llm_response"] == "ATTORNEY-EDITED DRAFT"
    assert result["attorney_notes"] == "rewrote it"


def test_human_review_notes_only_loops_back(monkeypatch):
    """Resume with notes-only + iter<cap: increment iter, reset llm_response/chunks/messages."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", "3")
    get_settings.cache_clear()

    with patch("graph.nodes.human_review.interrupt", return_value={
        "approved": False, "notes": "add confidentiality clause", "revised_response": "",
    }):
        state = _make_state(
            task_type="contract_generation",
            llm_response="DRAFT",
            retrieved_chunks=[{"doc_id": "d1"}],
            messages=[{"role": "system", "content": "x"}],
            review_iterations=0,
        )
        result = human_review(state)

    assert result["attorney_notes"] == "add confidentiality clause"
    assert result["review_iterations"] == 1
    assert result["llm_response"] == ""
    assert result["retrieved_chunks"] == []
    assert result["messages"] == []
    assert result["awaiting_review"] is False  # cleared so route_review picks skill_dispatcher


def test_human_review_iteration_cap_hit(monkeypatch):
    """At cap: don't loop, attach notes to report_notes_unincorporated, exit normally."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", "3")
    get_settings.cache_clear()

    with patch("graph.nodes.human_review.interrupt", return_value={
        "approved": False, "notes": "more changes", "revised_response": "",
    }):
        state = _make_state(
            task_type="contract_generation",
            llm_response="DRAFT v3",
            review_iterations=3,
        )
        result = human_review(state)

    assert result["report_notes_unincorporated"] == "more changes"
    assert result["awaiting_review"] is False
    assert result["llm_response"] == "DRAFT v3"  # kept
    assert result["review_iterations"] == 3  # unchanged


def test_human_review_pure_reject_no_notes(monkeypatch):
    """Pure reject (approved=False, no notes, no revised): exit normally with empty notes."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.human_review.interrupt", return_value={
        "approved": False, "notes": "", "revised_response": "",
    }):
        state = _make_state(task_type="contract_generation", llm_response="DRAFT")
        result = human_review(state)

    assert result["awaiting_review"] is False
    assert result["llm_response"] == "DRAFT"
    assert result["report_notes_unincorporated"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_nodes.py -v -k "human_review_approved_sets or revised_replaces or notes_only_loops or iteration_cap_hit or pure_reject"`

Expected: 5 FAILs — current `human_review` has only the simpler approved/revised branches and no iteration counting.

- [ ] **Step 3: Replace `human_review` function body**

In `graph/nodes/human_review.py`, replace the entire body of the `human_review` function with the version below. Keep all existing top-of-file imports (`logging`, `observe`, `interrupt`, `get_settings`, `LegalAgentState`):

```python
@observe(name="human_review")
def human_review(state: LegalAgentState) -> LegalAgentState:
    """Pause for attorney review. Applies the verdict on resume.

    Four outcomes:
      - approved=True              → exit (route_review → output_formatter)
      - revised_response non-empty → use revised text, exit
      - notes only + iter<cap      → reset llm_response/chunks/messages, +1 iter, loop back
      - cap hit or pure reject     → exit, attach notes to report_notes_unincorporated
    """
    state["awaiting_review"] = True
    logger.info(
        "[human_review] review required: task_type=%s, risk_level=%s, iter=%d",
        state.get("task_type"), state.get("risk_level"),
        state.get("review_iterations", 0),
    )

    settings = get_settings()
    if not settings.interrupt_enabled:
        logger.info("[human_review] interrupt disabled by config — flagging and continuing")
        return state

    review = interrupt({
        "type": "human_review",
        "task_type": state.get("task_type"),
        "risk_level": state.get("risk_level"),
        "llm_response": state.get("llm_response", "")[:500],
        "risk_flags": state.get("risk_flags", []),
        "review_iterations": state.get("review_iterations", 0),
    })

    if not isinstance(review, dict):
        logger.warning("[human_review] unexpected resume payload type=%s", type(review).__name__)
        state["awaiting_review"] = False
        return state

    approved = review.get("approved", True)
    notes = review.get("notes", "")
    revised = review.get("revised_response", "")
    iterations = state.get("review_iterations", 0)
    max_iter = settings.max_review_iterations

    if approved:
        state["awaiting_review"] = False
        state["attorney_notes"] = notes
        logger.info("[human_review] approved by attorney")
        return state

    if revised:
        state["llm_response"] = revised
        state["attorney_notes"] = notes
        state["awaiting_review"] = False
        logger.info("[human_review] revised by attorney")
        return state

    if notes and iterations < max_iter:
        state["attorney_notes"] = notes
        state["review_iterations"] = iterations + 1
        state["llm_response"] = ""
        state["retrieved_chunks"] = []
        state["messages"] = []
        state["awaiting_review"] = False
        logger.info("[human_review] loop-back iteration %d/%d", iterations + 1, max_iter)
        return state

    state["report_notes_unincorporated"] = notes
    state["awaiting_review"] = False
    logger.info(
        "[human_review] terminal: cap_hit=%s, pure_reject=%s",
        iterations >= max_iter, not notes,
    )
    return state
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_nodes.py -v -k "human_review"`

Expected: all `human_review` tests PASS (5 new + any pre-existing).

Then full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`. No regressions.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/human_review.py tests/test_nodes.py
git commit -m "feat(human_review): 4-way verdict (approve/revise/loop/cap)"
```

---

## Task 4: Add `route_review` + conditional edge

Replace the unconditional `human_review → output_formatter` edge with a conditional one.

**Files:**
- Modify: `graph/graph.py`
- Modify: `tests/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph.py`:

```python
def test_route_review_returns_skill_dispatcher_when_llm_response_empty():
    """Empty llm_response is the loop-back signal."""
    from graph.graph import route_review
    state = _make_state(llm_response="", awaiting_review=False)
    assert route_review(state) == "skill_dispatcher"


def test_route_review_returns_output_formatter_when_llm_response_set():
    """Non-empty llm_response means terminal exit (approve/revise/cap)."""
    from graph.graph import route_review
    state = _make_state(llm_response="DRAFT", awaiting_review=False)
    assert route_review(state) == "output_formatter"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_graph.py -v -k "route_review"`

Expected: 2 FAILs with `ImportError: cannot import name 'route_review'`.

- [ ] **Step 3: Add `route_review` and the conditional edge**

In `graph/graph.py`:

a) Add the new function next to the existing `route_intent` (around line 28):

```python
def route_review(state: LegalAgentState) -> str:
    """Decide where to go from human_review after the resume completes.

    Empty llm_response is the signal that human_review chose to loop back
    (it clears llm_response on the loop-back path). Any other state means
    a terminal verdict (approve, revise, cap-hit, pure-reject).
    """
    if state.get("llm_response", ""):
        return "output_formatter"
    return "skill_dispatcher"
```

b) Inside `build_graph`, find the existing edge:

```python
    # human_review -> output_formatter
    graph.add_edge("human_review", "output_formatter")
```

Replace with:

```python
    # Conditional: human_review -> output_formatter OR skill_dispatcher
    graph.add_conditional_edges("human_review", route_review, {
        "output_formatter": "output_formatter",
        "skill_dispatcher": "skill_dispatcher",
    })
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_graph.py -v -k "route_review"`

Expected: 2 PASS.

Run full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`.

**Note:** Pre-existing graph tests that route through `human_review` may now hit `skill_dispatcher` instead of `output_formatter` if `llm_response` is empty in their state. The existing tests pass a state with `llm_response` set (because the upstream LLM is mocked to return a response), so they should be unaffected. Verify by reading test output for regressions; if any test fails because the routing changed, the test's mock LLM needs to set `llm_response` (already the case in `_fake_ollama_post`).

- [ ] **Step 5: Commit**

```bash
git add graph/graph.py tests/test_graph.py
git commit -m "feat(graph): conditional edge from human_review via route_review"
```

---

## Task 5: Add `refresh_ttl` helper to `graph/checkpointer.py`

Fire-and-forget TTL refresh on Redis checkpoint keys.

**Files:**
- Modify: `graph/checkpointer.py`
- Modify: `tests/test_checkpointer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_checkpointer.py`:

```python
def test_refresh_ttl_calls_expire_on_checkpoint_keys(monkeypatch):
    """refresh_ttl scans and expires both checkpoint:* and checkpoint_write:* keys."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHECKPOINT_TTL_SECONDS", "3600")
    get_settings.cache_clear()

    fake_redis = MagicMock()
    fake_redis.scan_iter.side_effect = [
        iter([b"checkpoint:abc:foo", b"checkpoint:abc:bar"]),
        iter([b"checkpoint_write:abc:foo"]),
    ]

    with patch("graph.checkpointer.Redis", return_value=fake_redis):
        from graph.checkpointer import refresh_ttl
        refresh_ttl("abc")

    # 2 checkpoint keys + 1 checkpoint_write key = 3 expire calls
    assert fake_redis.expire.call_count == 3
    # Each call's TTL is 3600
    for call in fake_redis.expire.call_args_list:
        assert call.args[1] == 3600


def test_refresh_ttl_does_nothing_when_checkpointer_disabled(monkeypatch):
    """When checkpointer_enabled=False, refresh_ttl is a no-op."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHECKPOINTER_ENABLED", "false")
    get_settings.cache_clear()

    with patch("graph.checkpointer.Redis") as mock_redis_cls:
        from graph.checkpointer import refresh_ttl
        refresh_ttl("abc")

    mock_redis_cls.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_checkpointer.py -v -k "refresh_ttl"`

Expected: 2 FAILs with `ImportError: cannot import name 'refresh_ttl'`.

- [ ] **Step 3: Add `refresh_ttl` to `graph/checkpointer.py`**

In `graph/checkpointer.py`, add at the top of the file (after existing imports):

```python
from urllib.parse import urlparse

from redis import Redis
```

(`redis` is already an indirect dependency via `langgraph-checkpoint-redis`; verify by `import redis` succeeds.)

Then add the function at the bottom of the file:

```python
def refresh_ttl(session_id: str) -> None:
    """Refresh Redis TTL on all checkpoint keys for this session.

    Fire-and-forget: any failure is logged at WARN and swallowed so the
    request response is not affected by Redis hiccups.
    """
    settings = get_settings()
    if not settings.checkpointer_enabled:
        return
    try:
        url = urlparse(settings.redis_url)
        r = Redis(host=url.hostname, port=url.port or 6379, password=url.password)
        for key in r.scan_iter(match=f"checkpoint:{session_id}:*"):
            r.expire(key, settings.checkpoint_ttl_seconds)
        for key in r.scan_iter(match=f"checkpoint_write:{session_id}:*"):
            r.expire(key, settings.checkpoint_ttl_seconds)
    except Exception as e:
        logger.warning("refresh_ttl failed for %s: %s", session_id, e)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_checkpointer.py -v`

Expected: all tests PASS (2 new + 2 pre-existing).

- [ ] **Step 5: Commit**

```bash
git add graph/checkpointer.py tests/test_checkpointer.py
git commit -m "feat(checkpointer): add refresh_ttl helper for Redis EXPIRE"
```

---

## Task 6: Update `output_formatter` to include `report_notes_unincorporated`

When the cap is hit, the report should surface the un-incorporated notes.

**Files:**
- Modify: `graph/nodes/output_formatter.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nodes.py`:

```python
def test_output_formatter_includes_unincorporated_notes(monkeypatch):
    """When report_notes_unincorporated is set, it appears in the final report."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    from graph.nodes.output_formatter import output_formatter
    state = _make_state(
        task_type="contract_generation",
        llm_response="FINAL DRAFT",
        report_notes_unincorporated="Attorney wanted X, hit iteration cap.",
    )
    result = output_formatter(state)
    assert result["report"]["notes_unincorporated"] == "Attorney wanted X, hit iteration cap."


def test_output_formatter_omits_unincorporated_when_empty(monkeypatch):
    """When the field is empty, the report omits the key (or has empty string)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    from graph.nodes.output_formatter import output_formatter
    state = _make_state(task_type="contract_review", llm_response="OK")
    result = output_formatter(state)
    assert result["report"].get("notes_unincorporated", "") == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_nodes.py -v -k "output_formatter_includes_unincorporated or output_formatter_omits_unincorporated"`

Expected: 2 FAILs (the field doesn't exist in the report yet).

- [ ] **Step 3: Update `output_formatter`**

In `graph/nodes/output_formatter.py`, replace the function body with:

```python
@observe(name="output_formatter")
def output_formatter(state: LegalAgentState) -> LegalAgentState:
    """Build structured report from LLM response and metadata."""
    state["report"] = {
        "task_type": state.get("task_type", ""),
        "response": state.get("llm_response", ""),
        "risk_level": state.get("risk_level", "low"),
        "risk_flags": state.get("risk_flags", []),
        "awaiting_review": state.get("awaiting_review", False),
        "sources": [
            {"doc_id": c.get("doc_id"), "doc_title": c.get("doc_title")}
            for c in state.get("retrieved_chunks", [])
        ],
        "notes_unincorporated": state.get("report_notes_unincorporated", ""),
    }
    logger.info("[output_formatter] report built, task_type=%s", state["task_type"])
    return state
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_nodes.py -v -k "output_formatter"`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/output_formatter.py tests/test_nodes.py
git commit -m "feat(output_formatter): include notes_unincorporated in report"
```

---

## Task 7: API — interrupt payload from `submit_query`; real `resume_query`

**Files:**
- Modify: `api/routes/query.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_submit_query_returns_interrupt_payload_when_awaiting_review(monkeypatch):
    """When graph result has awaiting_review=True, API returns interrupt_payload."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    def _interrupt_invoke(state, config=None):
        state["awaiting_review"] = True
        state["task_type"] = "contract_generation"
        state["llm_response"] = "DRAFT"
        state["risk_level"] = "medium"
        state["risk_flags"] = []
        state["review_iterations"] = 0
        return state

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _interrupt_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "Generate", "task_type": "contract_generation"},
            headers={"X-User-ID": "attorney-1"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["awaiting_review"] is True
    assert "interrupt_payload" in data
    assert data["interrupt_payload"]["llm_response"] == "DRAFT"
    assert data["interrupt_payload"]["review_iterations"] == 0


def test_resume_query_calls_graph_with_command_resume(monkeypatch):
    """POST /api/query/{sid}/resume invokes graph with Command(resume=...) and matching thread_id."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    fake_state = MagicMock()
    fake_state.values = {"awaiting_review": False}

    with patch("api.routes.query._get_graph") as mock_get_graph, \
         patch("api.routes.query.refresh_ttl"):
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = fake_state
        mock_graph.invoke.return_value = {
            "task_type": "contract_generation",
            "report": {"response": "FINAL"},
            "risk_level": "low",
            "awaiting_review": False,
        }
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query/sess-X/resume",
            json={"approved": True, "notes": "", "revised_response": ""},
        )

    assert response.status_code == 200
    # First positional arg to invoke is the Command; assert thread_id propagated
    call = mock_graph.invoke.call_args
    config = call.kwargs.get("config") or call.args[1]
    assert config["configurable"]["thread_id"] == "sess-X"
    # And the resume value passed:
    cmd = call.args[0]
    assert hasattr(cmd, "resume") or isinstance(cmd, dict)  # langgraph Command object or dict-like


def test_resume_query_returns_error_when_session_unknown(monkeypatch):
    """If get_state returns no values, API returns session-expired error envelope."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    fake_state = MagicMock()
    fake_state.values = {}  # no checkpoint values = unknown session

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = fake_state
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query/sess-missing/resume",
            json={"approved": True, "notes": "", "revised_response": ""},
        )

    assert response.status_code == 200  # API uses envelope, not HTTP status
    data = response.json()
    assert data["status"] == "error"
    assert any("session expired" in e.lower() or "not found" in e.lower() for e in data["errors"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_api.py -v -k "interrupt_payload or resume_query_calls_graph or resume_query_returns_error_when"`

Expected: 3 FAILs (the resume endpoint is still a placeholder; submit_query doesn't yet detect interrupt).

- [ ] **Step 3: Update `api/routes/query.py`**

Replace the contents of `api/routes/query.py` with:

```python
# api/routes/query.py
"""Query endpoints — submit requests, resume interrupts, check status."""

import logging
import uuid

from fastapi import APIRouter, Header
from langfuse.decorators import observe, langfuse_context
from langgraph.types import Command

from api.models import ApiResponse, QueryRequest, ResumeRequest
from config import get_settings
from graph.checkpointer import build_checkpointer, refresh_ttl
from graph.graph import build_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_graph = None


def _get_graph():
    """Lazy-init compiled graph with optional Redis checkpointer."""
    global _graph
    if _graph is None:
        settings = get_settings()
        cp = build_checkpointer() if settings.checkpointer_enabled else None
        _graph = build_graph(checkpointer=cp)
    return _graph


def _payload_from_result(result: dict, session_id: str) -> dict:
    """Shape the response payload for both submit and resume."""
    if result.get("awaiting_review"):
        return {
            "session_id": session_id,
            "awaiting_review": True,
            "interrupt_payload": {
                "task_type": result.get("task_type", ""),
                "risk_level": result.get("risk_level", ""),
                "llm_response": result.get("llm_response", ""),
                "risk_flags": result.get("risk_flags", []),
                "review_iterations": result.get("review_iterations", 0),
            },
            "report": {},
        }
    return {
        "session_id": session_id,
        "task_type": result.get("task_type", ""),
        "report": result.get("report", {}),
        "risk_level": result.get("risk_level", ""),
        "awaiting_review": False,
    }


@router.post("/query", response_model=ApiResponse)
@observe(name="query")
def submit_query(
    body: QueryRequest,
    x_user_id: str = Header("anonymous", alias="X-User-ID"),
):
    """Submit a legal request for graph execution."""
    session_id = body.session_id or str(uuid.uuid4())

    langfuse_context.update_current_trace(
        name=f"query:{body.task_type or 'auto'}",
        user_id=x_user_id,
        session_id=session_id,
        input=body.request,
    )

    initial_state = {
        "request": body.request,
        "user_id": x_user_id,
        "uploaded_docs": [{"text": body.uploaded_text}] if body.uploaded_text else [],
        "task_type": body.task_type,
        "skill_plan": [body.task_type] if body.task_type else [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": body.filters,
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": session_id,
        "checkpoint_ref": "",
        "trace_id": session_id,
        "chat_history": [],
        "review_iterations": 0,
        "report_notes_unincorporated": "",
    }

    graph = _get_graph()
    config = {"configurable": {"thread_id": session_id}}

    try:
        result = graph.invoke(initial_state, config=config)
        refresh_ttl(session_id)
        return ApiResponse(status="ok", data=_payload_from_result(result, session_id))
    except Exception as e:
        logger.exception("Graph execution failed")
        return ApiResponse(status="error", errors=[str(e)])


@router.post("/query/{session_id}/resume", response_model=ApiResponse)
def resume_query(session_id: str, body: ResumeRequest):
    """Resume graph execution after human review interrupt."""
    graph = _get_graph()
    config = {"configurable": {"thread_id": session_id}}

    try:
        prior = graph.get_state(config)
    except Exception as e:
        logger.warning("resume: get_state failed for %s: %s", session_id, e)
        return ApiResponse(status="error", errors=["session expired or not found"])
    if not prior or not prior.values:
        return ApiResponse(status="error", errors=["session expired or not found"])

    try:
        result = graph.invoke(
            Command(resume={
                "approved": body.approved,
                "notes": body.notes,
                "revised_response": body.revised_response,
            }),
            config=config,
        )
        refresh_ttl(session_id)
        return ApiResponse(status="ok", data=_payload_from_result(result, session_id))
    except Exception as e:
        logger.exception("resume: graph invoke failed for %s", session_id)
        return ApiResponse(status="error", errors=[str(e)])


@router.get("/query/{session_id}/status", response_model=ApiResponse)
def query_status(session_id: str):
    """Check the status of a graph execution."""
    return ApiResponse(
        status="ok",
        data={"session_id": session_id, "status": "unknown"},
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_api.py -v`

Expected: all API tests PASS (3 new + pre-existing).

Then full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add api/routes/query.py tests/test_api.py
git commit -m "feat(api): interrupt payload + working resume_query with Command(resume)"
```

---

## Task 8: Agent skills inject `attorney_notes`

`contract_generation` and `legal_research` build their own user message and call `agent.invoke({"messages": [*chat_history, {user}]})`. Append the notes block to the user message.

**Files:**
- Modify: `skills/contract_generation/contract_generation.py`
- Modify: `skills/legal_research.py`
- Modify: `tests/test_skills.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills.py`:

```python
def test_contract_generation_injects_attorney_notes(monkeypatch):
    """When attorney_notes is set, the agent's user message includes the notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    from unittest.mock import patch, MagicMock
    from tests.test_nodes import _make_state

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: (
        captured.setdefault("payload", payload) or
        {"messages": [MagicMock(content="DRAFT v2")]}
    )

    state = _make_state(
        request="Generate a service agreement for Vertex",
        filters={"client_id": "internal"},
        attorney_notes="Add a confidentiality clause; reduce cap to 1.5x.",
    )

    with patch("skills.contract_generation.contract_generation._build_agent", return_value=fake_agent):
        from skills.contract_generation.contract_generation import contract_generation
        contract_generation(state)

    sent = captured["payload"]["messages"]
    # Final user message must contain both the request and the attorney notes block
    last_user = sent[-1]["content"]
    assert "Vertex" in last_user
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "confidentiality clause" in last_user


def test_legal_research_injects_attorney_notes(monkeypatch):
    """Same contract for the research agent."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    from unittest.mock import patch, MagicMock
    from tests.test_nodes import _make_state

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: (
        captured.setdefault("payload", payload) or
        {"messages": [MagicMock(content="Per case A (doc_id: d1)...")]}
    )

    state = _make_state(
        request="What's the standard cap for SaaS?",
        filters={"client_id": "internal"},
        attorney_notes="Focus on EU jurisdiction precedents only.",
    )

    with patch("skills.legal_research._build_agent", return_value=fake_agent):
        from skills.legal_research import legal_research
        legal_research(state)

    sent = captured["payload"]["messages"]
    last_user = sent[-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "EU jurisdiction" in last_user
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_skills.py -v -k "injects_attorney_notes"`

Expected: 2 FAILs (current code doesn't add the notes block).

- [ ] **Step 3: Update `contract_generation`**

In `skills/contract_generation/contract_generation.py`, find the `user_message = "\n".join(context_parts)` line. Immediately after it, add:

```python
    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_message += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )
```

The rest of the function (the `try:` block, `_build_agent`, `agent.invoke`, etc.) stays unchanged.

- [ ] **Step 4: Update `legal_research`**

In `skills/legal_research.py`, find the equivalent `user_message = "\n".join(context_parts)` line. Immediately after it, add the identical block:

```python
    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_message += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_skills.py -v -k "injects_attorney_notes"`

Expected: 2 PASS.

Full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`.

- [ ] **Step 6: Commit**

```bash
git add skills/contract_generation/contract_generation.py skills/legal_research.py tests/test_skills.py
git commit -m "feat(skills): agent skills inject attorney_notes into agent prompt"
```

---

## Task 9: Plain skills inject `attorney_notes`

`contract_review`, `compliance_check`, `drafting` all set `state["messages"]` with a system + user pair. Append the notes block to the user content.

**Files:**
- Modify: `skills/contract_review/contract_review.py`
- Modify: `skills/compliance_check.py`
- Modify: `skills/drafting.py`
- Modify: `tests/test_skills.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills.py`:

```python
def test_contract_review_injects_attorney_notes(monkeypatch):
    """contract_review's user message includes the attorney_notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    from tests.test_nodes import _make_state
    from skills.contract_review.contract_review import contract_review

    state = _make_state(
        request="Review this NDA",
        attorney_notes="Pay special attention to clauses 3 and 7.",
    )
    result = contract_review(state)
    last_user = result["messages"][-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "clauses 3 and 7" in last_user


def test_compliance_check_injects_attorney_notes(monkeypatch):
    """compliance_check user message includes the attorney_notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    from tests.test_nodes import _make_state
    from skills.compliance_check import compliance_check

    state = _make_state(
        request="Check GDPR compliance",
        attorney_notes="Focus on data-subject rights specifically.",
    )
    result = compliance_check(state)
    last_user = result["messages"][-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "data-subject rights" in last_user


def test_drafting_injects_attorney_notes(monkeypatch):
    """drafting user message includes the attorney_notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    from tests.test_nodes import _make_state
    from skills.drafting import drafting

    state = _make_state(
        request="Draft an NDA template",
        attorney_notes="Use the mutual-NDA format.",
    )
    result = drafting(state)
    last_user = result["messages"][-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "mutual-NDA" in last_user
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_skills.py -v -k "contract_review_injects_attorney_notes or compliance_check_injects_attorney_notes or drafting_injects_attorney_notes"`

Expected: 3 FAILs.

- [ ] **Step 3: Update `contract_review`**

In `skills/contract_review/contract_review.py`, replace the existing `state["messages"] = [...]` block with:

```python
    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    state["messages"] = [
        {"role": "system", "content": playbook},
        {"role": "user", "content": user_content},
    ]
```

(Place the new `attorney_notes` block immediately BEFORE the `state["messages"]` assignment so the appended content lands in the user message.)

- [ ] **Step 4: Update `compliance_check`**

In `skills/compliance_check.py`, replace the function body of `compliance_check` (keeping the `_SYSTEM_PROMPT` constant unchanged):

```python
def compliance_check(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for compliance verification via rag_retriever + llm_caller."""
    request = state["request"]
    attorney_notes = (state.get("attorney_notes") or "").strip()

    user_content = request
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    state["retrieval_query"] = request
    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    logger.info("[compliance_check] prepared for compliance verification: %s", request[:80])
    return state
```

- [ ] **Step 5: Update `drafting`**

In `skills/drafting.py`, replace the function body of `drafting`:

```python
def drafting(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for document drafting via rag_retriever + llm_caller."""
    request = state["request"]
    filters = state.get("filters", {})
    attorney_notes = (state.get("attorney_notes") or "").strip()

    query_parts = [request]
    if filters.get("jurisdiction"):
        query_parts.append(f"jurisdiction: {filters['jurisdiction']}")
    state["retrieval_query"] = " ".join(query_parts)

    user_content = request
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    logger.info("[drafting] prepared for document drafting: %s", request[:80])
    return state
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_skills.py -v`

Expected: all skill tests PASS (3 new + pre-existing).

Full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`.

- [ ] **Step 7: Commit**

```bash
git add skills/contract_review/contract_review.py skills/compliance_check.py skills/drafting.py tests/test_skills.py
git commit -m "feat(skills): plain skills inject attorney_notes into user message"
```

---

## Task 10: Add `resume_query` to frontend `api_client.py`

**Files:**
- Modify: `frontend/api_client.py`

No new tests (the api_client is a thin HTTP wrapper). It's exercised via Chainlit; coverage comes from Task 13's manual integration test.

- [ ] **Step 1: Add the function**

Append to `frontend/api_client.py` (after the existing `submit_query` function):

```python
async def resume_query(
    session_id: str,
    approved: bool,
    notes: str = "",
    revised_response: str = "",
) -> dict:
    """POST /api/query/{session_id}/resume — submit attorney verdict."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            f"{_base_url()}/api/query/{session_id}/resume",
            json={
                "approved": approved,
                "notes": notes,
                "revised_response": revised_response,
            },
        )
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 2: Static verification**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -c "import ast; ast.parse(open('frontend/api_client.py').read()); print('OK')"`

Expected: `OK`.

Run full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`. No regressions.

- [ ] **Step 3: Commit**

```bash
git add frontend/api_client.py
git commit -m "feat(frontend): add resume_query HTTP client"
```

---

## Task 11: Wire Chainlit action callbacks to `resume_query`

Replace the three local-only callback bodies (`on_approve`, `on_request_changes`, `on_reject`) with real calls to the resume endpoint. Add a shared rendering helper.

**Files:**
- Modify: `frontend/app.py`

No unit tests (Chainlit lifecycle code).

- [ ] **Step 1: Add imports and helper**

In `frontend/app.py`:

a) The existing top-of-file imports already include `submit_query`, `ingest_file`, `health_check` from `frontend.api_client`. Add `resume_query` to that import line:

```python
from frontend.api_client import submit_query, ingest_file, health_check, resume_query
```

b) Add a new rendering helper near the other helpers (after `_show_in_side_panel`, before the chat handlers). The helper handles both terminal results and loop-back interrupts:

```python
async def _render_resume_result(result: dict):
    """Render a /api/resume response. Handles loop-back (new interrupt) and final result."""
    if result.get("status") == "error":
        errs = result.get("errors") or ["unknown error"]
        msg = errs[0] if errs else "unknown error"
        await cl.Message(content=f"Resume failed: {msg}").send()
        cl.user_session.set("pending_review_text", "")
        return

    data = result.get("data", {})
    if data.get("awaiting_review"):
        payload = data.get("interrupt_payload", {})
        new_text = payload.get("llm_response", "")
        task_type = payload.get("task_type", "unknown")
        risk_level = payload.get("risk_level", "unknown")
        cl.user_session.set("pending_review_text", new_text)
        await _show_in_side_panel(
            cl.Message(content=""),
            response_text=new_text,
            task_type=task_type,
            risk_level=risk_level,
            sources=[],
            awaiting_review=True,
        )
    else:
        report = data.get("report", {})
        response_text = report.get("response", "(no response)")
        notes_unincorp = report.get("notes_unincorporated", "")
        content = response_text
        if notes_unincorp:
            content += (
                f"\n\n---\n**Notes not incorporated** "
                f"(iteration cap reached):\n{notes_unincorp}"
            )
        await cl.Message(content=content).send()
        cl.user_session.set("pending_review_text", "")
```

- [ ] **Step 2: Replace `on_approve` body**

Find the existing `@cl.action_callback("approve")` handler. Replace its body with:

```python
@cl.action_callback("approve")
async def on_approve(action: cl.Action):
    session_id = cl.user_session.get("session_id", "")
    if not session_id:
        await cl.Message(content="Session expired. Please start a new request.").send()
        return
    try:
        result = await resume_query(session_id=session_id, approved=True)
    except Exception as e:
        await cl.Message(content=f"Resume failed: {e}").send()
        return
    await _render_resume_result(result)
```

(The existing PDF-generation behavior on Approve is deferred — when the final report has a response, the user can re-trigger PDF export via a follow-up step. Keeping the resume call clean for now.)

- [ ] **Step 3: Replace `on_request_changes` body**

```python
@cl.action_callback("request_changes")
async def on_request_changes(action: cl.Action):
    session_id = cl.user_session.get("session_id", "")
    if not session_id:
        await cl.Message(content="Session expired. Please start a new request.").send()
        return
    notes_msg = await cl.AskUserMessage(
        content="Describe the changes you'd like made to this draft.",
        timeout=600,
    ).send()
    if not notes_msg:
        await cl.Message(content="No notes provided — cancelling.").send()
        return
    # AskUserMessage returns dict with "output" or "content" depending on Chainlit version
    notes_text = (notes_msg.get("output") or notes_msg.get("content") or "").strip()
    if not notes_text:
        await cl.Message(content="Empty notes — cancelling.").send()
        return
    try:
        result = await resume_query(
            session_id=session_id,
            approved=False,
            notes=notes_text,
        )
    except Exception as e:
        await cl.Message(content=f"Resume failed: {e}").send()
        return
    await _render_resume_result(result)
```

- [ ] **Step 4: Replace `on_reject` body**

```python
@cl.action_callback("reject")
async def on_reject(action: cl.Action):
    session_id = cl.user_session.get("session_id", "")
    if not session_id:
        await cl.Message(content="Session expired. Please start a new request.").send()
        return
    try:
        result = await resume_query(session_id=session_id, approved=False)
    except Exception as e:
        await cl.Message(content=f"Resume failed: {e}").send()
        return
    await _render_resume_result(result)
```

- [ ] **Step 5: Static + suite verification**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -c "import ast; ast.parse(open('frontend/app.py').read()); print('OK')"`

Expected: `OK`.

Run full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`. No regressions.

- [ ] **Step 6: Commit**

```bash
git add frontend/app.py
git commit -m "feat(frontend): wire Chainlit action callbacks to /api/resume"
```

---

## Task 12: End-to-end MemorySaver tests for interrupt + resume

Live verification (no Redis required) that the graph genuinely pauses and resumes through approve and notes-loop paths.

**Files:**
- Modify: `tests/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph.py`:

```python
def test_graph_interrupts_and_returns_partial_state_with_memory_saver(tmp_path, monkeypatch):
    """First invoke pauses at human_review; result has awaiting_review=True."""
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    init_audit_db(db_path)
    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-pause-1"}}

        state = _make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        )
        result = compiled.invoke(state, config=config)

    # The graph paused — state should show pending review
    assert result.get("awaiting_review") is True
    assert result.get("task_type") == "contract_generation"
    assert result.get("llm_response", "") != ""


def test_graph_resume_with_approved_completes(tmp_path, monkeypatch):
    """After pause, resume with approved=True → final result, awaiting_review=False."""
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    init_audit_db(db_path)
    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    from langgraph.types import Command

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-approve-1"}}

        compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ), config=config)

        final = compiled.invoke(
            Command(resume={"approved": True, "notes": "", "revised_response": ""}),
            config=config,
        )

    assert final.get("awaiting_review") is False
    assert final.get("report", {}).get("response", "") != ""


def test_graph_resume_with_notes_loops_back_and_regenerates(tmp_path, monkeypatch):
    """Resume with notes-only → graph loops back, regenerates, pauses again at higher iter."""
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", "3")
    get_settings.cache_clear()

    init_audit_db(db_path)
    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    from langgraph.types import Command

    # Track what the skill agent saw on each invocation
    invocations = []

    def _capture_agent():
        agent = MagicMock()
        def _invoke(payload):
            invocations.append(payload)
            return {"messages": [MagicMock(content=f"DRAFT v{len(invocations)} (doc_id: d1)")]}
        agent.invoke.side_effect = _invoke
        return agent

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_capture_agent()):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-loop-1"}}

        # Turn 1: invoke → pause
        compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ), config=config)

        # Resume with notes only → graph loops back, regenerates, pauses again
        result_2 = compiled.invoke(
            Command(resume={
                "approved": False,
                "notes": "Add a confidentiality clause",
                "revised_response": "",
            }),
            config=config,
        )

    assert result_2.get("awaiting_review") is True
    assert result_2.get("review_iterations") == 1
    assert len(invocations) == 2  # agent was called twice — original + regeneration
    # Second invocation's user message included the attorney notes
    second_msgs = invocations[1]["messages"]
    last_user = second_msgs[-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "confidentiality clause" in last_user


def test_graph_resume_iteration_cap_terminates(tmp_path, monkeypatch):
    """3 successive notes-only resumes → 4th resume terminates with unincorporated notes in report."""
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", "3")
    get_settings.cache_clear()

    init_audit_db(db_path)
    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    from langgraph.types import Command

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-cap-1"}}

        # Initial invoke
        compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ), config=config)

        # 3 loop-back resumes (iter 1, 2, 3)
        for i in range(3):
            compiled.invoke(
                Command(resume={
                    "approved": False,
                    "notes": f"iteration {i + 1} change",
                    "revised_response": "",
                }),
                config=config,
            )

        # 4th resume with notes — should hit the cap and terminate
        final = compiled.invoke(
            Command(resume={
                "approved": False,
                "notes": "this should not be incorporated",
                "revised_response": "",
            }),
            config=config,
        )

    assert final.get("awaiting_review") is False
    assert final.get("report", {}).get("notes_unincorporated") == "this should not be incorporated"


def test_graph_without_checkpointer_still_completes_in_one_shot(tmp_path, monkeypatch):
    """Regression: with no checkpointer + interrupt disabled, graph completes as before."""
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "false")
    get_settings.cache_clear()

    init_audit_db(db_path)
    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=None)
        result = compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ))

    # With interrupt disabled, graph completes through output_formatter
    assert result.get("report", {}).get("response", "") != ""
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/test_graph.py -v -k "interrupts_and_returns or resume_with_approved or resume_with_notes_loops or iteration_cap_terminates or without_checkpointer_still_completes"`

Expected: 5 PASS.

Run full suite: `/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pytest tests/ -q`. All tests pass.

**Note:** If any test fails because LangGraph's `Command(resume=...)` API has a different shape than expected (e.g., the import path or constructor differs), check the actual `langgraph.types` module in `.venv` for the right import. The fallback is `from langgraph.types import Command` per the spec.

- [ ] **Step 3: Commit**

```bash
git add tests/test_graph.py
git commit -m "test(graph): end-to-end interrupt + resume + loop + cap"
```

---

## Task 13: Manual integration test + merge

No code — manual verification of the full flow against live Ollama, then merge to local main (no remote configured).

- [ ] **Step 1: Start the stack**

Run:
```bash
docker compose up -d
bash scripts/start.sh
```

Confirm:
- http://localhost:8000/health → `status: ok`
- http://localhost:8080 → Chainlit loads

- [ ] **Step 2: First turn — initial request**

In Chainlit, send: `Generate a service agreement for Vertex Systems Inc., 2-year term`

Wait for the draft to render in the side panel with the Approve / Request Changes / Reject buttons.

Confirm the backend log shows:
- `[intent_router] LLM classified: contract_generation`
- `[contract_generation] agent completed`
- `[human_review] review required: task_type=contract_generation, risk_level=...`

And no `[history_appender]` log yet — that fires only after a terminal verdict.

- [ ] **Step 3: Click "Request Changes" → enter notes**

When the AskUserMessage prompt appears, enter: `Add a confidentiality clause and reduce the cap to 1.5x fees`.

Confirm the backend log shows:
- `[human_review] loop-back iteration 1/3`
- `[contract_generation] agent completed` (a SECOND time)
- `[human_review] review required: task_type=contract_generation, ..., iter=1`

And the side panel shows a NEW draft with confidentiality + 1.5x cap mentioned. Buttons present again.

- [ ] **Step 4: Click "Approve"**

Confirm:
- Backend log: `[human_review] approved by attorney`
- `[history_appender] appended turn ...`
- Memory_writer audit log line

Chainlit renders the final report; no buttons.

- [ ] **Step 5: Verify in Langfuse**

Open http://localhost:3000. Find the trace for this session.

Expected spans (in order): `intake`, `intent_router`, `skill_dispatcher`, `contract_generation`, `risk_assessor`, `human_review`, `skill_dispatcher`, `contract_generation`, `risk_assessor`, `human_review`, `output_formatter`, `history_appender`, `memory_writer`.

Two `human_review` spans + two `contract_generation` spans confirm the loop ran once.

- [ ] **Step 6: Verify session expiration**

In a terminal:
```bash
docker exec legal-plugin-redis-1 redis-cli -a myredissecret KEYS 'checkpoint:*' | head -5
docker exec legal-plugin-redis-1 redis-cli -a myredissecret TTL <one-of-the-keys-above>
```

Expected: TTL value should be near 86400 (24h) and decreasing.

- [ ] **Step 7: Verify session-expired path**

In Chainlit, send another contract gen request. When the buttons appear, manually expire the keys:
```bash
docker exec legal-plugin-redis-1 redis-cli -a myredissecret KEYS 'checkpoint:*' | xargs docker exec legal-plugin-redis-1 redis-cli -a myredissecret DEL
```

Click "Approve" in Chainlit. Expected: "Resume failed: session expired or not found" message.

- [ ] **Step 8: Stop the stack and merge**

In the `start.sh` terminal: Ctrl+C.

Then merge to local main (no remote configured for this repo):

```bash
git checkout main && git merge --no-ff feat/resume-after-interrupt -m "$(cat <<'EOF'
merge: feat/resume-after-interrupt — pause-and-resume with iterative review loop

Pauses the graph at human_review via interrupt(), exposes /api/query/{sid}/resume,
and adds an iterative Request-Changes loop (capped at 3) that re-runs the skill
with attorney_notes injected into the prompt. Checkpoints get a 24h Redis TTL,
refreshed on each activity.

- human_review: 4-way verdict (approve / revise / loop / cap-hit)
- new route_review conditional edge from human_review
- new state fields: review_iterations, report_notes_unincorporated
- 5 skill files inject attorney_notes into their prompts
- Chainlit's 3 action buttons now call /api/resume
- config: interrupt_enabled=True by default, max_review_iterations=3, checkpoint_ttl_seconds=86400

Verified live: contract generation → Request Changes with notes →
regenerated draft incorporating the notes → Approve → final report.

~22 new unit tests including 5 end-to-end MemorySaver tests covering
pause, resume-approve, resume-loop, iteration-cap, and the no-checkpointer
regression path.

Spec: docs/superpowers/specs/2026-05-20-resume-after-interrupt-design.md
Plan: docs/superpowers/plans/2026-05-20-resume-after-interrupt.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Verify with:
```bash
git log --oneline -5
```

---

## Self-review

**Spec coverage check:**

| Spec section | Plan task |
|---|---|
| Config: `interrupt_enabled=True` default, `max_review_iterations`, `checkpoint_ttl_seconds` | Task 1 |
| State: `review_iterations`, `report_notes_unincorporated` | Task 2 |
| `human_review` 4-way verdict logic (approve/revise/loop/cap) | Task 3 |
| `route_review` + conditional edge | Task 4 |
| `refresh_ttl` helper | Task 5 |
| `output_formatter` includes `notes_unincorporated` | Task 6 |
| API: interrupt payload from `submit_query`; working `resume_query`; 404 on unknown session | Task 7 |
| Agent skills (`contract_generation`, `legal_research`) inject `attorney_notes` | Task 8 |
| Plain skills (`contract_review`, `compliance_check`, `drafting`) inject `attorney_notes` | Task 9 |
| Frontend `api_client.resume_query` | Task 10 |
| Chainlit action callbacks call resume; `_render_resume_result` handles loop-back vs final | Task 11 |
| End-to-end pause+resume+loop+cap tests with MemorySaver | Task 12 |
| Manual integration verifying live behavior; merge | Task 13 |
| Rollback (`INTERRUPT_ENABLED=false`) | Implicit in Task 1 |
| Forward-compatibility with Word add-in | Noted in spec; no code action needed |

All spec sections covered. No gaps.

**Placeholder scan:** No "TODO", "TBD", "implement later", "add appropriate error handling" — all steps have complete code blocks. Each step has explicit expected output for verification commands.

**Type consistency:**
- `review_iterations: int` consistent across state (Task 2), `human_review` (Task 3), `route_review` (Task 4), API payload (Task 7), tests.
- `report_notes_unincorporated: str` consistent across state (Task 2), `human_review` (Task 3), `output_formatter` (Task 6 — exposed as `notes_unincorporated` in the report dict, deliberately renamed at the boundary for cleaner JSON), tests.
- `Command(resume={...})` from `langgraph.types` used identically in API code (Task 7) and tests (Task 12).
- `refresh_ttl(session_id: str) -> None` signature consistent across factory (Task 5) and consumers in API (Task 7).
- The phrase `"--- ATTORNEY REVIEW NOTES (incorporate these changes) ---"` is repeated verbatim across Tasks 8 and 9 — test assertions check for `"ATTORNEY REVIEW NOTES"` substring (not full string), so minor formatting drift won't break tests.

Plan complete.
