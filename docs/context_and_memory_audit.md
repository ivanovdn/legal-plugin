# Context & Memory Audit

> **Purpose:** a ground-truth map of *what memory the agent has*, *where each store lives*,
> *how context reaches the model each turn*, and *how to inspect each store*. Read it before
> extending memory or the context budget, or adding anything that keeps contract text.
>
> **Last audited: 2026-10-28**, against `main` at `b198467`; production facts measured on the VM
> the same day. Citations are **file + symbol**, not line numbers: the previous audit
> (2026-07-15) cited lines, and most of them had moved.
>
> **What changed since 2026-07-15:**
> - **Compaction:** history is condensed into summary segments, manually or automatically.
> - **Preferences:** each attorney has `USER.md` preferences.
> - **Reconciliation:** the recalled review is reconciled against the current document.
> - **Sanitising:** replayed history has edit and preference blocks stripped.
> - **Budget:** the chat context budget grew, is now measured (`context_breakdown`), and is announced when it cuts (`context_truncated`).
> - **Postgres:** an outage now degrades the turn instead of killing it.
> - **Tracing:** moved to OpenTelemetry (Langfuse locally, Phoenix on the VM).
> - **Records:** six tables arrived. Four of them keep contract text.
> - **Code layout:** `skills/legal_research.py` is now the package `skills/legal_research/`.

---

## TL;DR

The stores fall into three kinds. Some feed a prompt. Some are records that no model ever reads. Some live in the Word pane.

**Fed back into a prompt**

| Store | Backing | What it holds | Key / scope | Lifetime | Into which prompt |
|---|---|---|---|---|---|
| **Open document** | the request (`uploaded_text`) | full current text, tracked changes accepted | re-sent by the pane every turn | the request (copies are kept; see Records) | chat and review: the user message |
| **Preferences** (`USER.md`) | file `data/attorneys/<attorney_id>/USER.md` | the attorney's standing preferences, written by the attorney | `attorney_id` | permanent; re-read every turn | chat and review: an early system block, below the playbook's authority |
| **Playbook bundle** | generated markdown, `skills/contract_review/playbook/` | the per-type legal playbook | detected contract type | ships with the code | review: always. Chat: when `_needs_grounding` matches |
| **Governing MSA** | Qdrant `legal_docs` (`doc_type="msa"`) | MSA text, capped at `msa_max_chars` | `client_id` | long-lived | SOW review; SOW chat when grounded. **Never on the VM: its collection is empty** |
| **`review_store`** | Postgres `app-db` | full markdown of every review | `document_id`, shared across attorneys | permanent | chat: the latest review, redlines stripped, then reconciled |
| **`conversation_store`** | Postgres | every doc-chat message, raw | `(document_id, attorney_id)` | permanent | chat: the messages after the last condensed one. Also compaction's input |
| **`conversation_summary`** | Postgres | condensed segments of extractive quotes, each over a fixed row-id range | `(document_id, attorney_id)` | permanent, append-only | chat: the newest segments as system messages, below the grounding |
| **Redis `chat_history`** | Redis, inside the checkpoint | last 10 messages; assistant replies cut to 300 chars | `thread_id = session_id` | 24 h, refreshed each turn | chat: **fallback only**. The no-document research path: its only history. Never a review |
| **Checkpointer** | Redis | the whole `LegalAgentState`, document text included | `session_id` | 24 h; some keys never expire (§8) | indirectly: restores `chat_history`, and the Chainlit resume |
| **Qdrant `legal_docs` / `case_history`** | Qdrant | RAG corpus / past clauses | `client_id` filter | long-lived | no-document research / contract generation. **Empty on the VM** |

**Records: never fed back to a model**

| Store | Backing | What it holds | Key | Contract text? | Lifetime |
|---|---|---|---|---|---|
| `audit_log` | Postgres | one row per completed graph run | `session_id`, `user_id`, `turn_id` | the question's first 200 chars | permanent |
| `turn_log` | Postgres | one row per `/api/query`, resume and `/api/compact`. Holds the outcome, degradations, model timings and tokens, effective settings and the context breakdown | `turn_id` | request up to 2,000 chars; the document only as hash and length | permanent |
| `feedback` | Postgres | an attorney's flag and comment, plus a replay snapshot | `turn_id` | yes: the snapshot holds the document, answer and request | permanent |
| `interaction_event` | Postgres | pane actions (Apply / Discard / failed), per-turn counters, pane lifecycle | `turn_id`, `document_id`, `attorney_id` | yes: `applied` holds the full text an Apply put in | permanent; `applied` is cleared on deletion |
| `document_version` | Postgres | each distinct text of a document, stored once | `(document_id, sha256)` | yes, full text up to 1M chars | until `scripts.delete_document_data` |
| `document_snapshot` | Postgres | pane captures (open / after an Apply / idle / Finalize), with tracked changes and their authors | `document_id`, `session_id` | yes: tracked-change text | until deletion |
| Trace spans | Langfuse v3 (local) / Phoenix (VM) | every span, including prompt and answer text | `trace_id` | yes: document, playbook, MSA | the volume's lifetime |
| App log | the `start.sh` terminal (local) / journald (VM) | INFO lines tagged `[turn=<8>]`, including each chat turn's `[compaction]` decision | `turn_id` prefix | the full audit entry when its DB write fails | journald retention |

