# Server-Side Per-Attorney Conversation Store — Design

**Date:** 2026-07-15
**Status:** Approved (design) — pending spec review
**Slice:** 2 of 3 in the server-side multi-attorney chat-continuity initiative
**Supersedes:** the reverted localStorage chat-persistence-pane-reopen branch
**Builds on:** slice 1 — canonical document UUID (`docs/superpowers/specs/2026-07-14-canonical-document-uuid-design.md`, shipped `cc83cd5`)

## Goal

Make in-Word chat conversations **durable and per-attorney**: a conversation
survives pane reopen, a new `session_id`, and a different machine, and is kept
separate for each attorney working on the same contract — instead of dying with
the ephemeral Redis `session_id` thread.

## Background

Today `chat_history` lives in Redis, keyed by the client-supplied `session_id`
(the LangGraph checkpointer `thread_id`). The Word client mints a fresh
`session_id` on pane reopen, so the Redis thread is orphaned and the
conversation is gone. There is no per-attorney separation at all — the client
sends a hardcoded `X-User-ID: "word-addin"` for every request
(`clients/word/src/api.ts:37`).

Slice 1 gave us a stable, document-embedded `document_id` (a UUID in Office
`document.settings`) that already keys the durable **review** store
(`memory/review_store.py`, SQLite). Slice 2 extends the same pattern to
conversations, adding the second half of the key: the attorney.

## Architecture

A durable SQLite conversation log keyed by `(document_id, attorney_id)`,
**append-per-turn**, colocated in `data/legal.db` alongside the review and audit
stores. The doc-chat path reads recent turns from this store (preferring it over
the session-scoped Redis history) and injects them into the prompt; the graph's
terminal `memory_writer` node appends each chat turn.

The Redis checkpointer stays — it still handles interrupt/resume and the
within-session hot path. The conversation store is additive: it is the
cross-session source of truth for the doc-chat prompt, exactly as the review
store already supersedes Redis for review recall.

```
Word add-in                     FastAPI / graph                     SQLite (data/legal.db)
-----------                     ---------------                     ----------------------
resolveAttorneyId()  --X-User-ID header-->  state["user_id"]
resolveDocumentId()  --document_uuid----->  state["document_id"]

chat turn:
  READ   _run_doc_chat  ->  _load_prior_conversation(state)  --load_recent-->  conversation_store
  WRITE  memory_writer  (task_type=="research")             --append_turn-->  conversation_store
```

## Components

### 1. `memory/conversation_store.py` (new — mirrors `review_store.py`)

Plain `sqlite3`, `db_path`-per-call, same style as `review_store.py` /
`audit.py`.

Schema:

```sql
CREATE TABLE IF NOT EXISTS conversation_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    document_id TEXT NOT NULL,
    attorney_id TEXT NOT NULL,
    role        TEXT NOT NULL,   -- 'user' | 'assistant'
    content     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv ON conversation_store (document_id, attorney_id, id);
```

API:

- `init_conversation_db(db_path: str) -> None`
  Create table + index if absent (idempotent).

- `append_turn(db_path, document_id, attorney_id, user_text, assistant_text) -> None`
  Insert two rows (user then assistant) in one transaction, `timestamp` =
  UTC ISO. **Raises on DB failure** (loud at the module boundary, like
  `save_review`); the caller decides the failure policy.

- `load_recent(db_path, document_id, attorney_id, max_messages) -> list[dict]`
  Return up to `max_messages` most-recent messages for the pair, in
  **chronological** order (oldest first), as `[{"role", "content"}, ...]`.
  Returns `[]` when `document_id` or `attorney_id` is empty, or no rows.
  (Fetch by `id DESC LIMIT max_messages`, then reverse.)

**Isolation is the core property:** different `attorney_id` on the same
`document_id` → disjoint result sets; different `document_id` likewise. This is
what "per-attorney thread" means and is the headline test.

### 2. Client identity — per-install attorney id

`clients/word/src/attorneyIdentity.ts` (new):

