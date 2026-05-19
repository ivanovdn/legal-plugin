# Within-Session Conversation Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add within-session conversation memory so the LLM sees prior `(user, assistant)` turns on every new request inside a Chainlit session, persisted in Redis via a LangGraph checkpointer.

**Architecture:** A new `chat_history` field on `LegalAgentState` with a custom reducer that concatenates + caps at `2*N` entries. A new `history_appender` node sits between `output_formatter` and `memory_writer` and appends each turn, trimming assistant content to 300 chars. `llm_caller` and the ReAct agent skills prepend `chat_history` into the prompt. The API constructs a `RedisSaver` once and passes `config={"configurable": {"thread_id": session_id}}` to `graph.invoke`. Chainlit mints a UUID `session_id` per chat session and propagates it on every request.

**Tech Stack:** langgraph, langgraph-checkpoint-redis, langchain-ollama, httpx, pydantic-settings, pytest, chainlit, fastapi

**Spec:** `docs/superpowers/specs/2026-05-19-within-session-memory-design.md`

---

## File Structure

```
legal-plugin/
├── config.py                              # MODIFY — add 4 new settings
├── requirements.txt                       # MODIFY — add langgraph-checkpoint-redis
├── graph/
│   ├── state.py                           # MODIFY — add chat_history field + reducer
│   ├── graph.py                           # MODIFY — wire history_appender node
│   ├── checkpointer.py                    # CREATE — RedisSaver factory
│   └── nodes/
│       ├── history_appender.py            # CREATE — appends + trims turn pair
│       ├── llm_caller.py                  # MODIFY — prepend chat_history
│       └── human_review.py                # MODIFY — gate interrupt() behind config
├── skills/
│   ├── contract_generation/
│   │   └── contract_generation.py         # MODIFY — inject chat_history into agent
│   └── legal_research.py                  # MODIFY — inject chat_history into agent
├── api/
│   └── routes/query.py                    # MODIFY — wire checkpointer + thread_id
├── frontend/
│   └── app.py                             # MODIFY — mint + pass session_id
└── tests/
    ├── test_state.py                      # CREATE — reducer tests
    ├── test_nodes.py                      # MODIFY — history_appender + llm_caller tests
    ├── test_graph.py                      # MODIFY — end-to-end memory test (MemorySaver)
    └── test_api.py                        # MODIFY — thread_id propagation tests
```

---

## Task 1: Add config settings

Add four new fields to `Settings`. `redis_url` already exists, so only four are needed.

**Files:**
- Modify: `config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_settings_default_chat_history_n_turns(monkeypatch):
    """Default chat_history_n_turns is 5."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.chat_history_n_turns == 5


def test_settings_default_chat_history_trim_chars(monkeypatch):
    """Default chat_history_trim_chars is 300."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.chat_history_trim_chars == 300


def test_settings_default_checkpointer_enabled_true(monkeypatch):
    """Default checkpointer_enabled is True."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.checkpointer_enabled is True


def test_settings_default_interrupt_enabled_false(monkeypatch):
    """Default interrupt_enabled is False (resume not yet wired)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.interrupt_enabled is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v -k "chat_history or checkpointer_enabled or interrupt_enabled"`

Expected: 4 FAILs with `AttributeError` (fields don't exist yet).

- [ ] **Step 3: Add the four settings**

Append inside `Settings` class in `config.py`, immediately after the existing `bm25_enabled: bool = False` line (in the BM25 section is fine, or add a new `# Memory` section):

```python
    # Memory / checkpointer
    checkpointer_enabled: bool = True
    interrupt_enabled: bool = False
    chat_history_n_turns: int = 5
    chat_history_trim_chars: int = 300
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v -k "chat_history or checkpointer_enabled or interrupt_enabled"`

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat(config): add chat_history and checkpointer settings"
```

---

## Task 2: Add `chat_history` field and reducer to state

The reducer concatenates `old + new` and caps to last `2*N` entries. Trimming happens at write time (in `history_appender`), not in the reducer.

**Files:**
- Modify: `graph/state.py`
- Create: `tests/test_state.py`
- Modify: `tests/test_nodes.py` (add `chat_history: []` to `_make_state` helper)
- Modify: `tests/test_graph.py` (same `_make_state` helper update)

- [ ] **Step 1: Write the failing reducer tests**

Create `tests/test_state.py`:

```python
"""Unit tests for state reducer."""

def test_history_reducer_concatenates_empty_and_new(monkeypatch):
    """Empty old + non-empty new → just new."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    from graph.state import _history_reducer
    a = {"role": "user", "content": "hello"}
    b = {"role": "assistant", "content": "world"}
    assert _history_reducer([], [a, b]) == [a, b]


def test_history_reducer_caps_at_2n(monkeypatch):
    """With N=5, old of 10 + new of 4 → last 10 entries kept."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_N_TURNS", "5")
    from config import get_settings
    get_settings.cache_clear()

    from graph.state import _history_reducer
    old = [{"role": "user", "content": f"u{i}"} for i in range(10)]
    new = [{"role": "assistant", "content": f"a{i}"} for i in range(4)]
    result = _history_reducer(old, new)
    assert len(result) == 10
    # Oldest 4 from `old` are dropped; remaining 6 from `old` + 4 new
    assert result[0]["content"] == "u4"
    assert result[-1]["content"] == "a3"