**Held in the Word pane**

| What | Where | Key | Lifetime |
|---|---|---|---|
| Attorney id and display name | `localStorage` keys `legalTriageAttorneyId` / `legalTriageAttorneyName` | per install | until storage is cleared |
| Document id | Office document setting `legalTriageDocId` | per file | travels with the file; lost if the file is never saved |
| Visible chat and findings | React state in `App.tsx` | per pane | **gone on reopen.** Nothing reloads them (§1) |

**Three facts that matter most**

1. **The document is re-sent in full every turn and is never read back from storage, but it is kept.**
   - Each distinct text is stored in `document_version`.
   - The checkpoint holds it for 24 h.
   - The trace spans carry it.

   `scripts.delete_document_data` removes the copies. It does not reach reviews, conversations, feedback or traces (§5).
2. **`_run_doc_chat` assembles each chat prompt.** The order is:
   - chat rules
   - preferences
   - playbook
   - MSA
   - prior review
   - condensed summaries
   - recent messages
   - the document and the question

   The budget cuts only the document, from the end (§4).
3. **Everything relational is one Postgres database**, the `app-db` service (`memory/db.py`; §5 lists its tables). Redis holds only the 24-hour session checkpoint.

**Production (the VM) differs from a local run in three ways that change what the model sees:**
- **No MSA, no RAG.** Spark's Qdrant `legal_docs` collection is empty (0 points, checked 2026-10-28). No MSA is ever attached, and no-document research retrieves nothing.
- **Phoenix, not Langfuse.** Traces go to Phoenix at `http://172.20.1.10:6007`.
- **Redis keys that never expire** (§8).

**When reading older pilot turns:** until 2026-10-28 15:15 UTC the VM ran with `CHAT_CONTEXT_MAX_CHARS=68000`, the compaction-test value from [testing-compaction.md](testing-compaction.md), instead of the 150000 default. Every turn whose settings were recorded (from 2026-10-21) shows it in `turn_log.settings`, and none truncated. The override is gone and the VM now runs the default.

---

## 1. Identity keys: what scopes memory

| Key | Value | Minted / resolved in | Scopes |
|---|---|---|---|
| `session_id` | per-pane `crypto.randomUUID()`, shared by both tabs | `App.tsx` `useState` initializer. The backend mints a uuid4 if it is absent (`submit_query`) | the Redis checkpoint and `chat_history` (`thread_id`). Also recorded on most tables, but never used to look up memory |
| `document_id` | a per-file UUID in the Office setting `legalTriageDocId`. Without one, a hash of the normalised preamble | Pane: `docIdentity.ts::resolveDocumentId` mints it on the first query, flag or compaction, and saves it into the file. `readDocumentId` reads it without minting (pane events, captures). Backend: `intake` takes `document_uuid`, else `memory/document_id.py::resolve_document_id(text)` | review store, conversation store and summaries, the document copies, feedback, events, `turn_log`, `/api/compact` |
| `attorney_id` (`user_id`) | a per-install `localStorage` UUID sent as `X-User-ID`; the O365 `oid` when `sso_enabled` | `attorneyIdentity.ts::resolveAttorneyId` → `api/auth.py::resolve_user_id` → `state["user_id"]` | conversations and summaries (with `document_id`), the `USER.md` path, `/api/preferences`, feedback, events, `turn_log`, copies, `audit_log.user_id` |
| `client_id` | `"internal"` | `intake` overwrites what the client sends with `_USER_CLIENT_MAP.get(user_id, "internal")`; the map is empty | the Qdrant tenant filter |
| `turn_id` | uuid4 per request | `submit_query` / `resume_query` / `/api/compact`, returned to the pane | `feedback`, `interaction_event`, `turn_log` (unique), `audit_log`, the `[turn=…]` log tag |
| `trace_id` | the 32-hex OTel trace id | `observability/spans.py::current_trace_id`, returned to the pane | the trace backend; stored on `feedback` and `turn_log` |
| `doc_sha256` | SHA-256 of the document text | `memory/fingerprint.py::fingerprint` | joins `turn_log` to `document_version` |

`user_name` (`X-User-Name`) is display-only and never a key.

