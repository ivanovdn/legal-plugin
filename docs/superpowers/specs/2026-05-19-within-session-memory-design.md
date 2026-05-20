# Within-Session Conversation Memory — Design

> Date: 2026-05-19
> Branch: `feat/within-session-memory`
> Scope: within-session conversation history only. Cross-session memory (Qdrant `memory` collection) is a separate, follow-up spec.

## Context

Today the legal plugin has no conversation continuity:

- `build_graph(checkpointer=None)` accepts a checkpointer but the API never passes one.
- `graph.invoke(initial_state)` is called without a `config={"configurable": {"thread_id": ...}}` parameter.
- The Chainlit frontend does not send a `session_id` — the API mints a fresh UUID per request.
- `human_review` already calls `interrupt()`, but with no checkpointer attached the interrupt raises and is caught, so the graph just continues.

Result: every message is a fresh turn. The LLM cannot reference what was discussed earlier in the same chat session.

This spec adds within-session memory so that, within a single Chainlit browser session, the LLM sees prior `(user, assistant)` turns on each new request.

## Goals

1. The LLM receives a bounded, trimmed prior-turn history on every call within a session.
2. Memory is keyed by a stable `session_id` propagated end-to-end (Chainlit → API → graph).
3. All non-memory state fields remain one-shot (each turn replaces them; only `chat_history` accumulates).
4. The change is gracefully degradable: if Redis is unreachable at boot, the app runs without memory, exactly as today.
5. Test suite continues to run in ~1 second with no Docker dependency at the unit-test layer.

## Non-goals (deferred)

| Item | Why deferred |
|---|---|
| Cross-session memory (Qdrant `memory` collection) | Separate spec — user will scope after this one ships. |
| Resume after `interrupt()` (`POST /api/query/{id}/resume`) | Shares checkpointer infra but is a distinct feature. Interrupt is gated off behind `interrupt_enabled=False`. |
| History-aware `intent_router` (use prior task_type as hint) | Known limitation, called out below. Small follow-up. |
| Summarization of older turns | YAGNI at N=5, trim=300. |
| Survive Chainlit refresh / browser reopen | By design — that's the cross-session spec. |
| Admin endpoint to inspect / clear a thread's memory | Not requested. Manual `redis-cli FLUSHDB` is fine for now. |

## Approach

**Approach A — dedicated `chat_history` field with explicit append node.** Chosen over (B) repurposing `messages` because the existing `messages` field is currently a per-turn LLM input buffer that every skill *sets*; repurposing it would force changes to every skill's prompt-construction code. Chosen over (C) resume-only because (C) doesn't deliver the asked feature.

- Bounded recall: rolling window with per-message trim.
- `N = 5` turns retained (10 messages — 5 user + 5 assistant).
- Each historical assistant response trimmed to 300 chars + `[...]` marker.
- User requests stored verbatim (typically 50–300 chars).
- Worst-case history payload: ~10 × 300 chars ≈ ~3 KB ≈ ~750 tokens. Bounded.

### Why trim assistant content but not user content

Assistant responses for contract generation can be 4 KB–20 KB (full draft + deviation report). The full draft is already preserved in the Chainlit side panel as a downloadable file, so trimming it in chat history doesn't lose user-recoverable information. User requests are small by nature.

## Architecture

```
[Chainlit]              [FastAPI]                          [LangGraph + Redis]
on_chat_start  ──┐      POST /api/query                    RedisSaver (thread_id = session_id)
  session_id     │       │
on_message ──────┼──────▶│  thread_id := session_id              │
  pass session_id│       │  initial_state.chat_history := []     │
                 │       │  graph.invoke(state, config)─────────▶│
                 │       │                                       │ load saved state for thread
                 │       │                                       │   (chat_history merged via reducer)
                 │       │                                       │
                 │       │                                       │ intake → intent_router → skill →
                 │       │                                       │ rag_retriever → llm_caller
                 │       │                                       │   (prepends chat_history to prompt)
                 │       │                                       │ → risk_assessor → (output_formatter)
                 │       │                                       │ → history_appender  [NEW]
                 │       │                                       │   (appends current turn, capped & trimmed)
                 │       │                                       │ → memory_writer → END
                 │       │                                       │ persist new state
                 │       │◀──────────────────────────────────────│
                 │◀──────│
                 │  render full response in side panel
                 │  (untrimmed — side-panel files independent of chat_history)
```

## State model

