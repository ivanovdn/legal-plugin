# Context & Memory Audit

> **Purpose:** A ground-truth audit of *what memory the agent has*, *where each store lives*,
> *how context flows from turn to turn*, and *how to inspect each store*. Written to understand
> the current system before extending it. Every claim below is cited to a file:line.
>
> Last audited: **2026-07-15** (`main` after slices 1–3: canonical document UUID, per-attorney
> conversation store, O365 SSO backend seam — plus the chat-memory-grounding merge).
>
> **What changed since the 2026-06-26 audit:** the chat tab is no longer a "300-char stub" of
> memory. Three things landed: (a) the **chat path is grounded** — it attaches the playbook bundle
> and (for SOWs) the governing MSA, via the shared `skills/grounding.py`; (b) two **durable SQLite
> stores** — the review store and the per-attorney conversation store — now persist findings and
> full chat turns across sessions/machines; (c) memory failure is now **loud** (`memory_degraded`).
> The old audit's headline claims ("MSA is Findings-tab only", "chat memory is a 300-char stub",
> "reopening the pane resets chat") are all **superseded** — see the relevant sections.

---

## TL;DR

The system now has **eight distinct context/memory stores** across four backing services
(Redis, SQLite, Qdrant, Langfuse) plus the generated playbook on the filesystem. They are easy
to conflate — the table is the map:

| # | Store | Backing | What it holds | Key / scope | Lifetime | Fed to the LLM? |
|---|-------|---------|---------------|-------------|----------|-----------------|
| 1 | **Open document** (`uploaded_docs`) | — (in request) | Full current doc text | Re-sent fresh **every turn** by the client | not retained backend-side | ✅ inlined in the user message |
| 2 | **`chat_history`** | Redis | Prior (user, assistant) turns | `thread_id = session_id` | cap **10 msgs (5 turns)**, assistant trimmed **300 chars**, TTL 24 h | ✅ **fallback only** now (see #4) |
| 3 | **Checkpointer** | Redis | Full `LegalAgentState` snapshot | `thread_id = session_id`, TTL 24 h (refreshed each call) | 24 h | indirectly — it restores #2 |
| 4 | **Conversation store** | SQLite | **Full** per-turn chat (untrimmed) | `(document_id, attorney_id)` | **permanent** (unbounded); injects last **20 msgs** | ✅ **primary** chat history (prefers this over #2) |
| 5 | **Review store** | SQLite | **Full markdown** contract review | `(document_id, session_id)`, list-shaped | **permanent** | ✅ latest review injected into chat (redlines stripped) |
| 6 | **Audit log** | SQLite | One row per skill invocation | `session_id` / `user_id` | **permanent** | ❌ never fed back |
| 7 | **Qdrant** (`legal_docs`) | Qdrant | RAG corpus + governing MSA + (indirect) playbook source | `client_id` filter | long-lived | ✅ MSA on SOW paths; RAG only when no doc attached |
| 8 | **Langfuse** | Postgres | Traces, token usage | — | observability | ❌ never fed back |

Two more things that are *memory-shaped* but not stores of conversation:
- **Playbook bundle** — generated markdown under `skills/contract_review/playbook/` (from the
  legal-team `.docx`); attached as grounding on both the review and chat paths. Filesystem, not a DB.
- **Qdrant `memory` collection** — provisioned empty (`scripts/create_collections.py:24-27`) for a
  future cross-session attorney-preference store; **no code reads or writes it yet** (scaffold only).

**Most important fact for the Chat tab:** every turn re-sends the **entire current document**
inlined in the user message. On top of that, `_run_doc_chat` assembles a grounded, memory-rich
prompt: the chat system prompt, (conditionally) the **playbook** + **governing MSA**, the
**latest stored review** for this document, and the **durable conversation** for
`(document_id, attorney_id)` — falling back to Redis `chat_history` only when the durable store is
empty. The document is *resupplied* each turn; the conversation and review are *remembered durably*.

**Second most important fact:** the three durable stores all live in **one SQLite file**,
`data/legal.db` (`config.sqlite_path`) — see [§8, "How to inspect each store"](#8-how-to-inspect-each-store).

---

## 1. Identity keys — the four IDs that scope memory

Getting these straight is the whole game. Four different identifiers scope four different things:

| Key | Value today | Set where | Scopes |
|-----|-------------|-----------|--------|
| `session_id` | per-**pane** `crypto.randomUUID()` | `clients/word/src/App.tsx` (useState init) | Redis `chat_history` + checkpointer (`thread_id`) |
| `document_id` | per-**document** UUID from `Office.context.document.settings` (`legalTriageDocId`); preamble-hash fallback | client sends `document_uuid` → `query.py:168` → `intake.py:42` | Review store + conversation store |
| `attorney_id` (= `user_id`) | per-**install** `localStorage` UUID via `X-User-ID` header (or SSO `oid` when `sso_enabled`) | `clients/word/src/attorneyIdentity.ts` → `resolve_user_id` (`api/auth.py`) → `state["user_id"]` | Conversation store (with `document_id`), audit log |
| `client_id` | `"internal"` (default) | `_USER_CLIENT_MAP.get(user_id, "internal")` (`intake.py:13,21`) | Qdrant tenant filter (RAG + MSA) |

Consequences of the design:
- **`session_id` is per-pane** — reopening the pane mints a new one, so Redis `chat_history`
  resets. **But that no longer loses the conversation:** the durable conversation store is keyed by
  `(document_id, attorney_id)`, which survive a pane reopen, a fresh `session_id`, and even a
  different machine (same attorney install). This is why "chat history persistence across pane
  reopen" is a *shipped* capability, not a follow-up.
- **`document_id` is per-document, durable, edit-immune.** The client writes a UUID into the file
  (`Office settings`), so it's stable across edits and reopens. Callers that don't send one
  (Chainlit, unsaved docs) fall back to a hash of the normalized **preamble** (title + parties,
  first ~800 chars) — deliberately *not* the body, which is redlined and would orphan the review
  on every edit (`memory/document_id.py`).
- **`attorney_id` is a partitioning key, not auth.** Today it's a random per-install UUID. When
  `sso_enabled=True`, `resolve_user_id` swaps it for the verified O365 `oid` at the same seam —
  everything downstream is unchanged (see the SSO seam note in CLAUDE.md).

---

## 2. What the Chat tab sends, every turn

`ChatTab.send()` (`clients/word/src/components/ChatTab.tsx`):

1. Reads the **current** doc text via `readBody()` → `body.getReviewedText("current")` (accepts
   tracked changes for *extraction* so filled redlines aren't mis-read as blanks — per CLAUDE.md).
2. `POST /api/query` with:
   ```jsonc
   {
     "request":       "<the user's question>",
     "task_type":     "research",          // routes straight to legal_research, skips the classifier
     "session_id":    "<per-pane uuid>",
     "document_uuid": "<per-document Office-settings uuid>",   // → state.document_id
     "filters":       { "client_id": "internal" },
     "uploaded_text": "<FULL current document text>"           // re-sent in full, every turn
   }
   ```
   Header `X-User-ID: <per-install localStorage uuid>` (`clients/word/src/api.ts`) → `state.user_id`.

The document is **re-uploaded on every turn**; the backend never caches it. The two IDs that unlock
durable memory (`document_uuid`, `X-User-ID`) ride alongside it.

---

## 3. The backend chat turn, step by step (Chat path)

`submit_query` (`api/routes/query.py`) resolves `user_id` via `Depends(resolve_user_id)`, builds
`initial_state` with **`chat_history: []`**, `uploaded_docs: [{"text": uploaded_text}]`, and
`document_id: body.document_uuid`, then invokes the graph with `thread_id = session_id`.

> **How does prior history survive if the input sets `chat_history: []`?** The Redis checkpointer
> loads the prior checkpoint for `thread_id` and applies `_history_reducer` (`graph/state.py:10-29`):
> `(old + [])[-2N:] == old`. The empty input is a no-op merge, not a reset. (This only feeds the
> *fallback* now — the primary history comes from the SQLite conversation store, §4.)

Graph path for `task_type="research"` (`graph/graph.py`):

```
intake → intent_router → skill_dispatcher → legal_research
       → rag_retriever (SKIP) → llm_caller (SKIP) → risk_assessor
       → output_formatter → history_appender → memory_writer → END
```

- **`intake`** (`graph/nodes/intake.py`) — resolves `client_id`; sets `document_id` from the
  client UUID or the preamble-hash fallback (`intake.py:42`); sets `retrieval_query`.
- **`intent_router`** — sees a valid `task_type="research"` → keeps it, no classifier LLM call.
- **`legal_research`** (`_run_doc_chat`, `skills/legal_research.py:508`) — `uploaded_text` non-empty
  → doc-chat path (direct `ChatOllama`, no ReAct/RAG tools). Loads the prior review + durable
  conversation, attaches grounding, assembles the prompt (§4), sets `llm_response` + `proposed_edits`.
- **`rag_retriever` / `llm_caller`** — **skip** (`llm_response` already set).
- **`output_formatter`** — packs `report = { response, proposed_edits, memory_degraded, ... }`.
- **`history_appender`** — returns `{chat_history: [{user}, {assistant: trim(…, 300)}]}` (Redis
  fallback copy); reducer appends + caps.
- **`memory_writer`** (`graph/nodes/memory_writer.py`) — writes the audit log always; on a
  `research` turn with `(document_id, attorney_id, llm_response)` **appends the full turn to the
  conversation store** (best-effort — never fails the turn, §5). On a `contract_review` turn it
  persists the review (loud — §5).
- `refresh_ttl(session_id)` resets the 24 h Redis TTL (`graph/checkpointer.py:39`).

---

## 4. Exactly what the LLM sees, each Chat turn

`_run_doc_chat` (`skills/legal_research.py:508-559`) assembles, in order:

```
[
  { system: CHAT_SYSTEM_PROMPT },                 // embedded-in-Word rules + edit-JSON format
  ( system: playbook bundle ),                    // CONDITIONAL — see grounding gate below
  ( system: governing MSA block ),                // CONDITIONAL — SOWs only, capped at msa_max_chars
  ( system: PRIOR REVIEW block ),                 // latest stored review for this document, redlines stripped
  ...chat_history,                                // durable conversation (≤20 msgs); Redis fallback if empty
  { user: "--- ATTACHED DOCUMENT ---\n<FULL doc>\n--- END ---\n\nUser request: <question>" }
]
```

Model: `ChatOllama`, `temperature=0.0`, `reasoning=False`, **no tools**, `num_ctx=ollama_num_ctx`
(32768) so the grounding doesn't silently overflow a ~4k default (`_build_llm`).

**System prefix is stable-first on purpose** (`legal_research.py:547-551`): the intent was Ollama
prefix-cache reuse across turns. In practice that reuse **does not engage** (see follow-up
"Ollama cross-turn prefix-cache reuse"), so **conditional grounding** is the real latency lever.

- **Conditional grounding** (`chat_conditional_grounding=True`): the heavy playbook + MSA attach
  only when `_needs_grounding(question)` matches an edit / firm-position / MSA-conflict / clause-name
  term (`_GROUNDING_TRIGGER_RE`, `legal_research.py:430-462`). Plain factual Q&A ("who signs?",
  "billing model?") takes the lean path (~10 s vs ~30 s grounded). The gate is biased toward
  attaching — it never under-grounds. Toggle off → always attach.
- **Prior review injection** (`_load_prior_review_block`, `:384`) — `load_latest_review` for this
  `document_id`, with the **Suggested Redlines** section stripped (`_strip_redlines_section`, `:355`)
  so chat recalls findings without re-proposing fills. Directive: "answer recall questions from this
  review; do not re-derive or contradict it."
- **Durable conversation** (`_load_prior_conversation`, `:408`) — `load_recent(document_id,
  attorney_id, conversation_max_messages=20)` from SQLite; falls back to Redis `chat_history` only
  when the durable store returns empty (`:537-539`). This is what makes "Legal name the same we
  filled recently" resolve across a fresh session.
- **Context cap** (`_cap_chat_context`, `:487`) — if the assembled content exceeds
  `chat_context_max_chars` (100 000), it truncates **only the document**, never the grounding.
- **JSON-mode retry** (`_build_json_llm`) — if the prose promised an edit but emitted no `json`
  block, a second `ChatOllama(format="json")` call is made. It does **not** see `chat_history`.

> **Superseded:** the 2026-06-26 audit said chat's memory of a review was a 300-char stub. That is
> no longer true — the **full** latest review is injected from the review store, and the **full**
> conversation (untrimmed) from the conversation store. The 300-char trim still exists but only on
> the Redis `chat_history` *fallback* copy.

---

## 5. The persistence stores in detail (SQLite)

All three live in one file: **`data/legal.db`** (`config.sqlite_path`). Tables are created lazily
on first write (module-level `_*_initialized` guards in `memory_writer.py`).

- **`review_store`** (`memory/review_store.py`) — `save_review` appends one row per
  `(document_id, session_id)` holding the **full markdown** review + `contract_type`. **List-shaped:**
  a re-review appends a new row rather than overwriting, so `load_history` can return every past
  review (newest first) and `load_latest_review` returns the most recent. **Writes are LOUD:**
  `save_review` lets exceptions propagate; `memory_writer` catches and surfaces `review_persist_error`
  in the report (an amber signal on the Findings tab) — a lost review must never look like a save.
- **`conversation_store`** (`memory/conversation_store.py`) — `append_turn` inserts one `user` row
  then one `assistant` row per turn, keyed `(document_id, attorney_id)`, indexed on
  `(document_id, attorney_id, id)`. `load_recent` returns the last `max_messages` in chronological
  order. **Retains everything** (only the injected *window* is capped, not the store). **Writes are
  best-effort:** `memory_writer` wraps init + append in one try/except, logs on failure, **never
  raises** — a lost chat turn is a convenience loss, not a legal record (contrast the loud review).
- **`audit_log`** (`memory/audit.py`) — one row per skill invocation: `session_id`, `user_id`,
  `skill_name`, `task_type`, `request_summary` (≤200 chars), `risk_level`, `review_status`,
  `duration_ms`. Written on every turn. Never fed back to the LLM — it's the compliance trail.

> **Test hazard (from CLAUDE.md):** a test that calls the real `memory_writer` with
> `task_type=="research"` MUST monkeypatch `init_conversation_db` + `append_turn`, or it does a LIVE
> write to `data/legal.db` and pollutes the dev DB.

---

## 6. The Findings tab path, for contrast (and where grounding is shared)

`contract_review` (`skills/contract_review/contract_review.py`) does not call the LLM itself — it
sets `state["messages"]`, then `llm_caller` runs the generation.

- It uses the **shared `skills/grounding.py`** — `detect_contract_type` → `load_playbook_bundle` →
  (SOW only) `attach_parent_msa` — the *same* module the chat path uses. This shared module is what
  keeps the two surfaces from drifting apart (the old asymmetry the 2026-06-26 audit flagged).
- **SOW + governing MSA on file** → appends a `--- GOVERNING MSA (title) ---` block (capped at
  `config.msa_max_chars`, was the inline `_MSA_MAX_CHARS`) and adds `_MSA_COMPARISON_DIRECTIVE` as
  the last system message. Strictly additive: non-SOW / no-MSA / lookup error → standalone review.
- `memory_writer` then **persists the full review** to the review store, keyed by `document_id`.

> **Superseded:** the MSA comparison is **no longer Findings-tab-only.** The chat path attaches the
> governing MSA for SOWs too (`_build_chat_grounding`, `legal_research.py:465-484`), so asking "does
> this SOW conflict with the MSA?" in chat now runs with the MSA in context (when grounding attaches).

---

## 7. Degraded memory is loud

Memory failure never fails silently and never (by itself) fails the turn:

- **Startup:** checkpointer enabled but Redis unavailable → `_get_graph` logs an error and every
  turn reports `memory_degraded=True` (`api/routes/query.py:57-71`).
- **Mid-invoke Redis outage:** `_is_redis_failure` detects a Redis error in the exception chain and
  retries the turn on a checkpointer-less `_get_stateless_graph`, setting `memory_degraded=True`
  (`query.py:35-47, 180-190`). The turn still answers — grounding/review/conversation load from
  SQLite and Qdrant, not Redis.
- **In-graph store read failure:** `_load_prior_review_block` / `_load_prior_conversation` catch
  read errors, set `state["memory_degraded"]=True`, and return empty — the chat turn proceeds
  ungrounded-on-that-store rather than crashing.
- The Word add-in renders `memory_degraded` as an **amber banner**; a failed review write surfaces
  `review_persist_error` on the Findings tab.
- Note: `docker compose stop redis` also stops Langfuse ingestion (shared Redis) — trace-flush
  errors during a Redis-down test are expected and non-fatal.

---

## 8. How to inspect each store

**SQLite (`data/legal.db`) — the three durable stores:**
```bash
sqlite3 data/legal.db '.tables'                        # audit_log, conversation_store, review_store
sqlite3 data/legal.db '.schema review_store'

# Latest reviews (newest first)
sqlite3 -header -column data/legal.db \
  "SELECT id, timestamp, document_id, contract_type, length(review_markdown) AS md_len
   FROM review_store ORDER BY id DESC LIMIT 10;"

# A document's chat conversation (chronological)
sqlite3 -header -column data/legal.db \
  "SELECT timestamp, attorney_id, role, substr(content,1,80) AS preview
   FROM conversation_store WHERE document_id='<doc-id>' ORDER BY id;"

# Audit trail
sqlite3 -header -column data/legal.db \
  "SELECT timestamp, user_id, skill_name, task_type, risk_level FROM audit_log ORDER BY id DESC LIMIT 20;"
```

**Redis — `chat_history` + checkpointer (per session):**
```bash
redis-cli -u 'redis://:myredissecret@localhost:6379' --scan --pattern 'checkpoint:*' | head
redis-cli -u 'redis://:myredissecret@localhost:6379' TTL 'checkpoint:<session_id>:...'
```

**Qdrant — RAG corpus + MSA (and the empty `memory` collection):**
- Dashboard: `http://localhost:6333/dashboard` → collections `legal_docs` (populated), `memory` (empty).
- Filter by `client_id` when browsing points — tenant isolation depends on it.

**Langfuse — traces + token usage (never fed back, but the cost lens):**
- UI: `http://localhost:3000` — filter by `user_id` / `session_id` / `task_type` tag (set in `intake.py:23`).

**Playbook grounding — generated markdown (not a DB):**
```bash
ls skills/contract_review/playbook/          # per-type bundles; regenerate via scripts/build_playbook.py
```

---

## 9. Config knobs that govern memory

All in `config.py` (env-overridable):

| Setting | Default | Effect |
|---------|---------|--------|
| `checkpointer_enabled` | `True` | If false (or Redis down) → stateless turns, `memory_degraded=True` |
| `checkpoint_ttl_seconds` | `86400` (24 h) | Redis session expiry; refreshed every call |
| `chat_history_n_turns` | `5` | Redis `chat_history` cap = `2 × N` messages (fallback path) |
| `chat_history_trim_chars` | `300` | Max chars kept per **assistant** turn in the Redis fallback |
| `conversation_store_enabled` | `True` | Durable per-`(document, attorney)` chat store; False = Redis-only |
| `conversation_max_messages` | `20` | Messages injected from the durable store (~10 turns); store retains all (`<=0` → `[]`) |
| `chat_conditional_grounding` | `True` | Gate playbook/MSA on `_needs_grounding`; False = always attach |
| `chat_context_max_chars` | `100000` | Assembled chat-context budget; truncates the **document** only |
| `ollama_num_ctx` | `32768` | Context window for grounded LLM calls; unset → ~4k default silently truncates grounding |
| `msa_max_chars` | `24000` | Max MSA chars inlined (review + chat), shared via `config` |
| `sqlite_path` | `data/legal.db` | The one file holding all three durable stores |
| `sso_enabled` | `False` | On → `attorney_id` becomes the verified O365 `oid` (dormant today) |

---

## 10. What's implemented vs. what remains

**Implemented (was "open questions" in the prior audit):**
- ✅ **Chat sees the MSA** — `_build_chat_grounding` attaches it for SOWs.
- ✅ **Durable conversational memory** — full review + full conversation, not a 300-char stub.
- ✅ **Session/document decoupling** — durable stores keyed by `document_id`, survive pane reopen.
- ✅ **Loud degradation** — `memory_degraded` + `review_persist_error`.

**Still to implement (tracked in [wiki.md](wiki.md) Follow-ups / Roadmap):**
- **Stale-recall reconciliation** — chat recalls the *last* review verbatim; a field filled *after*
  the review is still reported as a placeholder. Reconcile the recalled review against the current
  `uploaded_text`, or nudge a re-review when the doc changed since the review timestamp.
- **Conversation retention / pruning** — `conversation_store` grows unbounded; only the injected
  window is capped. Revisit if `data/legal.db` grows.
- **FTS cross-matter precedent recall** — full-text search across all stored reviews for a
  `client_id` ("we flagged this same IP clause Red in three prior SOWs"). The list-shaped schema
  is the natural home.
- **Structured-JSON review output / selective finding injection** — inject only the findings
  relevant to a chat question (retrieval-narrowing) instead of the full markdown; unlocks Phase-2
  Word playbook citations.
- **Clause segmentation / retrieval-narrowing on chat** — fetch only the doc clauses relevant to
  the question instead of re-sending the whole document every turn (the main cost lever).
- **Long-term attorney-preference memory** — the empty Qdrant `memory` collection is the scaffold;
  no code reads/writes it yet.
- **SSO cutover migration** — when `sso_enabled` flips on, `attorney_id` changes from the
  localStorage UUID to the O365 `oid`, which re-keys the conversation partition; a one-time
  migration re-keys existing rows.
- **Ollama prefix-cache reuse** — the stable-first ordering was meant to enable it; it doesn't
  engage, so conditional grounding is the workaround. If reuse can be made to engage, always-ground.