What follows from these keys:
- **Reopening the pane starts a new `session_id`, so the Redis history starts over.**
  - **The model's memory survives.** The review, the conversation and its summaries are keyed by `(document_id, attorney_id)`. Those survive a reopen, a new session and another machine.
  - **The attorney's view does not survive.** The visible chat lives only in React state, and no route returns it, so a reopened pane shows an empty chat while the model still remembers the conversation.
  - A `localStorage` restore was built and reverted on 2026-07-14 ([spec](superpowers/specs/2026-07-13-chat-persistence-pane-reopen-design.md)). The server-side design that replaced it restored only the model's memory.
- **An unsaved document starts with no memory next time.** Its id is lost with the file, so the next session starts without the old review or conversation ([roadmap](roadmap.md): *unsaved document silently resets all memory*). The pane warns about this (`isDocumentUnsaved`).
- **`document_id` survives edits.** The UUID lives in the file. The hash fallback reads only the preamble (the first ~800 chars, cut at the first numbered clause), because the body gets redlined.
- **`attorney_id` is a partitioning key, not authentication.** SSO swaps it at `resolve_user_id` and nothing downstream changes, except that existing conversations stay keyed by the old UUID (§10).

---

## 2. What the Chat tab sends

`ChatTab.send()` → `api.ts::chatQuery`:

1. It reads the document with `word.ts::readBody` → `body.getReviewedText("current")`. Tracked changes come back accepted, so a blank filled by a redline isn't read as still blank.
2. It sends `POST /api/query`:
   ```jsonc
   {
     "request":       "<the question>",
     "task_type":     "research",                           // skips the intent classifier
     "session_id":    "<per-pane uuid>",
     "document_uuid": "<legalTriageDocId, may be empty>",   // → state.document_id
     "filters":       { "client_id": "internal" },          // overwritten by intake
     "uploaded_text": "<the full current document>"
   }
   ```
   Headers come from `requestHeaders.ts::requestHeaders`: `X-User-ID`, `X-User-Name`, `X-Office-Platform`, `X-Office-Version` and `X-Pane-Build`. There is no `Authorization` header yet; the SSO client wiring is deferred. `api/client_info.py::resolve_client_info` writes the client facts into `turn_log`, `feedback` and `interaction_event`.

The response carries `turn_id`, `trace_id`, `memory_degraded` and a `report`. The report holds:
- the answer
- `proposed_edits` and `proposed_preferences`
- `context_truncated` and `context_breakdown`
- token counts

The pane then posts `/api/events` (`edits_proposed`, `preferences_suggested`, `turn_waited`, `client_error`). Separately from turns, it posts document captures to `/api/document-snapshot` (§5).

---

## 3. The backend chat turn

`api/routes/query.py::submit_query` first resolves identity with `resolve_user_id`, `resolve_user_name` and `resolve_client_info`. It then mints `turn_id` and opens the turn (`api/turn_record.py::start_turn`).

It builds `initial_state` with:
- `chat_history: []`
- `uploaded_docs: [{"text": uploaded_text}]`
- `document_id: document_uuid`
- an explicit reset of every per-turn field (`context_truncated`, `context_breakdown`, `proposed_edits`, `memory_degraded`, and so on). Without the resets, the checkpointer would carry the previous turn's values forward.

It then invokes the graph with `thread_id = session_id`.

> `chat_history: []` does not wipe the history. The checkpointer restores the previous state, and `graph/state.py::_history_reducer` merges `(old + [])[-2N:]`, which is `old`.

Graph path for `task_type="research"` (`graph/graph.py::build_graph`):

```
intake → intent_router → skill_dispatcher → legal_research
       → rag_retriever (skips) → llm_caller (skips) → risk_assessor
       → output_formatter → history_appender → memory_writer → END
```

| Node | Its part in memory and context |
|---|---|
| `intake` | sets `client_id`, `document_id` (the UUID or the preamble hash) and `retrieval_query` |
| `intent_router` | keeps a valid `research`; no model call |
| `legal_research` | **every memory read happens here**, along with the model call: review store, summaries, conversation store, `USER.md`, playbook, MSA (Qdrant). Writes state only |
| `rag_retriever`, `llm_caller` | skip, because `llm_response` is already set |
| `risk_assessor` | always rates doc-chat `low`, so it never reaches `human_review` |
| `output_formatter` | builds `report` from state |
| `history_appender` | appends `[user, assistant ≤300 chars]` to Redis `chat_history`. **It runs for every task type**, so review turns land there too. That is how the Redis fallback can replay a review into chat ([roadmap](roadmap.md): *`chat_history` bleed — residual scope*) |
| `memory_writer` | writes `audit_log` (degrades on failure). On research it appends the raw turn to `conversation_store` (best-effort; needs `document_id` and `attorney_id`). On contract_review it calls `save_review` (loud) |