```python
# graph/state.py
from typing import Annotated
from config import get_settings

def _history_reducer(old: list[dict], new: list[dict]) -> list[dict]:
    """Concatenate, then cap to last 2*N entries (N user + N assistant pairs)."""
    n = get_settings().chat_history_n_turns
    return (old + new)[-(2 * n):]

class LegalAgentState(TypedDict):
    # ... all existing fields unchanged ...
    chat_history: Annotated[list[dict], _history_reducer]   # NEW
```

`config.get_settings()` is `@lru_cache`'d, so reducer invocation is essentially free.

**Field reset contract:** every state field *except* `chat_history` is replaced on each turn — the API sends a fresh `initial_state` for those fields. Only `chat_history` has a reducer, so it's the only field that accumulates across invocations of the same `thread_id`. This is critical: without explicit replacement, stale `task_type` / `retrieved_chunks` / `llm_response` from turn N−1 would leak into turn N.

## Components touched

| Component | Change |
|---|---|
| `requirements.txt` | Add `langgraph-checkpoint-redis`. |
| `config.py` | Add `redis_url`, `chat_history_n_turns: int = 5`, `chat_history_trim_chars: int = 300`, `checkpointer_enabled: bool = True`, `interrupt_enabled: bool = False`. |
| `graph/checkpointer.py` | **NEW** module — `build_checkpointer()` returns a `RedisSaver` or `None`. |
| `graph/state.py` | Add `chat_history` field with `_history_reducer`. |
| `graph/graph.py` | Insert `history_appender` between `output_formatter` and `memory_writer`. |
| `graph/nodes/history_appender.py` | **NEW** node — trims and appends current `(user, assistant)` pair. |
| `graph/nodes/llm_caller.py` | Prepend `state["chat_history"]` to the message list sent to Ollama. Log `history_turns` to Langfuse. |
| `graph/nodes/human_review.py` | Wrap `interrupt()` call in `if settings.interrupt_enabled:` to keep current behavior. |
| `skills/contract_generation/contract_generation.py` | Inject `state["chat_history"]` into the ReAct agent's initial messages. |
| `skills/legal_research.py` | Same as above. |
| `api/routes/query.py` | Build checkpointer at module init; pass it to `build_graph()`; pass `config={"configurable": {"thread_id": session_id}}` to `graph.invoke`; ensure `initial_state["chat_history"] = []`. |
| `frontend/app.py` | Mint a `session_id` UUID in `on_chat_start`, store in `cl.user_session`, pass on every `submit_query`. |
| `frontend/api_client.py` | No change — already accepts `session_id`. Just exercised by the new call site. |
| `tests/` | New tests as described in "Testing" below. |

## Wiring details

### `graph/checkpointer.py`

```python
"""Redis checkpointer factory — used to wire LangGraph thread persistence."""
import logging
from typing import Optional
from langgraph.checkpoint.redis import RedisSaver
from config import get_settings

logger = logging.getLogger(__name__)


def build_checkpointer() -> Optional[RedisSaver]:
    """Build a Redis checkpointer. Returns None on failure — app runs without memory."""
    settings = get_settings()
    try:
        saver = RedisSaver.from_conn_string(settings.redis_url)
        saver.setup()  # creates index/keys if missing
        logger.info("Redis checkpointer initialized at %s", settings.redis_url)
        return saver
    except Exception as e:
        logger.warning("Checkpointer unavailable (%s) — running without memory", e)
        return None
```

### `graph/nodes/history_appender.py`

```python
"""History appender — pushes current (user, assistant) pair into chat_history."""
import logging
from langfuse.decorators import observe

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def _trim(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "[...]"


@observe(name="history_appender")
def history_appender(state: LegalAgentState) -> dict:
    """Return the two new messages to append. The state reducer caps the list."""
    settings = get_settings()
    user_msg = {"role": "user", "content": state.get("request", "")}
    assistant_msg = {
        "role": "assistant",
        "content": _trim(state.get("llm_response", ""), settings.chat_history_trim_chars),
    }
    logger.info("[history_appender] appended turn (assistant_len=%d)", len(assistant_msg["content"]))
    return {"chat_history": [user_msg, assistant_msg]}
```

Returns *just* the partial state update (`{"chat_history": [...]}`), letting LangGraph apply the reducer. Does not mutate `state`.

### `graph/graph.py` — edge changes

```python
# OLD:
# graph.add_edge("output_formatter", "memory_writer")

# NEW:
graph.add_node("history_appender", history_appender)
graph.add_edge("output_formatter", "history_appender")
graph.add_edge("history_appender", "memory_writer")
```

### `graph/nodes/llm_caller.py` — message construction