```ts
const KEY = "legalTriageAttorneyId";

export function resolveAttorneyId(): string {
  try {
    const existing = localStorage.getItem(KEY);
    if (existing) return existing;
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `atty-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(KEY, id);
    return id;
  } catch {
    return "word-addin"; // fail-safe: never break a request over identity
  }
}
```

- **`localStorage`, not `document.settings`** — deliberately. `document.settings`
  (slice 1's `document_id`) travels *inside the file*, giving a different id per
  document; an attorney id must be the *same across all the attorney's
  documents*. `localStorage` is scoped to the add-in origin and was confirmed to
  survive the Word-for-Mac pane teardown during slice-1 smoke.
- Synchronous (localStorage is sync) — no `await` needed, unlike `resolveDocumentId()`.

`clients/word/src/api.ts` — `postQuery` sends the resolved id:

```ts
headers: { "Content-Type": "application/json", "X-User-ID": resolveAttorneyId() }
```

The backend already maps `X-User-ID` → `state["user_id"]`
(`api/routes/query.py`), so no backend identity plumbing changes.

**`attorney_id` is a partitioning key, not authentication.** It selects a
thread; it does not verify the caller. This is consistent with the project's
deferred-auth posture (`X-User-ID: anonymous` today). Spoofable, and that is
acceptable for a few trusted internal users.

**Interim caveat (accepted):** this identifies the install/browser profile, not
the person. Same attorney on two machines → two threads until slice 3 (O365
SSO) overwrites the id at the same header seam. No backend change is needed when
SSO lands.

### 3. Write path — extend `memory_writer` (`graph/nodes/memory_writer.py`)

`memory_writer` already owns durable SQLite writes (audit + review). Add a
conversation append, guarded to the doc-chat path, mirroring the review block:

- Only when `task_type == "research"` and `conversation_store_enabled`.
- Only when `document_id` **and** `user_id` **and** `llm_response` are present.
  A no-doc research turn has no `document_id` → skipped (Chainlit KB research,
  scripts). NOTE: `intake.py` backfills a preamble-hash `document_id` when no
  client UUID is sent, so a *doc-attached* Chainlit research turn IS persisted
  — safely isolated by `(document_id, attorney_id)`, but keyed by the fragile
  preamble hash (slice-1's accepted limitation) rather than the stable Office
  UUID the Word client sends. This is intended and harmless: continuity for any
  doc-attached chat, per attorney, with no cross-tenant risk.
- Lazy `init_conversation_db` once (module flag, like `_review_db_initialized`).
- **Best-effort:** wrap `append_turn` in `try/except`, log on failure, **never
  raise and never fail the turn**. (Contrast the review write, which surfaces
  `review_persist_error` to the user — a conversation loss is a convenience
  loss, not a lost legal record.)

Review turns are **not** written to `conversation_store`: their assistant
message is the full markdown review, already persisted in `review_store` and
injected separately via `_load_prior_review_block`.

### 4. Read path — `_run_doc_chat` (`skills/legal_research.py`)

New helper `_load_prior_conversation(state) -> list[dict]`:

- Returns `[]` when `conversation_store_enabled` is False, or `document_id` /
  `user_id` missing.
- Else `load_recent(sqlite_path, document_id, user_id, conversation_max_messages)`.
- On read failure: `logger.error`, set `state["memory_degraded"] = True`, return
  `[]` (mirrors `_load_prior_review_block` — memory must never break the turn).

In `_run_doc_chat`, replace the Redis-sourced history with the durable store,
falling back to Redis when the store is empty/disabled:

```python
chat_history = _load_prior_conversation(state)
if not chat_history:
    chat_history = state.get("chat_history", []) or []