After the graph:
1. `refresh_ttl(session_id)` runs, on the success path only.
2. `finish_turn` writes `turn_log` and the `document_version` copy, after the response has been sent.

---

## 4. What the model sees on a chat turn

`skills/legal_research/legal_research.py::_run_doc_chat` builds the messages in this order:

| # | Role | Content | When | Cap |
|---|---|---|---|---|
| 1 | system | `CHAT_SYSTEM_PROMPT` (`prompts.py`): answer from the document, the edit-JSON format, SCOPE, SOURCES, preference blocks | always | none |
| 2 | system | attorney preferences: `_PREFERENCES_DIRECTIVE` + `USER.md` (`skills/grounding.py::preferences_block_for_state`) | the file is non-empty | `preferences_max_chars` |
| 3 | system | the playbook bundle for the detected type | grounding attaches (below) | none |
| 4 | system | `--- GOVERNING MSA (title) ---` | grounding attaches, the type is SOW, and an MSA is on file | `msa_max_chars`, marked when truncated |
| 5 | system | `--- PRIOR REVIEW ---` + "answer recall questions from this review" | a review exists for the document | none |
| 6 | system | condensed summaries, oldest first (`compaction.py::render_segment`) | segments exist | `compaction_max_injected_segments` |
| 7 | user / assistant | stored messages after the last condensed row | always (may be empty) | `conversation_max_messages` |
| 7′ | user / assistant | Redis `chat_history` | **only when 6 and 7 are both empty** | 10 messages; assistant replies cut to 300 chars |
| 8 | user | `--- ATTACHED DOCUMENT (the source of truth — answer from this) ---` + the document + `User request: …` | always | the only part the budget cuts |

- **Grounding gate.** Rows 3–4 attach when `context.py::_needs_grounding(question)` matches an edit, firm-position, MSA-conflict or clause-name term. With `chat_conditional_grounding=False` they always attach. The gate leans toward attaching. Plain questions ("who signs?") stay lean, and that is the main latency lever.
- **Prior review** (`context.py::_load_prior_review_block`) takes `load_latest_review(document_id)` through three steps (functions in `review_recall.py`):
  1. `_strip_redlines_section` removes the suggested redlines, so chat doesn't re-propose the review's fills.
  2. `_reconcile_review_with_doc` drops placeholder findings that the document shows were filled since the review.
  3. `_reconcile_gate_verdict` downgrades the No-Signature verdict to `PENDING RE-REVIEW`, but only when no Red or Missing-Context row survives. It never asserts a pass.

  If reconciliation fails, the review is injected unchanged.
- **History** (`context.py::_load_prior_conversation`) is the summaries plus the verbatim rows with `id > latest_to_id`.
  - The floor spans *all* segments, so a segment that has aged out of the prompt still keeps its rows out.
  - `edit_parsing.py::_sanitize_history` then strips ```` ```json ```` and ```` ```preference ```` blocks from assistant turns, whichever source won. Replayed history acts as a few-shot example, and a replayed block outranks the prompt's own rules.
- **Budget** (`context.py::_cap_chat_context`). When all message text exceeds `chat_context_max_chars`, the document is cut **from the end** and the cut is marked.
  - Grounding and history are never cut, so history growth is paid for in document text.
  - The cut returns `{doc_chars, kept_chars, kept_pct}` → `report.context_truncated` → a red notice in the pane.
  - `build_context_breakdown` measures every part → `report.context_breakdown` → the pane's gauge (`ContextMeter`).
  - The backend computes `can_compact` and `auto_compact`. They decide whether the Condense button shows and whether the pane fires `POST /api/compact` automatically.
  - Each turn logs `[compaction] auto=… can=… pct=… truncated=…`.
- **Model call** (`_build_llm`): `ChatOllama(temperature=0, reasoning=False, num_ctx=ollama_num_ctx, num_predict=ollama_num_predict_chat)`, with no tools. `num_ctx` must equal the server's window, or Ollama reloads the model.
- **Edit retry** (`_build_json_llm`) fires when the answer promised an edit but carried no edit block.
  - It is one `format="json"` call with the request, the full (uncapped) document and the first answer. It gets no history, preferences or grounding.
  - If it fails, the first answer is kept and `edit_retry_failed` is recorded.
- **Preference retry** fires when the attorney asked for something to be remembered (or the model said it would remember) and no preference block came back.
  - It is one plain call with the request and the first answer.
  - **Not wrapped:** if it fails, the error reaches `legal_research`'s catch-all, which replaces a good answer with `Error: Legal research failed` (§10).
- **No document attached** (`_run_kb_research`): the ReAct agent runs with `search_legal`, `get_document` and `escalate`.
  - Its only history is Redis `chat_history`.
  - It gets no preferences, playbook, review or stored conversation.
  - An uncited answer can trigger `human_review`.
