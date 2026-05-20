# Resume After Interrupt — Design

> Date: 2026-05-20
> Branch: `feat/resume-after-interrupt`
> Scope: Make LangGraph's `interrupt()` actually pause the graph at `human_review`, expose a working `POST /api/query/{session_id}/resume` endpoint, and wire Chainlit's action buttons to call it. Adds an iterative "Request Changes" loop so attorneys can refine drafts in-place.

## Context

The within-session memory feature (merged 2026-05-20) wired the Redis checkpointer and propagated `thread_id = session_id` end-to-end. But `human_review.interrupt()` is gated behind `interrupt_enabled=False` because resume wasn't implemented. Today:

- `human_review` flags `awaiting_review=True`, logs a warning, and falls through to `output_formatter`. The graph completes in one shot.
- `POST /api/query/{session_id}/resume` returns the placeholder error `"Resume not yet implemented — interrupt_enabled=False"`.
- Chainlit's three action buttons (`approve`, `request_changes`, `reject`) act locally — they don't call any API.

This spec finishes the loop: the graph genuinely pauses at `human_review`, the API surfaces the partial state, the resume endpoint propagates the attorney's verdict back into the graph, and Chainlit's buttons fire real resume calls. A new iteration path lets attorneys say "request changes with these notes" and have the skill regenerate.

## Goals

1. `human_review` pauses the graph via `interrupt()` when both `interrupt_enabled=True` (new default) and a checkpointer is attached.
2. `POST /api/query` returns immediately on interrupt with the partial draft and a clear `awaiting_review` flag.
3. `POST /api/query/{session_id}/resume` accepts the attorney's verdict (approve / reject / request changes with notes) and returns either the final result or the next interrupt payload.
4. On "request changes with notes," the graph loops back through the same skill with the notes injected, regenerates, and re-enters `human_review` for the new draft.
5. The loop is bounded: maximum 3 iterations, then the graph forces an exit with un-incorporated notes attached to the report.
6. Pending checkpoints expire after 24 hours of inactivity (TTL refresh on every successful invoke/resume).
7. Chainlit's three action buttons call the real resume endpoint and re-render the side panel for iteration loops.
8. The API contract is forward-compatible with the upcoming Word add-in (same request/response shapes; granularity is a skill concern).

## Non-goals (deferred)

| Item | Why deferred |
|---|---|
| WebSocket / SSE streaming of partial state | Request-response is enough for v1; streaming is a separate spec when latency demands it. |
| Concurrent resume safety (double-click guard) | Single-attorney sessions in v1 don't need it; add request deduplication later if users hit it. |
| Word add-in client wiring | Same API works; client is a separate consumer spec. |
| Multi-attorney review (delegation, parallel reviewers) | v1 assumes the same attorney who started the session resumes it. |
| Auto-extracting `attorney_notes` into Qdrant `memory` collection (preferences) | Belongs to the cross-session memory spec. |
| `GET /api/query/{sid}/status` enrichment | Currently `unknown`; enrich only if a UI consumer asks for it. |
| Resume after restart of FastAPI process | Checkpoints survive because they live in Redis, but recompiling the graph means consumers must use the same `thread_id` to pick up where they left off. No graceful resume across deploys is built. |

## Approach

