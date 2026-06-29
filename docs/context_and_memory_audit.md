# Context & Memory Audit

> **Purpose:** A ground-truth audit of *what context the agent sees* and *how that context
> flows from message to message* — especially in the Word **Chat** tab. Written to understand
> the current system before extending it. Every claim below is cited to a file:line.
>
> Last audited: 2026-06-26 (commit on `main` after the SOW-vs-MSA merge).

---

## TL;DR

The system has **five distinct context/memory stores**, and they are easy to conflate:

| # | Store | What it holds | Lifetime / scope | Size cap | Fed to the LLM? |
|---|-------|---------------|------------------|----------|-----------------|
| 1 | **The open document** (`uploaded_docs`) | Full current text of the Word doc | Re-sent fresh **every turn** by the client; not retained backend-side | none (whole doc inlined) | ✅ inlined in the user message |
| 2 | **`chat_history`** | Prior (user, assistant) turns | Per `session_id`, in Redis | **10 messages = 5 turns**; each assistant msg **trimmed to 300 chars** | ✅ injected between system + user |
| 3 | **Redis checkpointer** | Full `LegalAgentState` snapshot per thread | Per `thread_id = session_id`, **TTL 24 h** (refreshed each call) | — | indirectly (it's what restores #2) |
| 4 | **Qdrant** (`legal_docs`) | RAG corpus + the governing MSA | Long-lived document store | — | only on RAG / SOW-review paths |
| 5 | **Langfuse** | Traces, token usage | Observability store | — | ❌ never fed back |

**The single most important fact for the Chat tab:** every turn re-sends the **entire current
document** inlined in the user message, plus the **last 5 turns** of conversation (with each
assistant reply trimmed to **300 characters**). The document is *not* remembered across turns —
it is *resupplied* each turn. The conversation *is* remembered, but only as short stubs.

**Second most important fact:** the **SOW-vs-MSA** comparison only happens in the **Findings tab**
(`contract_review` skill). The **Chat tab never attaches the MSA** — see [§7](#7-notable-behaviors-limits-and-risks).

---

## 1. The two Word entry points share one session

`clients/word/src/App.tsx:11-15` generates **one `sessionId` per task-pane lifetime** (a
`crypto.randomUUID()` in a `useState` initializer). Both tabs receive it:

- **Findings tab** → `submitReview(docText, sessionId)` → `task_type: "contract_review"` ([api.ts:44](../clients/word/src/api.ts))
- **Chat tab** → `chatQuery(question, docText, sessionId)` → `task_type: "research"` ([api.ts:65](../clients/word/src/api.ts))

Because they share `sessionId`, **the contract review and all chat turns land in the same
`chat_history` thread** (this is intentional — see the comment at `api.ts:54-64`). So the first
chat turn's history already contains the review turn… but only a **300-char stub** of it (see §4).

`sessionId` is **per-pane, not per-document**. Closing/reopening the pane mints a new session
(fresh history). Switching documents *without* closing the pane keeps the old `sessionId` —
so `chat_history` can reference a document you've since navigated away from.

---

## 2. What the Chat tab sends, every turn

`ChatTab.send()` ([ChatTab.tsx:51-99](../clients/word/src/components/ChatTab.tsx)):

1. Reads the **current** document text via `readBody()` → `body.getReviewedText("current")`
   (accepts tracked changes for *extraction* so filled-in redlines aren't mis-read as blanks —
   per CLAUDE.md).
2. `POST /api/query` with:
   ```jsonc
   {
     "request":       "<the user's question>",
     "task_type":     "research",          // routes straight to legal_research, skips the classifier
     "session_id":    "<stable per-pane uuid>",
     "filters":       { "client_id": "internal" },
     "uploaded_text": "<FULL current document text>"   // re-sent in full, every turn
   }
   ```
   Header `X-User-ID: word-addin` ([api.ts:35](../clients/word/src/api.ts)).

So the document is **re-uploaded on every single turn**. The backend never caches it.

---

## 3. The backend turn, step by step (Chat path)

`submit_query` ([api/routes/query.py:82-134](../api/routes/query.py)) builds `initial_state` with
**`chat_history: []`** and `uploaded_docs: [{"text": uploaded_text}]`, then
`graph.invoke(initial_state, config={"configurable": {"thread_id": session_id}})`.

> **How does prior history survive if the input sets `chat_history: []`?**
> The Redis checkpointer loads the prior checkpoint for `thread_id`, and LangGraph applies the
> channel reducer. `_history_reducer` ([graph/state.py:10-29](../graph/state.py)) computes
> `(old + new)[-2N:]`. With `new == []`, the result is just `old` — the stored history is
> preserved. The empty input is a no-op merge, **not** a reset.

Graph path for `task_type="research"` ([graph/graph.py](../graph/graph.py)):

```
intake → intent_router → skill_dispatcher → legal_research
       → rag_retriever (SKIP) → llm_caller (SKIP) → risk_assessor
       → output_formatter → history_appender → memory_writer → END
```

- **`intake`** ([intake.py:17-37](../graph/nodes/intake.py)) — resolves `client_id` (defaults to
  `"internal"`), sets `filters`, sets `retrieval_query = request`.
- **`intent_router`** ([intent_router.py:42-46](../graph/nodes/intent_router.py)) — sees a valid
  `task_type="research"` already set → **keeps it, no classifier LLM call**.
- **`legal_research`** ([skills/legal_research.py:456-498](../skills/legal_research.py)) — sees
  `uploaded_text` is non-empty → takes the **`_run_doc_chat`** path (direct `ChatOllama`, **no
  ReAct tools, no RAG** — tools added multi-minute latency for no gain when the doc is in context).
  It sets `state["llm_response"]` and `state["proposed_edits"]`, leaves `state["messages"]` empty.
- **`rag_retriever`** ([rag_retriever.py:17-19](../graph/nodes/rag_retriever.py)) — **skips**:
  `llm_response` is already set.
- **`llm_caller`** ([llm_caller.py:34-36](../graph/nodes/llm_caller.py)) — **skips**: `llm_response`
  set *and* `state["messages"]` empty. **The doc-chat LLM call is the only generation in this path.**
- **`output_formatter`** ([output_formatter.py:13-29](../graph/nodes/output_formatter.py)) — packs
  `report = { response, proposed_edits, ... }`.
- **`history_appender`** ([history_appender.py:24-40](../graph/nodes/history_appender.py)) — returns
  `{"chat_history": [{user: request}, {assistant: trim(llm_response, 300)}]}`; the reducer appends
  and caps.
- `refresh_ttl(session_id)` resets the 24 h Redis TTL ([checkpointer.py:39-56](../graph/checkpointer.py)).

The response the Chat tab renders comes from `res.data.report.response` (prose, JSON edit-blocks
stripped) and `res.data.report.proposed_edits` (rendered as edit cards) — `ChatTab.tsx:65-93`.

---

## 4. Exactly what the LLM sees, each Chat turn

`_run_doc_chat` ([skills/legal_research.py:342-411](../skills/legal_research.py)) assembles:

```
[
  { role: "system",  content: CHAT_SYSTEM_PROMPT },        // embedded-in-Word rules + edit-JSON format
  ...chat_history,                                          // ≤ 10 msgs (5 turns); each ASSISTANT msg ≤ 300 chars
  { role: "user", content:
      "User request: <question>\n\n"
      "--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
      "<FULL current document text>\n"                      // re-sent in full, every turn
      "--- END ATTACHED DOCUMENT ---"
  }
]
```

Model: `ChatOllama`, `temperature=0.0`, `reasoning=False`, **no tools** ([_build_llm, :92-102](../skills/legal_research.py)).

**Concrete implication of the 300-char trim:** after a contract review, the chat's memory of that
review is a 300-char fragment of the review text. The *document* is fully present (it's re-uploaded),
but the *findings* are mostly gone from conversational memory. A follow-up like "expand on the IP
risk you flagged" relies on the model re-deriving it from the doc, not recalling the review.

There is also a **JSON-mode retry** ([:381-409](../skills/legal_research.py)): if the prose *promised*
an edit but emitted no `json` block, a second `ChatOllama(format="json")` call is made with the
prior prose echoed back. That's a **second LLM call** in the turn (it does **not** see `chat_history`
— only the doc, the question, and the prior answer).

---

## 5. The Findings tab path, for contrast (where the MSA *does* attach)

`contract_review` ([skills/contract_review/contract_review.py:183-312](../skills/contract_review/contract_review.py))
does **not** call the LLM itself — it sets `state["messages"]`, then `llm_caller` runs the generation.

- It detects the contract type from the doc heading, loads the matching **playbook bundle**, and
  builds `state["messages"]`:
  ```
  [ {system: playbook bundle}, {system: _OUTPUT_CONSTRAINTS},
    (msa-only:) {system: _MSA_COMPARISON_DIRECTIVE},
    {user: "<request>\n\n--- CONTRACT TEXT ---\n<doc>\n--- END ---" (+ MSA block) (+ attorney notes)} ]
  ```
- **SOW + governing MSA on file** → `get_parent_msa(client_id)` scrolls Qdrant for
  `doc_type="msa"`, appends a `--- GOVERNING MSA (title) ---` block (capped at `_MSA_MAX_CHARS=24000`),
  and adds `_MSA_COMPARISON_DIRECTIVE` as the **last** system message ([:239-302](../skills/contract_review/contract_review.py)).
  Strictly additive: non-SOW / no-MSA / lookup error → standalone review.
- `llm_caller` ([llm_caller.py:42-62](../graph/nodes/llm_caller.py)) then injects `chat_history`
  **between the playbook system message and the rest**, and prepends `Context:\nNo documents
  retrieved.\n\n` to the user message (RAG is skipped for uploaded docs, so context is empty):
  ```
  [ {system: playbook}, ...chat_history, {system: _OUTPUT_CONSTRAINTS}, (msa directive), {user: "Context:\nNo documents retrieved.\n\n<request + contract + MSA>"} ]
  ```

> **Audit flag:** the MSA comparison is a **Findings-tab-only** capability. The Chat tab
> (`legal_research._run_doc_chat`) never calls `get_parent_msa`, so asking *"does this SOW conflict
> with the MSA?"* **in chat** runs with no MSA in context — only whatever 300-char stub of the
> review survived in `chat_history`. If cross-doc Q&A in chat is desired, that's a deliberate
> extension, not current behavior.

---

## 6. How history persists from message to message

```
 Turn N                                          Turn N+1
 ──────                                          ────────
 client POSTs {request, uploaded_text, sid}      client POSTs {request, uploaded_text, sid}
        │                                                │
        ▼                                                ▼
 graph.invoke(initial_state{chat_history:[]},    graph.invoke(initial_state{chat_history:[]},
              thread_id=sid)                                   thread_id=sid)
        │                                                │
        │  RedisSaver loads checkpoint(sid)              │  RedisSaver loads checkpoint(sid)
        │  reducer: stored + [] = stored                 │  reducer: stored + [] = stored
        ▼                                                ▼
   legal_research sees chat_history = [turns 1..N-1]   ... = [turns 1..N] (turn N now included)
        │                                                │
        ▼                                                ▼
   history_appender returns {chat_history:[userN, asstN]}
        │  reducer: (stored + [userN, asstN])[-10:]      (same, capped to last 5 turns)
        ▼
   RedisSaver persists new checkpoint(sid), TTL reset 24h
```

Key mechanics:
- **`thread_id = session_id`** is the join key ([query.py:126](../api/routes/query.py)).
- The reducer is **idempotent on no-op nodes** ([state.py:27-28](../graph/state.py)) — most nodes
  return the full state, which would otherwise double history on every node; the `new == old`
  guard prevents that. Only `history_appender` returns a genuine partial append.
- **If Redis is down**, `build_checkpointer()` returns `None` ([checkpointer.py:34-36](../graph/checkpointer.py))
  → no persistence → **every turn is stateless** (`chat_history` always `[]`). Conversational
  memory is best-effort, not guaranteed.

---

## 7. Notable behaviors, limits, and risks

1. **Document re-sent in full every turn.** Token cost and latency scale with doc size on *every*
   chat turn (and the review). No truncation on the chat path — a very large doc could blow the
   local model's context window. (Contrast: the MSA *is* capped at 24 000 chars on the review path.)

2. **Assistant replies trimmed to 300 chars in history** (`chat_history_trim_chars`,
   [config.py:56](../config.py)). After a contract review, chat's memory of the findings is a stub.
   User questions are stored untrimmed; only assistant turns are trimmed.

3. **Only 5 turns of history** (`chat_history_n_turns=5` → cap 10 messages, [config.py:55](../config.py)).
   Older turns silently fall off the front.

4. **MSA comparison is Findings-tab only** (§5). Chat has no MSA context.

5. **Session = pane lifetime, not document.** Reopening the pane resets chat; switching docs with
   the pane open does not (history may reference a stale doc).

6. **`client_id` is hardcoded `"internal"`** in the Word client ([api.ts:49,75](../clients/word/src/api.ts))
   and defaulted in `intake`. Tenant isolation in Qdrant tools depends on this — fine for the demo,
   a real auth story is deferred (CLAUDE.md "Out of scope").

7. **The chat path bypasses RAG and the ReAct agent entirely** — the doc is the sole source. KB
   research (`_run_kb_research`, ReAct + `search_legal`/`get_document`/`escalate`) only runs when
   **no** document is attached, which never happens from the Word Chat tab (it always sends
   `uploaded_text`).

8. **`llm_caller` chat_history placement on the review path** puts history *between* the playbook
   and `_OUTPUT_CONSTRAINTS` ([llm_caller.py:59-62](../graph/nodes/llm_caller.py)). Works, but it's
   a slightly odd spot — worth noting if review answers ever seem to "drift" toward chat context.

9. **`Context:\nNo documents retrieved.`** is prepended to the review user message even though the
   contract is fully present ([llm_caller.py:47-51](../graph/nodes/llm_caller.py)) — harmless but
   confusing noise in the prompt.

---

## 8. Config knobs that govern memory

All in `config.py` (env-overridable):

| Setting | Default | Effect |
|---------|---------|--------|
| `chat_history_n_turns` | `5` | History cap = `2 × N` messages |
| `chat_history_trim_chars` | `300` | Max chars kept per **assistant** turn in history |
| `checkpoint_ttl_seconds` | `86400` (24 h) | Redis session expiry; refreshed every call |
| `checkpointer_enabled` | `True` | If false (or Redis down) → stateless turns |
| `_MSA_MAX_CHARS` (in `contract_review.py`) | `24000` | Max MSA chars inlined into a SOW review |

---

## 9. Open questions — to "move further"

These are decisions the current code does *not* make for you; flagging them so they're explicit:

- **Should chat see the MSA?** Today only the Findings tab does. If chat Q&A about SOW-vs-MSA is a
  goal, `_run_doc_chat` would need its own (capped) MSA attach — a deliberate, playbook-grounded
  addition mirroring the review path.
- **Is a 300-char assistant stub enough conversational memory?** For "remember what you just told
  me" follow-ups it's thin. Options: raise `chat_history_trim_chars`, store a structured summary
  instead of a prefix, or persist the last full review separately.
- **Re-sending the whole doc every turn** is simple and always-fresh, but costly for large docs and
  unbounded on the chat path. A diff/section-scoped approach would be a larger change.
- **Session is per-pane, not per-document.** If multi-document workflows matter, bind `session_id`
  (or a sub-key) to the document identity.
- **Token visibility:** generations now record token usage via `observability/tracing.py`
  (`ollama_usage`, `traced_invoke`). That's the lever for measuring the cost of the "re-send the
  whole doc" design once you start tuning.