- **Cached prefix.** A code comment calls the system messages a "stable … cached prefix". Ollama's cross-turn prefix reuse doesn't engage here, and the prefix isn't stable anyway. Rows 3–4 come and go with the question, and row 5 is reconciled again after every edit.

---

## 5. The stores in detail (Postgres `app-db`)

All of these tables live in one database: the `app-db` compose service.
- **Address:** `config.database_url`, default `postgresql://legal:legal@localhost:5434/legal`. The host port is bound to `127.0.0.1` only.
- **Pool:** `memory/db.py::get_pool` is autocommit with `max_size=10` and `timeout=db_pool_timeout` (3 s). psycopg_pool's own default of 30 s made an outage look like a hang. `connect_timeout` is at least 2 s.
- **Startup:** `init_db()` creates every table at `api/main.py` lifespan startup and is fatal there on purpose; compose waits for `app-db` to be healthy.
- **Schema changes:** additive only. `_STATEMENTS` is an ordered, idempotent list of `CREATE … IF NOT EXISTS` and `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. There is no migration tool.
- **Timestamps:** ISO-8601 UTC text.

| Table | One row | Key | Written by | On failure | Read back into a prompt? |
|---|---|---|---|---|---|
| `review_store` | one review; every `save_review` call appends | `document_id` | `memory_writer` → `review_store.save_review` | **loud**: `review_persist_error`, a red line on the Findings tab | chat (latest only) |
| `conversation_store` | one raw message; a turn is a `user` + `assistant` pair | `(document_id, attorney_id)` | `memory_writer` → `conversation_store.append_turn` | quiet: logged | chat; compaction's input (`load_rows_after`) |
| `conversation_summary` | one condensed segment over `from_id..to_id` | `(document_id, attorney_id)` | `POST /api/compact` → `compaction.py::compact_conversation` → `append_segment` | loud: `compaction_failed`, HTTP 500 | chat |
| `audit_log` | one completed graph run | `session_id`, `user_id`, `turn_id` | `memory_writer` → `audit.write_audit_log` | degrades: the full entry is logged, `memory_degraded` is set | no |
| `turn_log` | one `/api/query`, resume or `/api/compact` | `turn_id` (unique) | `api/turn_record.py::finish_turn`, after the response | quiet: the row is logged | no |
| `feedback` | one *Send feedback* | `turn_id` | `POST /api/feedback` → `feedback_store.save_feedback` | loud (500) | no |
| `interaction_event` | one pane event | `turn_id`, `document_id`, `attorney_id` | `POST /api/events` → `feedback_store.record_events` | quiet (always 200) | no |
| `document_version` | one distinct text of a document | `(document_id, sha256)` | `finish_turn` (`source="turn"`) and `save_snapshot` (`source="pane"`) | quiet: ids, hash and size are logged, never the text | no |
| `document_snapshot` | one pane capture | `document_id`, `session_id` | `POST /api/document-snapshot` → `document_store.save_snapshot` | quiet (always 200) | no |

- **Reviews are shared across attorneys and append-only.** They are keyed by document only. `load_latest_review` is the only reader in production.
- **Conversations are per attorney and never deleted.** Compaction keeps the raw rows and only moves the replay floor.
- **Retention: no table is ever pruned.**
  - `scripts.delete_document_data --document <id> | --attorney <id>` (`memory/document_store.py::delete_document_data`) deletes `document_version` and `document_snapshot` rows and clears `interaction_event.applied`.
  - It reports, but does not touch, the rest: reviews, conversations, summaries, feedback and turn rows, plus the traces and nightly dumps that also hold the text.
- **Tests** run against an ephemeral testcontainers Postgres (`tests/conftest.py`). Every table in `STORE_TABLES` is truncated between tests, so a write the test forgot to patch can't reach the dev database. The suite needs Docker, and a new table must be added to `STORE_TABLES` (`tests/test_db.py` checks).

---

## 6. The Findings tab, for contrast

`contract_review` (`skills/contract_review/contract_review.py`) builds the messages, and `llm_caller` makes the call.
- **Grounding** comes from the same `skills/grounding.py` as chat:
  1. `detect_contract_type`
  2. `load_playbook_bundle`
  3. for a SOW only, `attach_parent_msa` (`rag/related_docs.py::get_parent_msa`, capped at `msa_max_chars`)

  If the MSA lookup fails, the review records `msa_lookup_failed` and runs standalone.
- **Message order.** System: playbook → preferences → `_OUTPUT_CONSTRAINTS` → `_MSA_COMPARISON_DIRECTIVE` (last; SOW with an MSA only). User: the request + `--- CONTRACT TEXT ---` [+ `--- GOVERNING MSA ---`] [+ attorney notes].
- **No chat history.** `llm_caller._HISTORY_FREE_TASK_TYPES` drops it, because a review must depend only on its inputs.
- **No input cap.** `llm_caller` only *detects* a prompt over `(ollama_num_ctx − ollama_num_predict_review) × est_chars_per_token` and flags `context_truncated`. It never cuts.
- **Saving.** `memory_writer` saves the review under `document_id`.
- **Defect found in this audit:**
  - When the model call fails, `llm_response` is left as `"Error: LLM call failed — …"`. `memory_writer` saves any non-empty response, so the error string is stored as the document's latest review.
  - The next chat turn then injects it as the PRIOR REVIEW, hiding the last good one.
  - It hasn't happened on the VM yet: 0 of 25 reviews on 2026-10-28 (§10).

---

## 7. Degraded memory is loud

| What failed | Where it's caught | Code | What the attorney sees |
|---|---|---|---|
| Redis absent when the graph is first built | `query.py::_checkpointer_startup_absent`, recorded every turn by `_record_startup_checkpointer_degradation` | `checkpointer_unavailable` | the amber banner, every turn |
| Redis error mid-turn | `submit_query` → `_is_redis_failure` → re-run on `_get_stateless_graph` | `checkpointer_unavailable` | the amber banner. The answer still comes, because the Postgres and Qdrant reads don't need Redis |
| `audit_log` write | `memory_writer`, flagged on the **returned report** (the node runs after `output_formatter`) | `audit_write_failed` | the amber banner |
| Prior-review, summary or conversation read | the `context.py` loaders | `prior_review_load_failed` / `summary_load_failed` / `prior_conversation_load_failed` | the amber banner. The turn answers without that store: a failed summary read replays the rows verbatim; a failed conversation read falls back to Redis |
| Review write | `memory_writer` | `review_persist_failed` | a red line on the Findings tab: the review won't be recalled |
| Document cut to fit the budget | `_cap_chat_context` (chat) / `llm_caller` (review, detection only) | `context_truncated` | a red notice saying how much was read |

**Not announced, on purpose:**
- a lost `conversation_store` append (logged only);
- failures to load preferences, chat grounding or the MSA (`preferences_load_failed`, `chat_grounding_failed`, `msa_lookup_failed`);
- failures of review reconciliation or of the compaction measurement (`review_reconciliation_failed`, `compressible_history_read_failed`).

These are span events with `announced=False`. Writes to `turn_log`, events and captures record no code at all.

**What does fail a request:**
- Redis down on a **resume**, which has no stateless fallback;
- the stateless fallback failing itself;
- store reads inside `/api/compact` (HTTP 500);
- `init_db` at startup.

**Postgres fails fast.** Every store call gives up after `db_pool_timeout`, so an `app-db` outage costs seconds per call instead of a hang. A doc-chat turn makes several calls in a row; the total hasn't been measured.

**Redis drills.** Locally, `docker compose stop redis` also stops Langfuse ingestion, which shares that Redis, so span-export errors during the drill are expected. On the VM, Phoenix doesn't use Redis.

---

## 8. How to inspect each store

These commands are for a local run. On the VM, run them through `$DC` (`docker compose -f docker-compose.yml -f docker-compose.remote.yml`) and add `-T`. The full VM recipes are in [feedback-loop.md](feedback-loop.md) (including a DBeaver tunnel) and [deploy-vm.md](deploy-vm.md).

**Postgres**

```bash
PSQL="docker compose exec app-db psql -U legal -d legal -P pager=off -c"
$PSQL "\dt"