**Approach A — Loop inside the graph via `skill_dispatcher`.** Chosen over (B) a dedicated `regenerate_with_notes` node (extra node for trivial logic; YAGNI) and over (C) client-orchestrated loops (loses the prior-draft context that's the whole point).

### Bounding values

- `max_review_iterations = 3` (config; loop terminates after 3 retries).
- `checkpoint_ttl_seconds = 86400` (24h; refreshed on every successful invoke).
- `interrupt_enabled = True` becomes the new default. Dev/test workflows that don't want pauses can set `INTERRUPT_ENABLED=false` to keep current behavior.

### Why route back through `skill_dispatcher`

It reuses the existing routing logic (the skill is picked by `task_type`, which doesn't change across iterations). Intent classification (`intent_router`) is upstream of `skill_dispatcher`, so we don't pay for re-classification on each loop. The graph's existing wiring `skill_dispatcher → skill → rag_retriever → llm_caller → risk_assessor → human_review` works unchanged on the loop-back.

## Architecture

```
[Chainlit]                            [FastAPI]                                    [LangGraph + RedisSaver]
on_message                            POST /api/query                              interrupt_enabled=True
  └──▶ submit_query ─────────────────▶│ thread_id := session_id                    │
                                      │ initial_state.chat_history := []           │
                                      │ initial_state.review_iterations := 0       │
                                      │ graph.invoke(state, config) ──────────────▶│
                                      │                                            │ intake → ... → skill →
                                      │                                            │ ... → human_review:
                                      │                                            │   interrupt({partial})  ← pauses
                                      │   ◀── partial result + awaiting_review=true│
on_review_buttons rendered ◀──────────│                                            │
                                      │                                            │
[attorney clicks "Request Changes"]   │                                            │
on_request_changes                    │                                            │
  └──▶ cl.AskUserMessage(notes) ──▶   │                                            │
  └──▶ resume_query ─────────────────▶│ POST /api/query/{sid}/resume                │
                                      │ validate sid exists in Redis               │
                                      │ graph.invoke(Command(resume={verdict})) ───▶│
                                      │                                            │ human_review reads verdict
                                      │                                            │ notes-only + iter<3:
                                      │                                            │   attorney_notes := notes
                                      │                                            │   review_iterations += 1
                                      │                                            │   llm_response := ""
                                      │                                            │   retrieved_chunks := []
                                      │                                            │   messages := []
                                      │                                            │   route_review → skill_dispatcher
                                      │                                            │ skill regenerates (sees notes)
                                      │                                            │ ... → human_review:
                                      │                                            │   interrupt({new partial})
                                      │   ◀── new partial + awaiting_review=true   │
on_review_buttons re-render ◀─────────│                                            │
                                      │                                            │
[attorney clicks "Approve"]           │                                            │
on_approve                            │                                            │
  └──▶ resume_query ─────────────────▶│ POST /api/query/{sid}/resume                │
                                      │ graph.invoke(Command(resume={approved})) ──▶│
                                      │                                            │ human_review:
                                      │                                            │   awaiting_review := False
                                      │                                            │   route_review → output_formatter
                                      │                                            │ → history_appender → memory_writer → END
                                      │   ◀── final report                         │
final report renders, no buttons ◀────│                                            │
```

## State model

```python
class LegalAgentState(TypedDict):
    # ... existing fields ...
    awaiting_review: bool        # existing — True at interrupt, False at approval/finalization
    attorney_notes: str          # existing — channel for "what to change," written on loop-back
    review_iterations: int       # NEW — count of completed review cycles; capped at max_review_iterations
```

**No new reducers.** `review_iterations` and `attorney_notes` are single-write per cycle (only `human_review` touches them), so default last-write-wins TypedDict semantics are sufficient.

**Field reset on loop-back.** When `human_review` decides to loop:

```python
state["attorney_notes"] = notes
state["review_iterations"] += 1
state["llm_response"] = ""           # so llm_caller's agent-skill short-circuit doesn't fire
state["retrieved_chunks"] = []       # force fresh retrieval if the skill needs it
state["messages"] = []               # plain skills rebuild from system prompt + new request context
state["awaiting_review"] = False     # cleared so route_review sees the routing decision
```

This forces the next skill invocation to run cleanly with `attorney_notes` as new context, instead of short-circuiting on stale state.

## Components touched

| Component | Change |
|---|---|
| `config.py` | Flip `interrupt_enabled` default to `True`. Add `max_review_iterations: int = 3` and `checkpoint_ttl_seconds: int = 86400`. |
| `graph/state.py` | Add `review_iterations: int` field. |
| `graph/nodes/human_review.py` | Process resume value (already enabled — just remove the `interrupt_enabled` guard since we now default it to True; keep the env-flag override). Apply the 4-way verdict logic and field-reset described above. Add `report_notes_unincorporated` to state when cap hit. |
| `graph/graph.py` | Replace the unconditional edge `human_review → output_formatter` with a conditional edge keyed off `route_review(state)`. Two outcomes: `"output_formatter"` (terminal) and `"skill_dispatcher"` (loop-back). |
| `graph/checkpointer.py` | Add `refresh_ttl(thread_id, redis_client)` helper. Called by the API after each successful invoke. Issues `EXPIRE` on `checkpoint:{thread_id}:*` and `checkpoint_write:{thread_id}:*` keys. |
| `api/routes/query.py` | `submit_query` detects `__interrupt__` in result, returns partial state with `awaiting_review=true` and `interrupt_payload`. Replace placeholder `resume_query` with: validate checkpoint exists, call `graph.invoke(Command(resume={...}))`, return next state. Add 404 path for unknown session_id. Call `refresh_ttl` after each successful invoke. |
| `api/models.py` | No schema changes — `ApiResponse.data` is already `dict`. Document the new `interrupt_payload` and `review_iterations` keys in field descriptions. |
| `skills/contract_generation/contract_generation.py` | Read `state["attorney_notes"]`. If non-empty, append `"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n{notes}"` to the agent's user message. |
| `skills/legal_research.py` | Same `attorney_notes` injection. |
| `skills/contract_review.py` | Same. (Plain skill — inject into `messages[-1]` user content or as a system addendum.) |
| `skills/compliance_check.py` | Same. |
| `skills/drafting.py` | Same. |
| `frontend/api_client.py` | Add `async def resume_query(session_id, approved, notes, revised_response)`. |
| `frontend/app.py` | Rewire `on_approve` / `on_request_changes` / `on_reject` to call `resume_query`. `on_request_changes` uses `cl.AskUserMessage` to capture notes synchronously. New helper `_render_resume_result` handles both terminal and loop-back responses (the latter re-renders the side panel with new buttons). |
| `tests/` | ~12 new tests (see Testing). |

## Wiring details

### `human_review` decision logic

```python
@observe(name="human_review")
def human_review(state: LegalAgentState) -> LegalAgentState:
    state["awaiting_review"] = True
    settings = get_settings()
    if not settings.interrupt_enabled:
        logger.info("[human_review] interrupt disabled — flagging and continuing")
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
        state["attorney_notes"] = notes  # captured for audit even on approval
        logger.info("[human_review] approved by attorney")
        return state

    if revised:
        state["llm_response"] = revised
        state["attorney_notes"] = notes
        state["awaiting_review"] = False
        logger.info("[human_review] revised by attorney")
        return state

    if notes and iterations < max_iter:
        # Loop-back: regenerate with attorney notes
        state["attorney_notes"] = notes
        state["review_iterations"] = iterations + 1
        state["llm_response"] = ""
        state["retrieved_chunks"] = []
        state["messages"] = []
        state["awaiting_review"] = False  # cleared so route_review picks skill_dispatcher
        logger.info("[human_review] loop-back iteration %d/%d", iterations + 1, max_iter)
        return state

    # Cap hit or pure reject without notes/revised → exit with un-incorporated notes attached
    state["report_notes_unincorporated"] = notes
    state["awaiting_review"] = False
    logger.info("[human_review] terminal: cap=%s, notes=%s", iterations >= max_iter, bool(notes))
    return state
```

**Note:** The state field `report_notes_unincorporated` is added implicitly via TypedDict — it's not declared on the schema (LangGraph tolerates extra keys), and `output_formatter` reads `state.get("report_notes_unincorporated", "")` to include in the report. We add the field to the TypedDict for typing clarity.

### `route_review` function in `graph/graph.py`

```python
def route_review(state: LegalAgentState) -> str:
    """Decide where to go after human_review's verdict."""
    if state.get("awaiting_review", False):
        # Should not happen — human_review always clears this; defensive.
        return "output_formatter"
    if not state.get("llm_response", ""):
        # Loop-back signal: llm_response was cleared by human_review
        return "skill_dispatcher"
    return "output_formatter"
```

Why "no `llm_response` means loop back"? `human_review` clears `llm_response` exactly when it loops back. Approval and revision paths both leave `llm_response` set. Cap-hit also leaves the prior `llm_response` set. So an empty `llm_response` is a unique signal for "regenerate."

### Conditional edge in `build_graph`

```python
# OLD:
# graph.add_edge("human_review", "output_formatter")

# NEW:
graph.add_conditional_edges("human_review", route_review, {
    "skill_dispatcher": "skill_dispatcher",
    "output_formatter": "output_formatter",
})
```

### API resume endpoint

```python
from langgraph.types import Command

@router.post("/query/{session_id}/resume", response_model=ApiResponse)
def resume_query(session_id: str, body: ResumeRequest):
    graph = _get_graph()
    config = {"configurable": {"thread_id": session_id}}

    # Validate checkpoint exists. If the thread doesn't appear in the saver,
    # report 404 cleanly instead of letting LangGraph raise generically.
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
        return ApiResponse(
            status="ok",
            data=_payload_from_result(result, session_id),
        )
    except Exception as e:
        logger.exception("resume: graph invoke failed for %s", session_id)
        return ApiResponse(status="error", errors=[str(e)])
```

Where `_payload_from_result` is a shared helper used by both `submit_query` and `resume_query`:

```python
def _payload_from_result(result: dict, session_id: str) -> dict:
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
```

### TTL refresh helper

```python
# graph/checkpointer.py

def refresh_ttl(session_id: str) -> None:
    """Refresh Redis TTL on all checkpoint keys for this session."""
    settings = get_settings()
    if not settings.checkpointer_enabled:
        return
    try:
        from redis import Redis
        from urllib.parse import urlparse
        url = urlparse(settings.redis_url)
        r = Redis(host=url.hostname, port=url.port or 6379, password=url.password)
        for key in r.scan_iter(match=f"checkpoint:{session_id}:*"):
            r.expire(key, settings.checkpoint_ttl_seconds)
        for key in r.scan_iter(match=f"checkpoint_write:{session_id}:*"):
            r.expire(key, settings.checkpoint_ttl_seconds)
    except Exception as e:
        logger.warning("refresh_ttl failed for %s: %s", session_id, e)
```

The helper is intentionally fire-and-forget — TTL refresh failures don't break the response.

### Skill prompt addendum (canonical pattern)

```python
chat_history = state.get("chat_history", []) or []
attorney_notes = (state.get("attorney_notes") or "").strip()

base_message = "\n".join(context_parts)
if attorney_notes:
    base_message += (
        f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
        f"{attorney_notes}"
    )

agent_messages = [*chat_history, {"role": "user", "content": base_message}]
result = agent.invoke({"messages": agent_messages})
```

Same pattern in all five skill files. Plain skills (`contract_review`, `compliance_check`, `drafting`) inject into their `messages` user content; agent skills (`contract_generation`, `legal_research`) inject into the agent's user message.

### Chainlit wiring

See Section 3 above for the full code. Three callbacks (`on_approve`, `on_request_changes`, `on_reject`) call `resume_query` via `api_client`. `on_request_changes` captures notes via `cl.AskUserMessage`. A shared helper `_render_resume_result` handles both loop-back (new draft + buttons) and terminal (final report) responses.

## Error handling

| Failure | API behavior | Frontend behavior |
|---|---|---|
| `/api/resume` with unknown `session_id` (TTL expired or never existed) | `{"status":"error","errors":["session expired or not found"]}` | Render "This review session has expired"; clear `pending_review_text` in `cl.user_session`. |
| `/api/resume` body missing `approved` field | 422 from FastAPI validation | Bubble error message. |
| Graph errors mid-resume (LLM call fails during regeneration) | `{"status":"error","errors":["<exception>"]}`; checkpoint untouched so retry is possible | Show error to attorney; retain the action buttons so they can retry. |
| Redis goes down during resume | LangGraph raises; caught at API level; 500-style envelope returned | Same as above; retry possible once Redis recovers. |
| Iteration cap hit | Graph exits normally with `awaiting_review=false` and `report_notes_unincorporated` populated | Render final report; include the un-incorporated notes block prominently. |
| Concurrent resume calls for the same `session_id` | LangGraph checkpointer is single-writer per thread; second call may raise or no-op | Document as "do not double-click Approve." Not optimized for v1. |
| Attorney closes browser mid-review | Backend has no awareness; checkpoint sits until 24h TTL expires | If they reopen with the same `session_id` (currently Chainlit doesn't surface it across reloads) they could resume. v1: let it expire. |
| `interrupt_enabled=false` (disabled override) | `human_review` short-circuits as today — no pause, no resume needed | Backward-compatible with prior behavior; existing tests still pass. |

## Testing

### Unit tests (no Redis required; use `MemorySaver`)

`tests/test_nodes.py` — extend `human_review` tests:
- `test_human_review_approved_routes_to_output_formatter`
- `test_human_review_revised_response_uses_revised_text`
- `test_human_review_notes_only_loops_back_and_increments_iterations`
- `test_human_review_iteration_cap_hit_exits_with_unincorporated_notes`
- `test_human_review_pure_reject_no_notes_exits_normally`

`tests/test_graph.py`:
- `test_route_review_returns_skill_dispatcher_when_llm_response_empty`
- `test_route_review_returns_output_formatter_when_llm_response_set`
- `test_graph_interrupt_returns_state_with_memory_saver`
- `test_graph_resume_with_approved_completes`
- `test_graph_resume_with_notes_loops_back_and_regenerates_with_notes`
- `test_graph_resume_iteration_cap_terminates_after_3_loops`

`tests/test_api.py`:
- `test_submit_query_returns_interrupt_payload_when_awaiting_review`
- `test_resume_query_calls_graph_with_command_resume`
- `test_resume_query_404_when_session_unknown`

`tests/test_skills.py` — one regression per skill:
- `test_<skill_name>_includes_attorney_notes_in_prompt` × 5

**Total: ~17 new tests.** Suite should still run in <2 seconds.

### Manual integration test (documented in PR)

1. Start full stack with `INTERRUPT_ENABLED=true`.
2. Chainlit: send `Generate a service agreement for Vertex Systems Inc., 2-year term`.
3. Wait for draft + buttons.
4. Click **Request Changes** → enter `Add a confidentiality clause.`
5. Confirm a new draft renders with confidentiality included; buttons present again.
6. Click **Approve** → confirm final report renders, no buttons.
7. Verify in Langfuse: trace shows two `human_review` spans, two skill spans, one `output_formatter` span.
8. Verify in Redis: `KEYS checkpoint:*` for the session — keys disappear after 24h.
9. Test expired-session path: wait/expire a session manually (`DEL` the keys), click Approve, confirm "session expired" message.

## Rollback

If the iteration loop misbehaves or the resume endpoint causes issues:

- Set `INTERRUPT_ENABLED=false` in `.env` and restart the API. `human_review` short-circuits as before; behavior reverts exactly to the prior memory-feature merge state.
- Optionally set `MAX_REVIEW_ITERATIONS=0` to disable the loop while keeping the pause/resume mechanics for approve/reject only.

## Open questions

None — design is fully specified. Implementation plan is the next step (`writing-plans` skill).
