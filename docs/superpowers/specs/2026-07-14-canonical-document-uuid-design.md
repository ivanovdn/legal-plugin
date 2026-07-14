# Canonical document UUID — design

> **Status:** approved design, ready for an implementation plan.
> **Scope:** Foundation slice #1 of the "server-side multi-attorney chat continuity" initiative.
> Adopt a stable, document-embedded UUID as the canonical `document_id` so the **review store**
> (write + recall) survives placeholder fills / redlines. **No conversation store and no
> attorney identity in this slice** — those are the next specs.
> **Source:** the sideload smoke test of the reverted `feat/chat-persistence-pane-reopen`
> branch (2026-07-14), which proved the preamble-hash identity drifts when the review workflow
> fills fields in the document's opening block. Supersedes the localStorage approach in
> `docs/superpowers/specs/2026-07-13-chat-persistence-pane-reopen-design.md`.

## Problem

Document identity is currently a **preamble hash** — `memory/document_id.py::resolve_document_id`
hashes the normalized first ~800 chars (title + parties + recitals). It was chosen on the
assumption that the opening block is stable while the body is redlined. Smoke testing disproved
that for real template contracts: the first 800 chars of the demo NDA contain **fill-in fields**
(`effective as of [date]`, receiving-party `[legal name]` / `[address]`) that get populated during
exactly the review/redline workflow the tool supports. Filling any of them changes the hash, so
the **same logical document produces different ids across sessions**.

The user-visible consequence proven in smoke testing (diagnostic dump): a document reviewed under
id `05c297…` later resolved to `fae237…`, so `load_latest_review` found nothing. Because the chat
path recalls the prior review by `document_id` (`skills/legal_research.py:387`), **filling a
placeholder silently drops the review from chat context** — a review-quality bug hiding in shipped
behavior. The same fragility would sink any future per-document persistence (e.g. the conversation
store).

## Guiding principle

Key document identity off something that travels **inside the document** and is **immune to
content edits** — a UUID stored in `Office.context.document.settings`, minted once and read back
thereafter. When no such id is available (non-add-in callers, unsaved docs, settings failure),
fall back to today's preamble hash so nothing regresses. The change is centralized and additive.

**Validated mechanism:** during the smoke-test debugging we confirmed a UUID written to
`Office.context.document.settings` **survives a task-pane close/reopen** (`settings docId(before)`
came back with the same UUID where the preamble hash did not) — so this identity is proven on the
target platform before we build on it.

---

## Architecture — one resolution seam

Identity resolution is already centralized: `graph/nodes/intake.py:39` sets `state["document_id"]`
**once**, and both consumers read that field:
- review **write** — `graph/nodes/memory_writer.py:57` (`save_review(document_id=state["document_id"], …)`)
- review **recall read** — `skills/legal_research.py:387` (`load_latest_review(…, document_id)`)

So adopting the UUID is a single behavioral change at the seam: **prefer a client-supplied id,
else compute the hash.** Every downstream consumer inherits the fix with no change.

## Components

1. **Client — `clients/word/src/docIdentity.ts` (new).**
   `resolveDocumentId(): Promise<string>` reads-or-creates a UUID in
   `Office.context.document.settings` under key `legalTriageDocId`:
   - `settings.get("legalTriageDocId")` → return it if a non-empty string.
   - else `crypto.randomUUID()`, `settings.set(...)`, `await settings.saveAsync(...)`, return it.
   - any failure (settings unavailable, save error) → return `""` (caller proceeds without an id).
   No document text is read or hashed on the client.

2. **Client — `clients/word/src/api.ts`.**
   `submitReview` and `chatQuery` call `resolveDocumentId()` and include `document_uuid` in the
   POST body. Both paths send it (the review path *writes* under it; the chat path *recalls* under
   it — they must agree).

3. **API model — `api/models.py::QueryRequest`.**
   Add `document_uuid: str = Field("", description="Client-supplied stable document id (Office custom setting); falls back to the server-side preamble hash when empty")`.

4. **Route — `api/routes/query.py`.**
   Seed the existing state field from the request: `"document_id": body.document_uuid` (replacing
   the hardcoded `""` at line 167).

5. **`graph/nodes/intake.py` (the one behavioral line).**
   Change line 39 from `state["document_id"] = resolve_document_id(text)` to:
   `state["document_id"] = state.get("document_id") or resolve_document_id(text)`
   — honor a client-supplied UUID, else compute the preamble hash. `review_store.py`,
   `memory_writer.py`, and `legal_research.py` are **unchanged**.