# Latest reviews
$PSQL "SELECT id, timestamp, document_id, contract_type, length(review_markdown) AS md_len
       FROM review_store ORDER BY id DESC LIMIT 10;"

# One document's conversation: the condensed segments, then the raw rows
$PSQL "SELECT id, attorney_id, from_id, to_id, length(content) FROM conversation_summary
       WHERE document_id='<doc-id>' ORDER BY id;"
$PSQL "SELECT id, timestamp, attorney_id, role, substr(content,1,80) AS preview
       FROM conversation_store WHERE document_id='<doc-id>' ORDER BY id;"

# Recent turns
$PSQL "SELECT timestamp, kind, task_type, outcome, degradations, doc_kept_pct, trace_id
       FROM turn_log ORDER BY id DESC LIMIT 20;"
```

**The feedback-loop reader**, a readable view of the record tables:

```bash
uv run python -m scripts.feedback_report                             # feedback
uv run python -m scripts.feedback_report --turns --days 1            # turn_log timeline, with pane events and captures
uv run python -m scripts.feedback_report --activity --days 7         # pane use per attorney per day
uv run python -m scripts.feedback_report --changes --document <id>   # what became of each applied edit
#   filters: --days N | --since ISO | --attorney ID | --document ID | --limit N
uv run python -m scripts.delete_document_data --document <id>        # counts only; add --yes to delete
```

**Redis.** The checkpointer (`langgraph-checkpoint-redis`) writes four key families per session:
- `checkpoint:<sid>:__empty__:<ckpt>`: the state as JSON, with `chat_history` inside
- `checkpoint_write:<sid>:…`
- `checkpoint_latest:<sid>:__empty__`
- `write_keys_zset:<sid>:…`

The saver is built without a TTL. `graph/checkpointer.py::refresh_ttl` sets expiry on only the first two families, and only after a successful turn. So **`checkpoint_latest:*` and `write_keys_zset:*` never expire.**
- On the VM on 2026-10-28 there were 59 and 1,526 of them. One sampled `write_keys_zset` key was 8 KB, which puts that family at roughly 12 MB of Redis's 15 MB.
- 10 `checkpoint:` and 288 `checkpoint_write:` keys also had no expiry. They presumably come from turns that ended before `refresh_ttl` ran; that wasn't traced (§10).

```bash
docker compose exec redis redis-cli -a myredissecret --scan --pattern 'checkpoint:<session_id>:*'
docker compose exec redis redis-cli -a myredissecret TTL '<key>'
```

**Qdrant.** The dashboard is at `http://localhost:6333/dashboard`, and `scripts/create_collections.py` creates `legal_docs`, `case_history` and `memory`. Filter by `client_id` when browsing.
- **`memory`:** empty, and nothing reads or writes it. `search_legal`'s tool description still offers it to the research agent.
- **On the VM:** Spark's Qdrant (`QDRANT_REMOTE_URL`) has only `legal_docs`, and it is empty.