```

Single source per turn → no double-counting. Within a session the store and
Redis agree (the store is appended each turn); across sessions only the store
has the history. Everything else in `_run_doc_chat` (grounding, review block,
`_cap_chat_context`) is unchanged.

### 5. Config (`config.py`)

- `conversation_store_enabled: bool = True` — master switch.
- `conversation_max_messages: int = 20` — cap on messages injected into the
  prompt (~10 turns). The store retains everything; only the injected window is
  capped. Kept well under `chat_context_max_chars`.

## Data flow

**Chat turn (write):** attorney asks → graph runs the research/doc-chat skill →
`output_formatter` → `history_appender` (Redis, unchanged) → `memory_writer`
appends `(request, llm_response)` to `conversation_store` under
`(document_id, user_id)` → END.

**Chat turn (read):** `_run_doc_chat` loads the last `conversation_max_messages`
for `(document_id, user_id)` and injects them as the conversation history in the
prompt.

**Cross-session continuity:** new pane / new `session_id` / different machine
(same install) → Redis thread is empty, but the store still returns the prior
conversation for `(document_id, user_id)` → chat continues seamlessly.

## Error handling / degraded posture

- **Write failure:** logged, non-fatal, turn still answers. (Best-effort;
  distinct from the loud review write.)
- **Read failure:** logged, `memory_degraded=True` (already surfaced as the
  amber Word banner), turn still answers from grounding/doc/review.
- **Identity failure (client):** `resolveAttorneyId` falls back to `"word-addin"`
  so a request never breaks over identity.
- **Store disabled:** `conversation_store_enabled=False` → no reads/writes;
  behavior reverts to the current Redis-only chat history.

## What stays untouched

- **Redis checkpointer** — interrupt/resume + within-session hot path.
- **`history_appender`** — remains Redis-only (the `chat_history` reducer path).
- **`review_store`** — reviews stay keyed by `document_id` only (shared across
  attorneys; a review is a document fact, a chat is a personal thread).
- **`document_id` resolution** — slice-1 seam (`intake.py`) unchanged.

## Testing strategy

**Backend (pytest):**

- `conversation_store`: append → exactly two rows; `load_recent` ordering
  (chronological) and cap; **per-attorney isolation** (same doc, two attorneys →
  disjoint); **per-document isolation**; empty `document_id`/`attorney_id` → `[]`;
  unknown key → `[]`.
- `memory_writer`: research turn with `document_id`+`user_id` → `append_turn`
  called with the right args; review turn → **not** appended to conversation;
  missing `document_id` or `user_id` → skipped; `conversation_store_enabled=False`
  → skipped; `append_turn` raising → turn still returns (no exception escapes).
- `_run_doc_chat` / `_load_prior_conversation`: loads and injects durable history;
  falls back to `state["chat_history"]` when the store is empty; read failure →
  `memory_degraded=True` and turn continues.

**Frontend:**

- `attorneyIdentity`: read-or-create against a `localStorage` mock (mint once,
  reuse thereafter); `localStorage`-throws → `"word-addin"` fallback.
- `npx tsc --noEmit` clean; api.ts sends the header (typecheck + smoke).

**Human sideload smoke (Word for Mac):**

1. Review + chat a follow-up on doc A → close and reopen the pane (new
   `session_id`) → ask another follow-up → prior conversation is recalled.
2. **Deterministic DB check** (the slice-1 lesson): inspect
   `conversation_store` rows — confirm they are keyed by the real
   `attorney_id` UUID and `document_id` UUID, not stale/hash values.
3. **Restart `bash scripts/start.sh` first** — uvicorn does not auto-reload
   Python changes (slice-1 false-positive cause).

## Out of scope (deferred follow-ups)

- **Stale-recall reconciliation** (`docs/wiki.md:554`) — about the *review*
  snapshot going stale after edits; orthogonal to conversation persistence.
- **O365 SSO attorney identity** (slice 3) — replaces the per-install id with
  the real O365 user at the same header seam.
- **Shared / collaborative threads** — "later maybe shared"; slice 2 is
  per-attorney only.
- **Conversation retention / pruning policy** — the store grows unbounded;
  only the injected window is capped. Revisit if `data/legal.db` size becomes a
  concern.

## Decisions resolved

- **Attorney identity = per-install `localStorage` UUID** (option A). Rejected:
  shared-header-dormant (does not deliver per-attorney now) and manual-name
  field (friction + typo-forking + throwaway at SSO).
- **Write location = `memory_writer`** (colocated with existing durable writes),
  not a new node — consistent with the review block; DRY.
- **Store is additive to Redis**, not a replacement — Redis retains
  interrupt/resume and within-session state.
