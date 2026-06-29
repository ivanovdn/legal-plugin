# Step 1 — Persist & inject the review (the core fix)

Solves: chat can't recall findings (300-char trim), and memory doesn't survive pane close / bleeds across docs (session-keyed, not document-keyed).

## The core idea

Stop discarding the review. When a review runs, persist the **full structured review** keyed to the **document** (not the session/pane). On the chat path, load that review back and inject it so follow-ups *recall* the findings instead of *re-deriving* them from the raw document.

"Expand on the IP risk you flagged" should resolve against the actual stored finding — its clause ref, severity, rationale — not force the model to re-analyze the document and possibly contradict its own prior review.

## Three parts

### 1. Emit the review as structured findings, not prose

Change the review output so each finding is a structured object, e.g.:

```
{
  id, clause_type, clause_ref, severity,
  headline,        // self-contained one-liner, meaningful on its own
  detail,          // full rationale
  proposed_edit    // optional
}
```

Key points:
- `headline` and `detail` are **siblings written in the same review call** — not a separate summarization pass. No second LLM call, so no drift between summary and finding.
- `headline` must be **self-contained** (e.g. "Work-product ownership ambiguous (§11)", not "see analysis"). Enforce in the prompt.
- For now this split is *forward-prep only* — see "What to inject" below; we inject everything, so the split isn't load-bearing yet. But emitting it now costs nothing and avoids a re-parse later.

### 2. Persist it, keyed to a document_id, list-shaped

- **Key on a `document_id`, via a single resolver function** — e.g. `resolve_document_id()`. All storage/retrieval code keys on this. The *quality* of the id is deferred (see below); the *concept* lands now.
- **Store shape: one record per document containing an ordered list of sessions.** Even if it only ever holds one entry today, make it a list. Adding session history later is then an append, not a schema migration.

```
document_id
  └─ sessions: [ { timestamp, findings: [...], (chat turns if useful) }, ... ]
```

- Document is the **retrieval key** (continuity across pane reopens). Session is the **organizing unit inside** (recency + provenance). Do not flatten all sessions into one undifferentiated pile (destroys recency — stale findings contradict current ones) and do not key by session (that's the bug we're fixing — islands, no cross-session recall).

### 3. Inject on the chat path

- On the chat path, before assembling the prompt, look up the stored review by `document_id`.
- **Inject the latest session's findings in full.** This is the source of truth for the document as it stands.
- Older sessions (once they exist): inject as **one-line summaries only**, for continuity — not full replay. (Not needed until re-review exists; see decision below.)
- **Remove / replace the 300-char history trim** for the review content. The trim is the bug.

## What to inject — keep it dumb for now

**Inject all findings from the latest review, in full.** Do **not** build selective injection / "expand on demand" / a finding-fetch tool yet.

Reasoning: selective injection exists only to solve "the review doesn't fit alongside the document in the context window." At ~a dozen findings, it fits. Building the expand mechanism now solves a problem you don't have and adds failure surface (mismatching, extra round-trips on a latency-bound local model). The structured `headline`/`detail` split above is the *only* forward-prep needed; the moment the token telemetry shows reviews not fitting, selective injection becomes a small change on top of that structure.

## Two stores, two roles — do not conflate them

There are two distinct storage jobs here. They are not competing for one role; you need both, and they should be different stores because they have opposite requirements.

1. **Checkpointer — live conversation state. Stays Redis.**
   The LangGraph thread / rolling working state. Fast, ephemeral, in-memory. This is Redis's correct job and it does not change. Problem E (Redis down → stateless turn) is about *this* role — the fix is Step 0 (fail loud), not replacing the store.

2. **Review store — persisted findings. Use SQLite.**
   The structured reviews keyed to `document_id` that must survive across sessions, pane closes, and restarts. This is durable legal-record data, not working state.

**Why SQLite for the review store, not Redis:**
- Redis is in-memory and ephemeral *by default*. It can be configured for durability (AOF/RDB), but its native posture is "cache that may evaporate." Storing the durable legal record in the ephemeral-by-default store is the exact mismatch behind problem E, relocated. SQLite is durable by nature — survives restarts, no eviction surprises.
- The review store needs **queries** ("find the review for this document_id"; later, "find findings mentioning indemnity across matters"). SQLite does structured queries and FTS5 full-text search natively; Redis is awkward at both.
- It is the natural home for the **Hermes-style FTS cross-matter precedent recall** on the roadmap. Choosing SQLite now means that layer is a query away, not a second migration. (Hermes itself uses SQLite + FTS5 for exactly this reason.)
- Single-firm, single-tenant, local-model deployment — SQLite is sized exactly right; nothing here warrants a heavier database.

**Ship-fast exception:** if landing the persist-and-inject loop immediately matters more than avoiding a later migration, reusing Redis for the reviews is acceptable **only with Redis persistence (AOF/RDB) enabled** so reviews don't evaporate — and with the explicit understanding that the FTS precedent layer will force a move to SQLite later. Given FTS precedent recall is already on the roadmap, starting on SQLite avoids doing this work twice.

## document_id quality — deferred on purpose

`resolve_document_id()` should have a **cheap implementation now** and a **real one later**:
- **Now (interim):** hash of a stable region of the document (title + parties — something that does NOT change as clauses get redlined). Do **not** hash the whole document body — the user is actively editing it, so a full-text hash changes on every redline and orphans the stored review. Falling back to the existing session id is acceptable only to ship the plumbing, with the known limitation that it stays pane-scoped until upgraded.
- **Later (correct):** an Office.js custom document property — a UUID written into the document the first time it's seen, read back on every open. Survives pane close, reopen, even emailing the file. This is a swap of `resolve_document_id()` only; nothing else changes.

Confirm what the Word integration already exposes (any document identifier or custom-property access) before finalizing the interim — if a real id is already available, skip the hash.

## Decisions to confirm

1. **Re-review of a changed document: append a session, or overwrite latest?** Recommendation: **append** — legal rarely wants a prior analysis silently discarded; the list shape supports it natively. If shipping fastest matters more, **overwrite-latest** is fine and the list shape lets you switch to append later with no migration.
2. **Persistence target — resolved (see "Two stores, two roles" above).** Checkpointer stays **Redis** (live state); persisted reviews go to **SQLite** (durable, queryable, FTS-ready). The only thing left to confirm is the ship-fast exception: if you must reuse Redis for reviews to land faster, enable Redis persistence and treat the SQLite move as a known next step. Default: start on SQLite for reviews.

## Done when

- A review persists in full, structured, keyed by `document_id`.
- Closing and reopening the pane on the same document still surfaces the prior review on the chat path (once a real-enough `document_id` is in place).
- "Expand on the [X] you flagged" answers from the stored finding, consistent with the original review, without re-deriving.
- The 300-char trim no longer governs what chat knows about the review.