**Traces.** There is one trace per request. Its root span is `query:<task_type>`, `resume:<session_id>` or `compact`, and it carries:
- `user.id` and `session.id`;
- `metadata`: the turn id, user name and builds;
- `app.outcome` and `app.degradations`;
- the question and the answer, as `input.value` / `output.value`.

Degradations are span events. Locally, browse Langfuse at `http://localhost:3000`. On the VM, browse Phoenix at `http://172.20.1.10:6007` (VPN), or use its REST API:

```bash
curl -s "http://172.20.1.10:6007/v1/projects/default/spans?start_time=<iso>Z&limit=1000"
```

**Preferences.** Read the files under `data/attorneys/<attorney_id>/USER.md` (bind-mounted on the VM), or run `curl -H 'X-User-ID: <id>' http://localhost:8000/api/preferences`.

**Logs.** Every app line carries `[turn=<first 8 of turn_id>]`, and `grep -i compaction` shows each chat turn's auto-compaction decision. Locally the log is the `start.sh` terminal. On the VM run `$DC logs backend`; journald also keeps the logs of earlier containers.

**Playbook.** It lives in `skills/contract_review/playbook/`, generated by `scripts/build_playbook.py`.

---

## 9. Config knobs that govern memory and context

All of these live in `config.py` and can be overridden from the environment.
- `get_settings()` is cached, so **any** change needs a backend restart (`bash scripts/start.sh` locally). `USER.md` *content* is the exception: it is re-read every turn.
- Every `turn_log` row records the effective values in `settings`, without credentials (`api/runtime_facts.py`). That is how to see what the VM actually ran.

**Session history (Redis)**

| Setting | Default | Effect |
|---|---|---|
| `checkpointer_enabled` | `True` | off → stateless turns with no `memory_degraded`; the flag means "enabled but Redis unavailable" |
| `checkpoint_ttl_seconds` | `86400` | the expiry `refresh_ttl` sets on checkpoint keys |
| `chat_history_n_turns` | `5` | Redis `chat_history` keeps `2 × N` messages |
| `chat_history_trim_chars` | `300` | assistant messages in Redis are cut to this length |

**Durable conversation**

| Setting | Default | Effect |
|---|---|---|
| `conversation_store_enabled` | `True` | off → no append, no stored history and no summaries; Redis history only |
| `conversation_max_messages` | `20` | how many verbatim stored messages are replayed after the last condensed row |

**Context budget and model**

| Setting | Default | Effect |
|---|---|---|
| `chat_context_max_chars` | `150000` | the chat budget; above it the document is cut from the end. Derived from a ~30 s prefill ceiling |
| `est_chars_per_token` | `4.89` | chars→tokens for the gauge and the review overflow check; measured on legal text |
| `ollama_num_ctx` | `131072` | must equal the Ollama server's window, or the model reloads |
| `ollama_num_predict_chat` / `_review` | `2048` / `8192` | output caps; a review's input headroom is `num_ctx − num_predict_review` |
| `ollama_think_review` | `True` | reviews think; chat never does |