```python
chat_history = state.get("chat_history", [])
messages = state.get("messages") or [
    {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
    {"role": "user",   "content": f"Context:\n{context}\n\nRequest: {state['request']}"},
]
# Insert chat_history right after the system message (if there is one)
if messages and messages[0].get("role") == "system":
    out_messages = [messages[0], *chat_history, *messages[1:]]
else:
    out_messages = [*chat_history, *messages]
# Send out_messages to Ollama.
```

### `api/routes/query.py`

```python
def _get_graph():
    global _graph
    if _graph is None:
        settings = get_settings()
        cp = build_checkpointer() if settings.checkpointer_enabled else None
        _graph = build_graph(checkpointer=cp)
    return _graph

# in submit_query:
initial_state["chat_history"] = []
config = {"configurable": {"thread_id": session_id}}
result = graph.invoke(initial_state, config=config)
```

### Chainlit `frontend/app.py`

```python
import uuid

@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("session_id", str(uuid.uuid4()))
    # ... existing health-check + welcome message ...

@cl.on_message
async def on_message(message: cl.Message):
    session_id = cl.user_session.get("session_id", "")
    # ... existing logic ...
    result = await submit_query(
        request=message.content,
        user_id=user_id,
        uploaded_text=uploaded_text,
        session_id=session_id,   # NEW — was absent
    )
```

### `graph/nodes/human_review.py` — interrupt gate

```python
settings = get_settings()
if settings.interrupt_enabled:
    from langgraph.types import interrupt
    review = interrupt({...})
    # ... existing review-handling code ...
else:
    logger.info("[human_review] interrupt disabled by config — flagging and continuing")
```

This makes the side effect of wiring the checkpointer explicit. Today `interrupt()` falls through via an exception; with the checkpointer it would actually pause. The flag keeps current behavior until a follow-up spec implements resume.

## Data flow walk-through

### Turn 1 — fresh chat: "Generate an NDA for ACME with 2-year term"

```
[Chainlit]   on_chat_start → cl.user_session["session_id"] = "sess-abc123"
[Chainlit]   on_message → submit_query(..., session_id="sess-abc123")
[FastAPI]    thread_id = "sess-abc123"
             initial_state.chat_history = []
             graph.invoke(state, config={"configurable": {"thread_id": "sess-abc123"}})
[RedisSaver] no prior checkpoint → use initial_state
[Graph]      intake → intent_router → skill_dispatcher → contract_generation
             → rag_retriever (skipped — agent skill set llm_response)
             → llm_caller (skipped — agent skill set llm_response)
             → risk_assessor → human_review (interrupt disabled, logs + continues)
             → output_formatter
             → history_appender returns {"chat_history": [user_msg, asst_msg]}
                 user_msg     = {"role":"user", "content":"Generate an NDA for ACME with 2-year term"}
                 asst_msg     = {"role":"assistant","content": llm_response[:300] + "[...]"}
             → reducer: [] + [user_msg, asst_msg] → cap[-10:] → [user_msg, asst_msg]
             → memory_writer (SQLite audit) → END
[RedisSaver] persist final state under "sess-abc123" — chat_history length 2
[Chainlit]   renders full untrimmed contract in side panel (independent of chat_history)
```

### Turn 2 — same chat: "Make the term 3 years instead"

```
[Chainlit]   on_message → same session_id "sess-abc123"
[FastAPI]    initial_state.chat_history = []  (always)
             graph.invoke(state, config={"configurable": {"thread_id": "sess-abc123"}})
[RedisSaver] loads prior checkpoint:
                 state.chat_history = [user_msg_T1, asst_msg_T1]
             reducer merges old + new ([] adds nothing) → still [user_msg_T1, asst_msg_T1]
             other state fields: replaced by initial_state (one-shot)
                 — request, task_type, llm_response, retrieved_chunks, report, etc. all reset
[Graph]      intake (chat_history present, leaves alone)
             intent_router classifies CURRENT request only — see "Known limitation"
             → skill → (rag_retriever) → llm_caller / agent
                 LLM messages built as:
                     SYSTEM: <skill prompt>
                     USER:    "Generate an NDA for ACME with 2-year term"     ← from history
                     ASSIST:  "DRAFT NDA — ... [...]"                          ← from history (trimmed)
                     USER:    "Make the term 3 years instead"                  ← current turn
                 → LLM has enough context to resolve "the term".
             → output_formatter → history_appender (appends turn 2 pair)
             → reducer: prior 2 + new 2 → cap[-10:] → 4 messages
             → memory_writer → END
[RedisSaver] persist updated state — chat_history length 4
```