def test_history_reducer_preserves_order(monkeypatch):
    """Reducer preserves FIFO order after cap."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_N_TURNS", "2")
    from config import get_settings
    get_settings.cache_clear()

    from graph.state import _history_reducer
    old = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    new = [
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]
    result = _history_reducer(old, new)
    # 2*N = 4, so keep last 4: u2, a2, u3, a3
    assert [m["content"] for m in result] == ["u2", "a2", "u3", "a3"]


def test_history_reducer_handles_none_old(monkeypatch):
    """If old is None (first turn before reducer ever ran), reducer still works."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    from graph.state import _history_reducer
    new = [{"role": "user", "content": "hello"}]
    # The reducer should be defensive
    assert _history_reducer(None, new) == new
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_state.py -v`

Expected: 4 FAILs with `ImportError: cannot import name '_history_reducer' from 'graph.state'`.

- [ ] **Step 3: Add the field and reducer**

Replace the contents of `graph/state.py` with:

```python
# graph/state.py
from __future__ import annotations

from typing import Annotated, TypedDict

from ingest.chunk_models import LegalChunk


def _history_reducer(old: list[dict] | None, new: list[dict]) -> list[dict]:
    """Concatenate old + new, then cap to the last 2*N entries.

    N = chat_history_n_turns from settings. Each turn contributes 2 entries
    (one user message, one assistant message), so 2*N is the message cap.
    """
    from config import get_settings  # local import to avoid circular at module load
    n = get_settings().chat_history_n_turns
    old = old or []
    return (old + new)[-(2 * n):]


class LegalAgentState(TypedDict):
    request: str
    user_id: str
    uploaded_docs: list[LegalChunk]
    task_type: str          # contract_generation | contract_review | compliance | research | drafting | multi
    skill_plan: list[str]
    retrieval_query: str
    retrieved_chunks: list[LegalChunk]
    filters: dict           # client_id, jurisdiction, doc_type
    messages: list[dict]
    llm_response: str
    risk_level: str         # low | medium | high
    risk_flags: list[dict]
    awaiting_review: bool
    attorney_notes: str
    report: dict
    session_id: str
    checkpoint_ref: str
    trace_id: str
    chat_history: Annotated[list[dict], _history_reducer]
```

- [ ] **Step 4: Update test helpers**

In `tests/test_nodes.py`, modify the `_make_state` helper (around line 8) to include `chat_history`:

```python
def _make_state(**overrides):
    base = {
        "request": "test request",
        "user_id": "attorney-1",
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
        "session_id": "test-sess",
        "checkpoint_ref": "",
        "trace_id": "",
        "chat_history": [],          # NEW
    }
    base.update(overrides)
    return base
```

Apply the same `"chat_history": []` addition to `_make_state` in `tests/test_graph.py` (around line 33) AND to the inline state literal in `test_legal_agent_state_can_be_created` (around line 7) — both helpers need updating.

- [ ] **Step 5: Run the new tests and the full suite to verify nothing regresses**

Run: `python -m pytest tests/test_state.py -v && python -m pytest tests/ -q`

Expected: 4 new state tests PASS. Full suite PASS (64 + 4 = 68 tests, give or take depending on current count).

- [ ] **Step 6: Commit**

```bash
git add graph/state.py tests/test_state.py tests/test_nodes.py tests/test_graph.py
git commit -m "feat(state): add chat_history field with reducer (concat+cap at 2*N)"
```

---

## Task 3: Create `history_appender` node

Pure function. Reads `state["request"]` and `state["llm_response"]`, returns a partial state update with the new turn pair. Trims assistant content to `chat_history_trim_chars`.

**Files:**
- Create: `graph/nodes/history_appender.py`
- Test: `tests/test_nodes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nodes.py` (use the existing `_make_state` helper):

```python
# --- history_appender ---

def test_history_appender_appends_user_and_assistant_pair(monkeypatch):
    """history_appender returns a chat_history list with one user + one assistant message."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "300")
    from config import get_settings
    get_settings.cache_clear()

    from graph.nodes.history_appender import history_appender
    state = _make_state(request="What's the term?", llm_response="The term is 2 years.")
    result = history_appender(state)

    assert "chat_history" in result
    assert len(result["chat_history"]) == 2
    assert result["chat_history"][0] == {"role": "user", "content": "What's the term?"}
    assert result["chat_history"][1] == {"role": "assistant", "content": "The term is 2 years."}


def test_history_appender_trims_long_assistant_response(monkeypatch):
    """Assistant content longer than trim_chars is truncated and gets '[...]' marker."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "10")
    from config import get_settings
    get_settings.cache_clear()

    from graph.nodes.history_appender import history_appender
    state = _make_state(request="Generate NDA", llm_response="A" * 100)
    result = history_appender(state)

    asst = result["chat_history"][1]
    assert asst["content"] == "AAAAAAAAAA[...]"
    assert len(asst["content"]) == 15  # 10 chars + 5-char marker