**Grounding**

| Setting | Default | Effect |
|---|---|---|
| `chat_conditional_grounding` | `True` | attach the playbook/MSA to chat only when `_needs_grounding` matches; off → always attach |
| `msa_max_chars` | `24000` | the MSA cap, for review and chat |

**Compaction**

| Setting | Default | Effect |
|---|---|---|
| `compaction_enabled` | `True` | turns on `/api/compact` and summary injection |
| `compaction_auto` | `True` | fire without a click when the backend sets `auto_compact` |
| `compaction_auto_min_chars` | `4000` | compressible history needed before auto-compaction fires |
| `compaction_warn_pct` | `90` | budget share at which Condense becomes possible |
| `compaction_keep_recent_messages` | `2` | the newest messages are never condensed |
| `compaction_min_quotes` / `_max_quotes` | `4` / `24` | bounds on segment size |
| `compaction_max_injected_segments` | `3` | summaries per prompt |

**Preferences, records and infrastructure**

| Setting | Default | Effect |
|---|---|---|
| `preferences_enabled` / `_dir` / `_max_chars` | `True` / `data/attorneys` / `8000` | the `USER.md` store, its location, and the injection cap |
| `feedback_enabled` | `True` | off → `/api/feedback` returns 403 and `/api/events` is discarded |
| `feedback_snapshot_max_chars` | `200000` | per-field cap on a feedback snapshot |
| `document_capture_enabled` | `True` | the `document_version` / `document_snapshot` copies |
| `database_url` / `db_pool_timeout` | `…@localhost:5434/legal` / `3.0` | the `app-db` address; seconds before a store call gives up |
| `redis_url` | `redis://:myredissecret@localhost:6379` | the checkpointer |
| `tracing_enabled` / `otel_exporter_otlp_endpoint` | `True` / local Langfuse | on the VM: `http://phoenix:6006` |
| `sso_enabled` | `False` | on → `attorney_id` becomes the verified O365 `oid` (dormant) |

---

## 10. Open items

**Shipped since the 2026-07-15 audit** (each has a row in [shipped-log.md](shipped-log.md)):
- stale-recall reconciliation, for placeholders and the gate verdict
- history compaction, manual and automatic
- attorney preferences
- the measured context budget and its truncation notice
- `contract_review` kept free of chat history
- history sanitising
- Postgres degradation
- OTel tracing
- `turn_log`
- feedback and pane events
- the document copies (SP4)

**Found in this audit** (added to [roadmap.md](roadmap.md) on 2026-10-28):
- **A failed review is stored as the document's latest review** (§6).
- **The preference retry can replace a good answer with an error** (§4).
- **Some Redis checkpoint keys never expire** (§8).
- **A reopened pane shows an empty chat while the model still remembers it** (§1).

**Still open.** These are listed in [roadmap.md](roadmap.md) under these titles:
- **Recall and freshness**
  - *Chat: re-review nudge on doc divergence*: reviews carry no document fingerprint yet.
  - *Chat gate-verdict reconciliation: broaden trigger to prose gates*.
- **Budget and truncation**
  - *`_cap_chat_context` can cut the document to ZERO and still be over budget*.
  - *One oversized pasted message degrades every later turn until it is condensed*.
  - *Truncation induces FABRICATED "completion" edits at the cut boundary*.
  - *Raising the MSA cap requires solving "the MSA must not absorb history" first*.
  - *MSA truncation — raise the cap / smarter slice*.
- **Compaction**
  - *Read the condensed summary in the pane*.
  - *A table-shaped answer is unquotable*.
  - *Re-summarising older segments*.
  - *Self-calibrating chars/token*.
  - *Close the abbreviation residual in the compaction gate*.
  - *Review-path truncation notice … `doc_chars` misnomer*.
- **Identity and continuity**
  - *Word add-in: unsaved document silently resets all memory*.
  - *`chat_history` bleed — residual scope*.
  - *O365 SSO attorney identity*: at cutover, existing conversations must be re-keyed from the per-install UUID to the `oid`.
- **Retention**
  - *Conversation retention / pruning policy*.
  - *`interaction_event` retention*: it covers `turn_log` and the copies too.
  - *SP4 follow-ups*: deleting the copies at pilot end.
- **Future memory**
  - *Attorney preference memory → self-improving agent harness*, Stage 2+: inferred memory. The empty Qdrant `memory` collection is reserved for it.
  - *Chat: relaxing preferences vs the ceiling*: needs an eval.
  - *Cross-session recall node*.
  - *FTS cross-matter precedent recall*.
  - *Structured-JSON review output / selective finding injection*.
  - *Clause segmentation / retrieval-narrowing on chat*.
  - *Ollama cross-turn prefix-cache reuse doesn't engage*.