## Data flow

```
add-in interaction
  → resolveDocumentId() reads/mints the doc's UUID in Office settings
  → POST /api/query { …, document_uuid: "<uuid>" }
  → query.py: initial_state["document_id"] = "<uuid>"
  → intake: state["document_id"] = "<uuid>" (non-empty → kept)
  → review path: save_review(document_id="<uuid>", …)
  → later chat turn (preamble edited): same "<uuid>" sent
  → legal_research: load_latest_review(document_id="<uuid>") → FOUND
```

## Default decisions (settled during brainstorm)

- **Fallback, not requirement.** No `document_uuid` (Chainlit web client, unsaved doc, settings
  failure) → intake computes the preamble hash exactly as today. Purely additive; no caller breaks.
- **No migration.** Existing review rows keyed by preamble hash are left as-is; they orphan, and
  fresh reviews key by UUID. The store is early/dev-stage with few rows.
- **Review store stays per-document.** All attorneys share a document's review. Per-attorney
  scoping is only for the later conversation store.
- **Lazy minting.** The UUID is created on the first backend interaction, not proactively on every
  document open. It persists once the document is saved (the workflow already saves).

## Edge cases

- **Unsaved / never-saved doc** → `saveAsync` can't persist; the UUID lives only in memory for the
  session; next open mints a new one → new id (review re-keys). Same "save to persist" caveat as
  any doc-embedded id; acceptable.
- **Settings unavailable / `saveAsync` fails** → `resolveDocumentId()` returns `""` → the turn
  still runs, identity falls back to the preamble hash. No crash, no blocked turn.
- **Co-authoring UUID race** (two attorneys open a brand-new doc simultaneously, both mint) → last
  write wins on settings merge; a brief divergence is possible before a save syncs. Rare; benign
  (worst case a review re-keys once). Not mitigated in this slice.
- **Chainlit web client** cannot write Office settings → always uses the preamble-hash fallback,
  unchanged. Its per-file-upload flow keeps working.
- **Empty document text AND no UUID** → `resolve_document_id("")` returns `""` → review store
  no-ops on an empty id (existing `load_latest_review`/`save_review` guards), as today.

## Non-goals / Out of scope

- The **server-side conversation store** (append-per-turn, fetch-on-open, render) — next spec.
- **Attorney identity / O365 SSO** — not needed here (the review store is per-document); it enters
  with the conversation store.
- **Making Chainlit supply a UUID** — it stays on the preamble-hash fallback.
- **Migrating old review rows** — see default decisions.
- Any change to the Redis `chat_history` reducer, the checkpointer, or the graph shape.

## Testing

- **Backend unit — intake id precedence:** a state with `document_id` preset (client UUID) is
  preserved through `intake`; an empty `document_id` falls back to `resolve_document_id(text)`.
- **Backend unit — request plumbing:** `QueryRequest(document_uuid=…)` reaches
  `initial_state["document_id"]` in `submit_query` (assert via the existing route test pattern).
- **Backend unit — recall stability:** `save_review` under a UUID, then `load_latest_review` by the
  same UUID returns it — and (the regression the fix targets) recall does **not** depend on the
  document text, so a changed preamble with the same UUID still recalls. (Extends the existing
  `memory/review_store.py` tests.)
- **Client:** `docIdentity.ts` is `Office.context.document.settings` integration → **smoke-tested**,
  consistent with existing Office.js code (no unit harness). `api.ts` field addition is typed and
  covered by `tsc --noEmit`.
- **Smoke (the real gate):** review a document → fill a placeholder in the opening block → ask a
  chat follow-up about the review → the prior review is still recalled (the exact failure observed
  in the 2026-07-14 smoke test), proving the fix end-to-end across NDA + one other type.

## Risks / rollback

- **Risk: low.** One resolver line + an additive optional request field + a client settings
  handshake. Non-UUID callers are byte-for-byte unaffected (they hit the same preamble-hash path).
- **Rollback:** revert `intake.py` to unconditional `resolve_document_id(text)` (ignores
  `document_uuid`); the client field and model field become inert. No data migration to undo.

## Relationship to the initiative

This is slice #1 of three (see the 2026-07-14 brainstorm): **(1) canonical document UUID [this
spec]** → (2) server-side per-attorney conversation store (append-per-turn, keyed by
`(document_uuid, attorney_id)`) → (3) O365 SSO attorney identity (stubbed locally until scale).
Slice #1 delivers the review-recall fix on its own and is the stable-identity spine the later
slices key off.