## Known limitations

### `intent_router` is history-blind

`intent_router` classifies the current request standalone. On turn 2 above, it sees only `"Make the term 3 years instead"` — the LLM may not be able to classify that as `contract_generation` from the fragment alone and may fall back to its default (`research`). The downstream LLM call still has full history, so the *answer* will be correct, but *skill routing* can be wrong.

A follow-up fix would pass `chat_history` or last-turn `task_type` as a hint into `intent_router`. **Out of scope.**

### `task_type` is never sticky

By the same field-reset contract, `task_type` always re-classifies from scratch. A user iterating on a draft will be re-routed each turn. Acceptable for v1 — same skill is usually picked when the conversation stays on-topic.

## Error handling

| Failure | Behavior |
|---|---|
| Redis unreachable at app start | `build_checkpointer()` returns `None`. `build_graph(None)` works exactly as today. App boots; no memory; no crash. Warning logged. |
| Redis goes down mid-session | `RedisSaver` raises on next checkpoint write. Surfaces as 500 from `/api/query` with error in response envelope. Acceptable for v1. |
| Corrupted / stale-shape state in Redis (e.g. after a deploy) | LangGraph raises on state load. Same 500 surfaces. Recovery: `redis-cli FLUSHDB` on the Redis service. Sessions are ephemeral conversation memory, not persisted records. |
| Chainlit reload / browser refresh | `cl.user_session` resets → new `session_id` → new thread → no recall. By design. |
| `session_id` missing from request body | API mints a fresh UUID (existing behavior). Request runs without history. Logged WARN. |
| API accidentally sends a non-empty `chat_history` in `initial_state` | Reducer concatenates — would duplicate the prefix. Mitigation: API always sets `chat_history: []` in `initial_state`. Asserted in a test. |

## Testing

### Unit-test layer (no Docker dependency)

`tests/test_state.py` (new file)
- `test_history_reducer_concatenates` — empty + `[a, b]` → `[a, b]`.
- `test_history_reducer_caps_at_2N` — N=5, old of 10 + new of 4 → last 10 entries.
- `test_history_reducer_preserves_order` — verify FIFO ordering after cap.

`tests/test_nodes.py` (extend)
- `test_history_appender_appends_user_and_assistant_pair`
- `test_history_appender_trims_long_assistant_response`
- `test_history_appender_does_not_trim_short_response`
- `test_history_appender_does_not_trim_user_request`

`tests/test_nodes.py` — extend existing `llm_caller` tests
- `test_llm_caller_prepends_chat_history_between_system_and_user`
- `test_llm_caller_works_when_chat_history_empty`

`tests/test_graph.py` (extend)
- `test_graph_with_checkpointer_persists_chat_history_across_invocations` — use LangGraph's in-memory `MemorySaver` (not Redis) to keep tests Docker-free. Invoke twice with the same `thread_id`; assert turn 2's LLM call sees turn 1's messages.
- `test_graph_without_checkpointer_works_unchanged` — graceful-degradation regression check.

`tests/test_api.py` (extend)
- `test_submit_query_passes_thread_id_to_graph` — mock `graph.invoke`; assert it's called with `config={"configurable": {"thread_id": session_id}}`.
- `test_submit_query_with_repeated_session_id_reuses_thread` — call twice with same `session_id`; assert the same `thread_id` is propagated each time.

**Total: ~12 new tests. Suite still runs in ~1 second.**

### Integration test (manual, documented in the PR)

1. Start full stack: `docker compose up -d && bash scripts/start.sh`.
2. Open Chainlit at http://localhost:8080.
3. Turn 1: "Generate an NDA for ACME with 2-year term." Confirm response renders.
4. Turn 2: "Make the term 3 years instead." Confirm response references the previously drafted NDA.
5. Open Langfuse at http://localhost:3000. Find the trace for turn 2. Inspect the `llm_caller` span — confirm its `input` field contains both turn 1's user message and turn 1's (trimmed) assistant message.
6. Refresh the browser. Confirm a new `session_id` is minted (turn 1 history is no longer visible to the LLM).

## Rollback

If the feature misbehaves in any way:

- Set `CHECKPOINTER_ENABLED=false` in `.env` and restart the API. The graph is built without a checkpointer; behavior reverts to current.
- The added `chat_history` field stays in state but is empty on every invocation. No data corruption.

## Open questions

None — design is fully specified. Implementation plan is the next step (`writing-plans` skill).