def test_history_appender_does_not_trim_short_response(monkeypatch):
    """Short responses are kept verbatim, no marker appended."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "300")
    from config import get_settings
    get_settings.cache_clear()

    from graph.nodes.history_appender import history_appender
    state = _make_state(request="Q", llm_response="Short answer.")
    result = history_appender(state)

    assert result["chat_history"][1]["content"] == "Short answer."
    assert "[...]" not in result["chat_history"][1]["content"]


def test_history_appender_does_not_trim_user_request(monkeypatch):
    """User request is stored verbatim even if very long."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "10")
    from config import get_settings
    get_settings.cache_clear()

    from graph.nodes.history_appender import history_appender
    long_request = "B" * 500
    state = _make_state(request=long_request, llm_response="ok")
    result = history_appender(state)

    assert result["chat_history"][0]["content"] == long_request
    assert "[...]" not in result["chat_history"][0]["content"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_nodes.py -v -k "history_appender"`

Expected: 4 FAILs with `ModuleNotFoundError: No module named 'graph.nodes.history_appender'`.

- [ ] **Step 3: Create the node module**

Create `graph/nodes/history_appender.py`:

```python
# graph/nodes/history_appender.py
"""History appender — pushes the current (user, assistant) pair into chat_history.

Sits between output_formatter and memory_writer. Returns a partial state update;
the chat_history reducer in graph/state.py applies the cap.
"""

import logging

from langfuse.decorators import observe

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def _trim(s: str, n: int) -> str:
    """Truncate s to n chars and append '[...]' marker if it was longer."""
    return s if len(s) <= n else s[:n] + "[...]"


@observe(name="history_appender")
def history_appender(state: LegalAgentState) -> dict:
    """Append the current turn to chat_history.

    Returns only the partial state update so the reducer in state.py handles
    concatenation and capping.
    """
    settings = get_settings()
    user_msg = {"role": "user", "content": state.get("request", "")}
    assistant_msg = {
        "role": "assistant",
        "content": _trim(state.get("llm_response", ""), settings.chat_history_trim_chars),
    }
    logger.info(
        "[history_appender] appended turn (user_len=%d, assistant_len=%d)",
        len(user_msg["content"]), len(assistant_msg["content"]),
    )
    return {"chat_history": [user_msg, assistant_msg]}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_nodes.py -v -k "history_appender"`

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/history_appender.py tests/test_nodes.py
git commit -m "feat(graph): add history_appender node with assistant trim"
```

---

## Task 4: Wire `history_appender` into the graph

Replace the `output_formatter → memory_writer` edge with `output_formatter → history_appender → memory_writer`.

**Files:**
- Modify: `graph/graph.py`
- Test: `tests/test_graph.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graph.py`:

```python
def test_graph_includes_history_appender_node():
    """history_appender is registered as a graph node."""
    from graph.graph import build_graph
    compiled = build_graph()
    # nodes attribute on compiled graph is a dict of node-name -> internal node object
    assert "history_appender" in compiled.nodes


def test_graph_history_appender_runs_before_memory_writer(monkeypatch):
    """In a full graph invocation, history_appender produces chat_history before memory_writer."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    # Patch agent skill so the graph completes without real LLM
    with patch("graph.nodes.intent_router.httpx.post") as mock_post, \
         patch("skills.contract_review.contract_review") as mock_skill, \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=[]), \
         patch("graph.nodes.llm_caller.httpx.post") as mock_llm:

        fake_intent = MagicMock()
        fake_intent.status_code = 200
        fake_intent.json.return_value = {"message": {"content": '{"task_type":"contract_review"}'}}
        mock_post.return_value = fake_intent

        fake_llm = MagicMock()
        fake_llm.status_code = 200
        fake_llm.json.return_value = {"message": {"content": "Answer with cite (doc_id: d1)"}}
        mock_llm.return_value = fake_llm

        mock_skill.side_effect = lambda s: s   # passthrough

        from graph.graph import build_graph
        compiled = build_graph()
        state = _make_state(request="Review my NDA")
        result = compiled.invoke(state)

    # After full invocation, chat_history should hold one user + one assistant pair
    assert len(result["chat_history"]) == 2
    assert result["chat_history"][0]["role"] == "user"
    assert result["chat_history"][1]["role"] == "assistant"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_graph.py -v -k "history_appender"`

Expected: FAILs — `"history_appender" not in compiled.nodes`, and the invocation test fails because `chat_history` is never written.

- [ ] **Step 3: Update the graph wiring**

In `graph/graph.py`:

a) Add the import near the other node imports (after the `from graph.nodes.memory_writer import memory_writer` line):

```python
from graph.nodes.history_appender import history_appender
```

b) Inside `build_graph`, add the node registration immediately after `graph.add_node("memory_writer", memory_writer)`:

```python
    graph.add_node("history_appender", history_appender)
```

c) Replace the old edge:

```python
    # OLD:
    # graph.add_edge("output_formatter", "memory_writer")

    # NEW:
    graph.add_edge("output_formatter", "history_appender")
    graph.add_edge("history_appender", "memory_writer")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_graph.py -v -k "history_appender"`

Expected: 2 PASS.

Then run the full suite to catch regressions: `python -m pytest tests/ -q`. All tests should still pass.

- [ ] **Step 5: Commit**

```bash
git add graph/graph.py tests/test_graph.py
git commit -m "feat(graph): wire history_appender between output_formatter and memory_writer"
```

---

## Task 5: Modify `llm_caller` to prepend `chat_history`

`llm_caller` has two code paths — skill-provided `messages` (system + user) and the default `[system, user]`. In both, inject `chat_history` between the system message and the (modified) user message.

**Files:**
- Modify: `graph/nodes/llm_caller.py`
- Test: `tests/test_nodes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nodes.py`:

```python
# --- llm_caller chat_history injection ---

def test_llm_caller_prepends_chat_history_default_path(monkeypatch):
    """When no skill_messages and chat_history present, history sits between system and user."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "answer"}}

    captured = {}
    def _capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return fake_response

    history = [
        {"role": "user", "content": "prior Q"},
        {"role": "assistant", "content": "prior A"},
    ]

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(request="new Q", chat_history=history)
        llm_caller(state)

    sent = captured["json"]["messages"]
    # Expect: [system, prior_user, prior_assistant, current_user]
    assert sent[0]["role"] == "system"
    assert sent[1] == history[0]
    assert sent[2] == history[1]
    assert sent[-1]["role"] == "user"
    assert "new Q" in sent[-1]["content"]


def test_llm_caller_prepends_chat_history_skill_path(monkeypatch):
    """When skill provides system + user messages, history sits between system and user."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "answer"}}

    captured = {}
    def _capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return fake_response

    history = [
        {"role": "user", "content": "prior Q"},
        {"role": "assistant", "content": "prior A"},
    ]
    skill_messages = [
        {"role": "system", "content": "Skill system prompt"},
        {"role": "user", "content": "Review my doc"},
    ]

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(messages=skill_messages, chat_history=history)
        llm_caller(state)

    sent = captured["json"]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[0]["content"] == "Skill system prompt"
    assert sent[1] == history[0]
    assert sent[2] == history[1]
    assert sent[-1]["role"] == "user"
    assert "Review my doc" in sent[-1]["content"]


def test_llm_caller_works_when_chat_history_empty(monkeypatch):
    """Empty chat_history: prompt looks exactly like before this feature."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "answer"}}

    captured = {}
    def _capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return fake_response

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(request="just one Q", chat_history=[])
        llm_caller(state)

    sent = captured["json"]["messages"]
    assert len(sent) == 2  # system + user, no history
    assert sent[0]["role"] == "system"
    assert sent[1]["role"] == "user"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_nodes.py -v -k "llm_caller_prepends or llm_caller_works_when_chat_history_empty"`

