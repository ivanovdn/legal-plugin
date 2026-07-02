# Legal agent — memory & context work: implementation plan

This is the handoff for the next round of work on the contract-review agent. Read this file first, then the per-step specs (`01`–`04`). Code is intentionally not specified here — these docs define **what to build and why**. Implement against the real node names in the codebase; where this doc names a node, confirm it exists before wiring to it.

## Product context (don't lose this)

- The agent reviews contracts **against a firm playbook**, and applies redlines **by chat command**. Single firm, single tenant.
- Two surfaces: a **Findings tab** (structured review) and a **Chat tab** (conversational redlining). They currently have **asymmetric knowledge** — the Findings tab loads the playbook and (for SOWs) the governing MSA; the Chat tab loads neither. This asymmetry is the root of several bugs below.
- Local model (Ollama). **Latency is the dominant constraint.** Tool calls / extra LLM round-trips were previously removed from the chat path because they added multi-minute latency for no gain. Do not reintroduce extra round-trips without a measured reason.

## The guiding principle for all of this

Build the dumb version, measure with the token telemetry already in place, and only add cleverness when measurement shows the simple thing failing. Several "obvious" optimizations below are explicitly deferred for this reason. Resist building them early.

## The five problems, consolidated

The audit notes and the prior design discussion describe the same issues from two directions. Consolidated:

| # | Problem | Root cause |
|---|---------|-----------|
| A | After a review, chat can't recall findings | Assistant replies trimmed to 300 chars in history; full review is discarded |
| B | Memory doesn't survive pane close / bleeds across docs | Session key = pane lifetime, not document |
| C | "Does this SOW conflict with the MSA?" fails in chat | MSA + playbook attached on Findings tab only, never on chat path |
| D | Chat context can overflow | Whole document re-sent every turn, uncapped (Findings path caps MSA at 24k chars; chat path has no cap) |
| E | Memory silently vanishes intermittently | If Redis is down, checkpointer returns None → every turn becomes stateless, with no signal |

A + B are one piece of work (persist the review, keyed to a document — you can't recall what you can't key). C is its own piece. D is a guard that must land alongside C (adding MSA+findings to chat is what makes overflow real). E is a reliability floor that must come first because every step below writes *more* state.

## Build order (and why)

1. **Step 0 — reliability floor (problem E).** First, because steps 1–2 write more state to Redis; a silent-stateless failure does more damage as you add memory. → `01_reliability_floor.md`
2. **Step 1 — persist + inject findings (problems A + B).** The core feature. Introduces the `document_id` concept. → `02_persist_findings.md`
3. **Step 2 — MSA + playbook on chat path (problem C).** The highest-value single gap for this product. → `03_msa_playbook_on_chat.md`
4. **Step 3 — context cap guard (problem D).** Lands with/after step 2 so the new context additions can't overflow the window. → `04_context_cap.md`
5. **Later — document_id quality upgrade.** Step 1 keys everything on a `resolve_document_id()` function with a cheap implementation; upgrading it to a real Office.js document property is a swap of that one function. Not now. (See `02`.)

The Hermes-style FTS cross-matter precedent recall (also later) lands naturally on the SQLite review store from Step 1 — choosing SQLite now means it's a query away, not another migration.

## Decisions to confirm before building

These are flagged in the relevant specs too. Defaults given so work can proceed if not overridden.

1. **Re-review of a changed document: append a new session, or overwrite the latest?** Default recommendation: **append** (legal rarely wants to silently discard a prior analysis), but store the structure list-shaped either way so switching is not a migration. (Spec `02`.)
2. **Persistence target — resolved.** Two separate stores: **Redis stays the checkpointer** (live conversation state); **persisted reviews go to SQLite** (durable, queryable, FTS-ready for the later precedent layer). Do not store the durable legal record in the ephemeral-by-default cache. Ship-fast exception only: reuse Redis with persistence enabled, knowing SQLite is the next step. (Spec `02`, "Two stores, two roles.")
3. **Whether to mirror the MSA (not just the playbook) onto the chat path immediately.** Recommendation: **yes** — the MSA is the concrete payload behind problem C. (Spec `03`.)