Expected: 2 FAILs (assertions about history position), 1 PASS (empty case happens to pass because we don't break the existing 2-message structure).

- [ ] **Step 3: Inject `chat_history` in both code paths**

In `graph/nodes/llm_caller.py`, replace the message construction block (currently around lines 41–53) with:

```python
    skill_messages = state.get("messages", [])
    chat_history = state.get("chat_history", []) or []

    if skill_messages:
        base = list(skill_messages)
        if base and base[-1]["role"] == "user":
            base[-1] = {
                "role": "user",
                "content": f"Context:\n{context}\n\n{base[-1]['content']}",
            }
    else:
        base = [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nRequest: {state['request']}"},
        ]

    # Inject chat_history between the system message (if any) and the rest.
    if base and base[0].get("role") == "system":
        messages = [base[0], *chat_history, *base[1:]]
    else:
        messages = [*chat_history, *base]
```

(The rest of the function — the `httpx.post` call, response handling, langfuse logging — stays unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_nodes.py -v -k "llm_caller"`

Expected: All existing `llm_caller` tests PASS, plus the 3 new ones PASS.

Then full suite: `python -m pytest tests/ -q`. No regressions.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/llm_caller.py tests/test_nodes.py
git commit -m "feat(llm_caller): prepend chat_history between system and user message"
```

---

## Task 6: Inject `chat_history` into agent skills

`contract_generation` and `legal_research` build their own message list and call `agent.invoke({"messages": [...]})` without consulting `chat_history`. Inject the history just before the new user message.

**Files:**
- Modify: `skills/contract_generation/contract_generation.py`
- Modify: `skills/legal_research.py`
- Test: `tests/test_skills.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills.py` (use the existing `_make_state` helper if present; otherwise import the one from `tests/test_nodes.py`):

```python
def test_contract_generation_injects_chat_history_into_agent(monkeypatch):
    """Agent.invoke receives chat_history prepended to the new user message."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    from unittest.mock import patch, MagicMock
    from tests.test_nodes import _make_state

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: captured.setdefault("payload", payload) or {
        "messages": [MagicMock(content="DRAFT NDA ...")]
    }

    history = [
        {"role": "user", "content": "Generate NDA for ACME"},
        {"role": "assistant", "content": "DRAFT NDA [...]"},
    ]
    state = _make_state(
        request="Make the term 3 years",
        filters={"client_id": "internal"},
        chat_history=history,
    )

    with patch("skills.contract_generation.contract_generation._build_agent", return_value=fake_agent):
        from skills.contract_generation.contract_generation import contract_generation
        contract_generation(state)

    sent = captured["payload"]["messages"]
    # Expect history first, then the current user request
    assert sent[0] == history[0]
    assert sent[1] == history[1]
    assert sent[-1]["role"] == "user"
    assert "Make the term 3 years" in sent[-1]["content"]


def test_legal_research_injects_chat_history_into_agent(monkeypatch):
    """Same contract, on the research agent."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    from unittest.mock import patch, MagicMock
    from tests.test_nodes import _make_state

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: captured.setdefault("payload", payload) or {
        "messages": [MagicMock(content="Per case A (doc_id: d1)...")]
    }

    history = [
        {"role": "user", "content": "What's the standard cap?"},
        {"role": "assistant", "content": "2x fees in most cases."},
    ]
    state = _make_state(
        request="And for ACME specifically?",
        filters={"client_id": "internal"},
        chat_history=history,
    )

    with patch("skills.legal_research._build_agent", return_value=fake_agent):
        from skills.legal_research import legal_research
        legal_research(state)

    sent = captured["payload"]["messages"]
    assert sent[0] == history[0]
    assert sent[1] == history[1]
    assert sent[-1]["role"] == "user"
    assert "ACME" in sent[-1]["content"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_skills.py -v -k "injects_chat_history"`

Expected: 2 FAILs — `sent[0]` won't match `history[0]` because the current code only sends the new user message.

- [ ] **Step 3: Update `contract_generation`**

In `skills/contract_generation/contract_generation.py`, in the `contract_generation` function, find the line:

```python
    user_message = "\n".join(context_parts)
```

Right after the `try:` line that follows it, replace the existing `agent.invoke(...)` line with:

```python
        agent = _build_agent()
        chat_history = state.get("chat_history", []) or []
        agent_messages = [*chat_history, {"role": "user", "content": user_message}]
        result = agent.invoke({"messages": agent_messages})
```

(Keep everything else in the function the same — the response extraction, source collection, error handling.)

- [ ] **Step 4: Update `legal_research`**

In `skills/legal_research.py`, find the same pattern (`user_message = "\n".join(context_parts)` followed by `agent.invoke`). Replace the `agent.invoke` line and the line just before it with:

```python
        agent = _build_agent()
        chat_history = state.get("chat_history", []) or []
        agent_messages = [*chat_history, {"role": "user", "content": user_message}]
        result = agent.invoke({"messages": agent_messages})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_skills.py -v -k "injects_chat_history"`

Expected: 2 PASS.

Then full suite: `python -m pytest tests/ -q`. No regressions.

- [ ] **Step 6: Commit**

```bash
git add skills/contract_generation/contract_generation.py skills/legal_research.py tests/test_skills.py
git commit -m "feat(skills): inject chat_history into contract_generation and legal_research agents"
```

---

## Task 7: Gate `human_review.interrupt()` behind `interrupt_enabled` config

Once a checkpointer is wired (Task 9), `interrupt()` will actually pause the graph. Until resume is implemented, gate it off.

**Files:**
- Modify: `graph/nodes/human_review.py`
- Test: `tests/test_nodes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nodes.py`:

```python
def test_human_review_skips_interrupt_when_disabled(monkeypatch):
    """When interrupt_enabled is False, human_review flags awaiting_review but does NOT call interrupt()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "false")
    from config import get_settings
    get_settings.cache_clear()

    from unittest.mock import patch
    from graph.nodes.human_review import human_review

    with patch("langgraph.types.interrupt") as mock_interrupt:
        state = _make_state(task_type="contract_generation", risk_level="high")
        result = human_review(state)

    assert mock_interrupt.call_count == 0
    assert result["awaiting_review"] is True


def test_human_review_calls_interrupt_when_enabled(monkeypatch):
    """When interrupt_enabled is True, human_review calls interrupt()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    from config import get_settings
    get_settings.cache_clear()

    from unittest.mock import patch
    from graph.nodes.human_review import human_review

    with patch("langgraph.types.interrupt", return_value={"approved": True, "notes": "ok"}) as mock_interrupt:
        state = _make_state(task_type="contract_generation", risk_level="high")
        result = human_review(state)

    assert mock_interrupt.call_count == 1
    assert result["awaiting_review"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_nodes.py -v -k "human_review_skips_interrupt or human_review_calls_interrupt_when_enabled"`

Expected: 2 FAILs (the first will fail because the current code always tries to call `interrupt()`; the second will fail because the existing exception fallback path swallows the result).

- [ ] **Step 3: Add the config gate**

Replace the body of `human_review` in `graph/nodes/human_review.py` with:

```python
@observe(name="human_review")
def human_review(state: LegalAgentState) -> LegalAgentState:
    """Pause for human review. Uses interrupt() when both checkpointer and interrupt_enabled are present."""
    from config import get_settings

    state["awaiting_review"] = True
    logger.info(
        "[human_review] review required: task_type=%s, risk_level=%s",
        state.get("task_type"), state.get("risk_level"),
    )

    settings = get_settings()
    if not settings.interrupt_enabled:
        logger.info("[human_review] interrupt disabled by config — flagging and continuing")
        return state

    try:
        from langgraph.types import interrupt
        review = interrupt({
            "type": "human_review",
            "task_type": state.get("task_type"),
            "risk_level": state.get("risk_level"),
            "llm_response": state.get("llm_response", "")[:500],
            "risk_flags": state.get("risk_flags", []),
        })
        if isinstance(review, dict):
            state["attorney_notes"] = review.get("notes", "")
            if review.get("approved", True):
                state["awaiting_review"] = False
                logger.info("[human_review] approved by attorney")
            else:
                state["llm_response"] = review.get("revised_response", state["llm_response"])
                state["awaiting_review"] = False
                logger.info("[human_review] revised by attorney")
    except Exception as e:
        logger.warning("[human_review] interrupt unavailable (%s) — marking and continuing", type(e).__name__)

    return state
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_nodes.py -v -k "human_review"`

Expected: All existing `human_review` tests PASS, plus the 2 new ones PASS.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/human_review.py tests/test_nodes.py
git commit -m "feat(human_review): gate interrupt() behind interrupt_enabled config"
```

---

## Task 8: Create the Redis checkpointer factory

Adds the dependency, the factory, and a graceful-degradation test (returns `None` when Redis is unreachable).

**Files:**
- Modify: `requirements.txt`
- Create: `graph/checkpointer.py`
- Test: `tests/test_checkpointer.py` (new file)

- [ ] **Step 1: Install the new dependency**

Run:
```bash
/Users/dmytroivanov/projects/legal-plugin/.venv/bin/python -m pip install langgraph-checkpoint-redis
```

Then add the package to `requirements.txt`. Find the existing langgraph line and add immediately below it:

```
langgraph-checkpoint-redis
```

(No specific pin — match the project's existing convention of unpinned versions.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_checkpointer.py`:

```python
"""Tests for the Redis checkpointer factory."""

from unittest.mock import patch, MagicMock


def test_build_checkpointer_returns_none_when_redis_unavailable(monkeypatch):
    """If RedisSaver construction raises, factory returns None and logs a warning."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("REDIS_URL", "redis://invalid-host:9999")
    from config import get_settings
    get_settings.cache_clear()

    with patch("graph.checkpointer.RedisSaver") as mock_saver_cls:
        mock_saver_cls.from_conn_string.side_effect = ConnectionError("nope")
        from graph.checkpointer import build_checkpointer
        result = build_checkpointer()

    assert result is None


def test_build_checkpointer_returns_saver_when_redis_ok(monkeypatch):
    """When RedisSaver constructs successfully, factory returns it after calling setup()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_saver = MagicMock()
    with patch("graph.checkpointer.RedisSaver") as mock_saver_cls:
        mock_saver_cls.from_conn_string.return_value = fake_saver
        from graph.checkpointer import build_checkpointer
        result = build_checkpointer()

    assert result is fake_saver
    fake_saver.setup.assert_called_once()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_checkpointer.py -v`

Expected: 2 FAILs — `ModuleNotFoundError: No module named 'graph.checkpointer'`.

- [ ] **Step 4: Create the factory module**

Create `graph/checkpointer.py`:

```python
# graph/checkpointer.py
"""Redis checkpointer factory — used to wire LangGraph thread persistence.

Returns None on failure so the app can still boot and run without memory.
"""

import logging

from langgraph.checkpoint.redis import RedisSaver

from config import get_settings

logger = logging.getLogger(__name__)


def build_checkpointer():
    """Build a Redis checkpointer from settings. Returns None on failure."""
    settings = get_settings()
    try:
        saver = RedisSaver.from_conn_string(settings.redis_url)
        saver.setup()
        logger.info("Redis checkpointer initialized at %s", settings.redis_url)
        return saver
    except Exception as e:
        logger.warning("Checkpointer unavailable (%s) — running without memory", e)
        return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_checkpointer.py -v`

Expected: 2 PASS.

Then full suite: `python -m pytest tests/ -q`. No regressions.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt graph/checkpointer.py tests/test_checkpointer.py
git commit -m "feat(graph): add Redis checkpointer factory with graceful degradation"
```

---

## Task 9: Wire checkpointer + thread_id in `api/routes/query.py`

The API constructs the checkpointer once at first invoke, passes it to `build_graph`, then passes `config={"configurable": {"thread_id": session_id}}` to `graph.invoke`. Always sets `initial_state["chat_history"] = []` (the reducer concatenates with the saved history).

**Files:**
- Modify: `api/routes/query.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_submit_query_passes_thread_id_to_graph(monkeypatch):
    """graph.invoke is called with config containing thread_id = session_id."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "test", "session_id": "sess-fixed-123"},
            headers={"X-User-ID": "attorney-1"},
        )

    assert response.status_code == 200
    # graph.invoke is called as invoke(state, config=...)
    call_kwargs = mock_graph.invoke.call_args.kwargs
    config = call_kwargs.get("config") or mock_graph.invoke.call_args.args[1]
    assert config["configurable"]["thread_id"] == "sess-fixed-123"


def test_submit_query_passes_empty_chat_history_in_initial_state(monkeypatch):
    """initial_state always carries chat_history=[] — the reducer merges saved state."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        client.post(
            "/api/query",
            json={"request": "test"},
            headers={"X-User-ID": "attorney-1"},
        )

    state_arg = mock_graph.invoke.call_args.args[0]
    assert state_arg["chat_history"] == []


def test_get_graph_builds_with_checkpointer_when_enabled(monkeypatch):
    """_get_graph passes the result of build_checkpointer() to build_graph()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("CHECKPOINTER_ENABLED", "true")
    from config import get_settings
    get_settings.cache_clear()

    # Reset module-level cache
    import api.routes.query as qmod
    qmod._graph = None

    fake_cp = MagicMock(name="RedisSaver")
    with patch("api.routes.query.build_checkpointer", return_value=fake_cp) as mock_factory, \
         patch("api.routes.query.build_graph") as mock_build_graph:
        mock_build_graph.return_value = MagicMock()
        qmod._get_graph()

    mock_factory.assert_called_once()
    mock_build_graph.assert_called_once_with(checkpointer=fake_cp)


def test_get_graph_builds_without_checkpointer_when_disabled(monkeypatch):
    """When CHECKPOINTER_ENABLED=false, build_graph receives checkpointer=None."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("CHECKPOINTER_ENABLED", "false")
    from config import get_settings
    get_settings.cache_clear()

    import api.routes.query as qmod
    qmod._graph = None

    with patch("api.routes.query.build_checkpointer") as mock_factory, \
         patch("api.routes.query.build_graph") as mock_build_graph:
        mock_build_graph.return_value = MagicMock()
        qmod._get_graph()

    mock_factory.assert_not_called()
    mock_build_graph.assert_called_once_with(checkpointer=None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_api.py -v -k "thread_id or chat_history or get_graph"`

Expected: 4 FAILs — none of the new wiring exists yet.

- [ ] **Step 3: Update `api/routes/query.py`**

Replace the contents of `api/routes/query.py` with:

```python
# api/routes/query.py
"""Query endpoints — submit requests, resume interrupts, check status."""

import logging
import uuid

from fastapi import APIRouter, Header
from langfuse.decorators import observe, langfuse_context

from api.models import ApiResponse, QueryRequest, ResumeRequest
from config import get_settings
from graph.checkpointer import build_checkpointer
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
    }

    graph = _get_graph()
    config = {"configurable": {"thread_id": session_id}}

    try:
        result = graph.invoke(initial_state, config=config)
        return ApiResponse(
            status="ok",
            data={
                "session_id": session_id,
                "task_type": result.get("task_type", ""),
                "report": result.get("report", {}),
                "risk_level": result.get("risk_level", ""),
                "awaiting_review": result.get("awaiting_review", False),
            },
        )
    except Exception as e:
        logger.exception("Graph execution failed")
        return ApiResponse(status="error", errors=[str(e)])


@router.post("/query/{session_id}/resume", response_model=ApiResponse)
def resume_query(session_id: str, body: ResumeRequest):
    """Resume graph execution after human review interrupt."""
    return ApiResponse(
        status="error",
        errors=["Resume not yet implemented — interrupt_enabled=False"],
    )


@router.get("/query/{session_id}/status", response_model=ApiResponse)
def query_status(session_id: str):
    """Check the status of a graph execution."""
    return ApiResponse(
        status="ok",
        data={"session_id": session_id, "status": "unknown"},
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_api.py -v -k "thread_id or chat_history or get_graph"`

Expected: 4 PASS.

Then full suite: `python -m pytest tests/ -q`. No regressions.

- [ ] **Step 5: Commit**

```bash
git add api/routes/query.py tests/test_api.py
git commit -m "feat(api): wire checkpointer and propagate thread_id from session_id"
```

---

## Task 10: Propagate `session_id` from Chainlit

Mint a UUID on chat start and pass it on every `submit_query` call.

**Files:**
- Modify: `frontend/app.py`

This is the only task without a unit test — Chainlit lifecycle code isn't unit-testable without significant scaffolding. Coverage comes from the manual integration test below.

- [ ] **Step 1: Update `on_chat_start` and `on_message`**

In `frontend/app.py`:

a) Add `import uuid` at the top with the other imports (after `import sys`).

b) In `on_chat_start` (around line 121), set the session_id on the user session. Replace the existing function body with:

```python
@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("session_id", str(uuid.uuid4()))
    try:
        result = await health_check()
        if result.get("status") == "ok":
            await cl.Message(
                content="Legal Assistant ready. How can I help you today?\n\n"
                "You can:\n"
                "- Ask legal questions\n"
                "- Request contract generation or review\n"
                "- Upload documents (PDF/DOCX) for ingestion\n"
                "- Check compliance against policies",
            ).send()
        else:
            await cl.Message(content="Warning: Backend API is not fully healthy.").send()
    except Exception as e:
        await cl.Message(content=f"Error: Cannot reach backend API at port 8000.\n\n{e}").send()
```

c) In `on_message` (around line 140), pull the session_id and pass it to `submit_query`. Find the existing `await submit_query(...)` call (currently 4 keyword args) and change it to include `session_id`:

```python
        result = await submit_query(
            request=message.content,
            user_id=user_id,
            uploaded_text=uploaded_text,
            session_id=cl.user_session.get("session_id", ""),
        )
```

- [ ] **Step 2: Smoke-test manually**

Run:
```bash
docker compose up -d
bash scripts/start.sh
```

Open http://localhost:8080. Send a message. Confirm in the FastAPI log that the request payload includes a non-empty `session_id`. Send a second message in the same browser tab and confirm the same `session_id` is reused.

Stop the services with Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.py
git commit -m "feat(frontend): propagate per-chat session_id to backend"
```

---

## Task 11: End-to-end memory test with in-memory checkpointer

Verify that running the compiled graph twice with the same `thread_id` makes turn 2's LLM call see turn 1's history. Uses LangGraph's `MemorySaver` so this stays a unit test (no Redis required).

**Files:**
- Modify: `tests/test_graph.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graph.py`:

```python
def test_graph_with_checkpointer_persists_chat_history_across_invocations(monkeypatch):
    """Two invocations with the same thread_id: turn 2's LLM prompt contains turn 1's messages."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    from unittest.mock import patch, MagicMock
    from langgraph.checkpoint.memory import MemorySaver

    captured_messages = []

    def _fake_post(*args, **kwargs):
        captured_messages.append(kwargs.get("json", {}).get("messages", []))
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"message": {"content": '{"task_type":"compliance"}'}}
        # Detect whether this is intent_router (returns JSON) or llm_caller (returns prose)
        # by inspecting the last user message
        msgs = kwargs.get("json", {}).get("messages", [])
        last_user = next((m for m in reversed(msgs) if m["role"] == "user"), {})
        if "classify" in last_user.get("content", "").lower() or "intent" in last_user.get("content", "").lower():
            fake.json.return_value = {"message": {"content": '{"task_type":"compliance"}'}}
        else:
            fake.json.return_value = {"message": {"content": "Cited answer from doc_id: d1"}}
        return fake

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=[]):

        from graph.graph import build_graph
        compiled = build_graph(checkpointer=MemorySaver())

        state_1 = _make_state(request="What's our policy on indemnification caps?")
        config = {"configurable": {"thread_id": "test-thread-xyz"}}
        result_1 = compiled.invoke(state_1, config=config)
        assert len(result_1["chat_history"]) == 2

        # Turn 2 — same thread, fresh request, chat_history=[] in initial state
        state_2 = _make_state(request="And for vendors specifically?", chat_history=[])
        result_2 = compiled.invoke(state_2, config=config)

    # After turn 2, chat_history should hold 4 messages (2 turns * 2)
    assert len(result_2["chat_history"]) == 4

    # The llm_caller call in turn 2 should have seen turn 1's messages in its prompt
    # Find the llm_caller call in turn 2 (it'll be the last non-intent call)
    llm_calls = [m for m in captured_messages if any("Cited" in msg.get("content", "") or "Context:" in msg.get("content", "") for msg in m)]
    # At minimum, the turn 2 LLM prompt should reference the turn 1 user request somewhere
    turn_2_llm = captured_messages[-1]
    history_contents = [m["content"] for m in turn_2_llm if m["role"] in ("user", "assistant")]
    assert any("indemnification caps" in c for c in history_contents), \
        f"Turn 2 LLM prompt did not include turn 1's user message. Got: {history_contents}"


def test_graph_without_checkpointer_still_works(monkeypatch):
    """Regression: graph compiled with checkpointer=None still runs end-to-end."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    from config import get_settings
    get_settings.cache_clear()

    from unittest.mock import patch, MagicMock

    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"message": {"content": '{"task_type":"compliance"}'}}

    with patch("graph.nodes.intent_router.httpx.post", return_value=fake), \
         patch("graph.nodes.llm_caller.httpx.post", return_value=fake), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=[]):

        from graph.graph import build_graph
        compiled = build_graph(checkpointer=None)

        result = compiled.invoke(_make_state(request="hello"))

    assert len(result["chat_history"]) == 2  # turn was still appended (just not persisted)
```

- [ ] **Step 2: Run the tests to verify they fail or pass as expected**

Run: `python -m pytest tests/test_graph.py -v -k "persists_chat_history or without_checkpointer_still_works"`

Expected:
- `test_graph_without_checkpointer_still_works` likely PASSES already (Task 4 wired the append; checkpointer being None just means no persistence).
- `test_graph_with_checkpointer_persists_chat_history_across_invocations` should PASS too — but if it fails, the most likely cause is that LangGraph's `MemorySaver` requires specific config for the input-state merge. Adjust the test or the `chat_history` reducer call if needed.

If the first test fails: check whether LangGraph is replacing `chat_history` outright instead of running the reducer. The reducer is annotated on the field; LangGraph should apply it automatically.

- [ ] **Step 3: If the persistence test fails — debug**

If turn 2 doesn't see turn 1's history, the likely root cause is the reducer not being picked up. Verify:
- `chat_history: Annotated[list[dict], _history_reducer]` is correctly typed (Task 2).
- `_history_reducer` returns the merged list, not raises.
- The reducer is *not* wrapped in `Annotated[..., add_messages]` instead — it must be `_history_reducer`.

If the reducer is set up correctly but LangGraph still replaces instead of merging, log `state["chat_history"]` at the top of `intake` in the test to confirm what's loaded from the saver.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -q`

Expected: All tests pass (target: 64 prior + 4 state + 4 history_appender + 3 llm_caller + 2 skills + 2 human_review + 2 checkpointer + 4 api + 2 graph + 4 config = ~91 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_graph.py
git commit -m "test(graph): end-to-end chat_history persistence via MemorySaver"
```

---

## Task 12: Manual integration test + PR

Run the full stack and verify behavior in Langfuse. This task produces no code — only a verified working feature and a PR.

- [ ] **Step 1: Start the stack**

Run:
```bash
docker compose up -d
bash scripts/start.sh
```

Confirm Backend, Frontend, Langfuse all reachable:
- http://localhost:8000/health → `status: ok`
- http://localhost:8080 → Chainlit UI loads
- http://localhost:3000 → Langfuse UI loads

- [ ] **Step 2: Two-turn smoke test**

In Chainlit:

Turn 1: Send `"What's the standard indemnification cap in our service agreements?"`. Confirm a response renders.

Turn 2: Send `"And for software vendors specifically?"`. Confirm the response acknowledges the prior question (e.g., references "indemnification" without you re-stating it).

- [ ] **Step 3: Verify in Langfuse**

Open http://localhost:3000. Find the trace for turn 2. Open the `llm_caller` span. Confirm the `input` field is an array of messages and includes:
- A system message,
- A user message with content matching turn 1's request,
- An assistant message with content matching the trimmed turn 1 response,
- A user message with content matching turn 2's request.

If you don't see history in turn 2's `llm_caller`: stop and debug.

- [ ] **Step 4: Verify session boundary**

Refresh the browser. Send `"What did we just discuss?"`. Confirm the LLM cannot recall — a new `session_id` was minted on `on_chat_start`, so turn 1's history is gone. Expected: LLM says it has no context, or asks for clarification.

- [ ] **Step 5: Verify graceful degradation**

Stop Redis: `docker compose stop redis`.

Restart the backend: `pkill -f "uvicorn api.main" && uvicorn api.main:app --host 0.0.0.0 --port 8000 &`. Watch the log for `Checkpointer unavailable ... — running without memory`.

Send a query in Chainlit. Expected: it works, but turn 2 will not recall turn 1 (no memory).

Restart Redis: `docker compose start redis`. Restart the backend. Memory resumes.

- [ ] **Step 6: Open the PR**

Push the branch:
```bash
git push -u origin feat/within-session-memory
```

Open the PR:
```bash
gh pr create --title "feat: within-session conversation memory via Redis checkpointer" --body "$(cat <<'EOF'
## Summary
- Adds `chat_history` to LegalAgentState with a custom reducer (concat + cap at 2*N).
- New `history_appender` node trims assistant content to 300 chars and appends `(user, assistant)` after every turn.
- `llm_caller` and the two ReAct agent skills prepend `chat_history` into their prompts.
- API wires a `RedisSaver` checkpointer and passes `thread_id = session_id` to `graph.invoke`.
- Chainlit mints a UUID per chat session and propagates it.
- `interrupt()` gated behind `interrupt_enabled=False` so wiring the checkpointer doesn't accidentally pause `human_review` (resume is a separate spec).

## Test plan
- [x] Unit tests: state reducer (4), history_appender (4), llm_caller (3), agent skills (2), human_review gate (2), checkpointer factory (2), API wiring (4), graph end-to-end (2).
- [x] Manual: two-turn chat in Chainlit, verified turn 2 prompt contains turn 1 in Langfuse trace.
- [x] Manual: browser refresh → new session_id → no recall.
- [x] Manual: Redis down → app boots without memory; warning logged.

## Spec
docs/superpowers/specs/2026-05-19-within-session-memory-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**Spec coverage check:**

| Spec section | Plan task |
|---|---|
| Config additions | Task 1 |
| State model + reducer | Task 2 |
| `history_appender` node | Tasks 3, 4 |
| `llm_caller` injects history | Task 5 |
| Agent skills inject history | Task 6 |
| `interrupt_enabled` gate | Task 7 |
| `graph/checkpointer.py` factory + graceful degradation | Task 8 |
| API wires checkpointer + thread_id + `chat_history: []` in initial_state | Task 9 |
| Chainlit `on_chat_start` mints session_id + `on_message` passes it | Task 10 |
| End-to-end persistence test (MemorySaver) | Task 11 |
| Manual integration test | Task 12 |
| Rollback (set `CHECKPOINTER_ENABLED=false`) | Implicit in Task 1 + Task 9 graceful-degradation test |
| Field-reset contract (only `chat_history` accumulates) | Enforced in Task 9 Step 3 (explicit empty values in `initial_state`) |
| Known limitation: history-blind `intent_router` | Documented in spec, not addressed (out of scope) |

All spec requirements have a task. None left as TBD.

**Placeholder scan:** No "TODO", "TBD", "implement later", "appropriate error handling" — all code blocks are complete. Each step has the exact code to write.

**Type consistency:** `_history_reducer` named consistently in Task 2 (definition) and Task 11 (debugging hint). `chat_history` named consistently across all tasks. `build_checkpointer` returns optional `RedisSaver` in Task 8, consumed as that in Task 9.

Plan complete.
