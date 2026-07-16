# Stale-Recall Reconciliation — Design

**Date:** 2026-07-15
**Status:** Approved (brainstorming) — ready for implementation plan
**Surface:** Backend only (`skills/legal_research.py`). No Word client / graph / config change.

---

## Problem

Chat recall is durable but point-in-time. On the doc-chat path, `_run_doc_chat`
(`skills/legal_research.py:508`) injects the **latest stored review** for the document via
`_load_prior_review_block` (`legal_research.py:384` → `load_latest_review`), with the directive
*"answer recall questions from this review; do not re-derive or contradict it."*

That directive is correct for a stable doc, but wrong after an edit. If the attorney fills a
placeholder the review flagged — e.g. `[Legal Name]` → `Sony`, or `Signed by: [__]` →
`Signed by: Suzy Quatro` — **after** the review was run, the recalled review still says the field
is unfilled, and the local model faithfully parrots it. Chat then reports an already-filled field
as an open placeholder / signature blocker.

Surfaced by the canonical-UUID smoke (2026-07-14): durable recall made the point-in-time nature of
the stored review visible. Today's only workaround is a manual re-review after material edits.

## Goal

On the chat recall path, deterministically drop the review findings that the **current** document
proves are stale — specifically placeholder/blank-field findings whose quoted text is no longer in
the document — before the review is injected into the prompt. The model never sees the contradictory
"unfilled" claim, so it cannot repeat it.

## Non-goals

- **No reconciliation of substantive findings.** A Red/Yellow on a real clause (indemnity,
  liability, IP) cannot be verified against text deterministically — those findings are left
  exactly as-is. Only placeholder/blank-field findings are in scope.
- **No gate-verdict recomputation in v1.** We do not rewrite the "No Signature Checklist Result"
  verdict (fabricating a gate result is riskier than the note approach — see Behavior).
- **No re-review nudge / UI element.** Backend-only; the Findings-tab review path already
  re-derives from the current doc, so it is never stale and is unchanged.
- **No prompt change.** This is a code-side reconciliation, consistent with the standing
  "fix in code, not prompt" principle. `CHAT_SYSTEM_PROMPT` / `_JSON_RETRY_SYSTEM` are untouched.

## Approach (chosen)

**Deterministic placeholder reconciliation, backend-only.** Chosen over (a) a prompt directive
telling the model to cross-check the review against the doc — the model already parrots the review,
so an added instruction is unreliable and model-specific; and (b) a re-review nudge — coarser,
warns instead of correcting, needs a doc-divergence signal we do not track, and pushes a ~30s
re-review onto the user. The deterministic approach removes the exact wrong answer, is model-neutral,
and reuses the established placeholder vocabulary (`[__]`, `[Legal Name]`, `[Date]`, `[Address]`).

## Design

### Component: `_reconcile_review_with_doc` (new pure helper)

Location: `skills/legal_research.py`, adjacent to `_strip_redlines_section` (`:355`).

```
_reconcile_review_with_doc(review_markdown: str, doc_text: str) -> tuple[str, list[str]]
```

- **Returns:** `(reconciled_markdown, filled_tokens)` — the review with stale placeholder finding
  lines removed and (when any were removed) a reconciliation note prepended, plus the list of
  quoted tokens found to be filled (for the note + tests + logging).
- **Pure:** no I/O, no LLM, no DB. Fully unit-testable.

Algorithm:

1. **Extract placeholder candidates** from `review_markdown` — the *quoted / bracketed* strings
   that carry a placeholder marker:
   - backtick-quoted substrings (`` `...` ``) that contain a placeholder marker,
   - bracketed tokens `\[[^\]]{0,40}\]` (e.g. `[Legal Name]`, `[__]`, `[Date]`, `[Address]`),
   - underscore blanks `_{2,}`.
   Exclude generated-draft source tags `[Source: <id>]` (same exclusion the Layer-1 placeholder
   work uses) so a draft's heading tags are never treated as fillable blanks.
2. **Normalize** both a candidate and `doc_text` before comparison: NFC, curly→straight quotes,
   nbsp→space, collapse runs of whitespace to one space. (Light inline normalization — no
   dependency on the Word client's `normalize.ts`.)
3. **Classify stale:** a candidate is *stale* (filled/removed since the review) when its normalized
   form is **not present** in the normalized `doc_text`. Matching the **whole quoted string**
   (label + blank, e.g. `Signed by: [__]`) — not the bare token — disambiguates a generic `[__]`
   that recurs across fields: only the specific filled field is classified stale.
4. **Remove stale finding lines:** drop each `review_markdown` line that references a stale
   candidate. Placeholder findings are single-line markdown table rows / bullets, so dropping a
   line removes exactly one finding and leaves the surrounding table valid (fewer data rows).
   Never drop section headings or lines that contain no stale candidate.
5. **Prepend a reconciliation note** when ≥1 candidate was removed:
   > `> **Auto-reconciled:** N placeholder(s) flagged below were filled in the document after this
   > review and have been removed from the recalled findings: <tokens>. If these were the only
   > signature-block blockers, re-review to confirm the No-Signature gate now passes.`
   This is a factual transparency note, not an instruction the model must obey — the stale lines
   are already gone, so correctness does not depend on the model reading the note.
6. **No-op path:** when no candidate is stale, return `review_markdown` unchanged and `[]` — the
   output is byte-identical to the input.

### Call-site change: `_load_prior_review_block`

`_load_prior_review_block(state)` (`:384`) currently does:
`review_text = _strip_redlines_section(latest["markdown"])`, then wraps it in the PRIOR REVIEW block.

Signature changes to `_load_prior_review_block(state, uploaded_text)` — `_run_doc_chat` already holds
`uploaded_text` and calls it at `:530`, so we pass that exact string (the same text sent to the LLM
this turn) rather than re-joining from state. New flow:
1. `stripped = _strip_redlines_section(latest["markdown"])`
2. When `uploaded_text` is empty, skip reconciliation (cannot verify) and inject `stripped`.
3. `reconciled, _ = _reconcile_review_with_doc(stripped, uploaded_text)` — wrapped in try/except.
4. Inject `reconciled` in the PRIOR REVIEW block.

Update the one caller (`_run_doc_chat:530`) to `_load_prior_review_block(state, uploaded_text)`.
No backwards-compat shim (per project rule #5 — change the call site).

### Error handling

Reconciliation is a best-effort enhancement, never a memory failure:
- The `_reconcile_review_with_doc` call in `_load_prior_review_block` is wrapped in try/except.
- On **any** exception, or when `doc_text` is empty, fall back to injecting the review
  **unchanged** (the `_strip_redlines_section` output) — never empty, never a dropped turn.
- It does **not** set `state["memory_degraded"]` — that flag is reserved for actual store
  read/write failures (`load_latest_review` / `load_recent`), not a reconcile hiccup. Log at WARN.

### Data flow

```
_run_doc_chat(state, uploaded_text)
  └─ _load_prior_review_block(state, uploaded_text)          # NEW 2nd param
       ├─ load_latest_review(sqlite, document_id)            # unchanged (loud store read)
       ├─ _strip_redlines_section(markdown)                  # unchanged
       └─ _reconcile_review_with_doc(stripped, uploaded_text) # NEW: drop stale placeholder findings
            → reconciled markdown injected in PRIOR REVIEW block
```

## Testing

Pure-function unit tests for `_reconcile_review_with_doc` (no LLM, no DB):

1. **Filled placeholder dropped + note added** — review quotes `` `Signed by: [__]` ``; doc now has
   `Signed by: Suzy Quatro` → that finding line removed, note lists the token.
2. **Still-unfilled kept** — token still present in doc → review returned unchanged, no note.
3. **Generic-blank disambiguation** — doc still contains other `[__]`s, but the specific
   `Signed by: [__]` is filled → only that finding dropped, the others kept.
4. **No placeholders** — review with only substantive findings → byte-identical output, `[]`.
5. **Substantive finding untouched** — a Red indemnity row that happens to sit near a stale
   placeholder row → the indemnity row is kept; only the placeholder row is dropped.
6. **Source-tag exclusion** — `[Source: abc123]` in the review is never treated as a fillable
   blank, even if absent from the doc.
7. **Normalization match** — curly-quote / nbsp / whitespace variance between review and doc still
   classifies a present token as not-stale (no false drop).
8. **Malformed / odd markdown** — no crash; safe output.

Call-site test for `_load_prior_review_block`:

9. **Injects reconciled block** — with a filled placeholder in the passed `uploaded_text`, the
   injected PRIOR REVIEW block omits the stale finding.
10. **Reconcile error → raw review** — force `_reconcile_review_with_doc` to raise (monkeypatch);
    `_load_prior_review_block` still returns the (un-reconciled) review block, no exception.

All tests offline. Full suite must stay green (currently 317 backend).

## Files

- **Modify:** `skills/legal_research.py` — add `_reconcile_review_with_doc`; call it in
  `_load_prior_review_block`.
- **Test:** `tests/test_stale_recall_reconciliation.py` (new) — the pure-helper + call-site tests.

## Risks & mitigations

- **False drop (removing a still-live finding):** the dangerous direction. Mitigated by
  whole-quoted-string matching + normalization; the "most-specific-wins per line" rule (a bare
  label can't out-vote its own filled span); and — added after the final review — **only
  single-marker backtick spans are droppable**, so a span bundling several markers
  (`Signed by: [__] / Title: [__] / …`, the MSA/SOW shape) is left intact and a partial fill can
  never vanish a still-live blocker.
- **Under-reconciliation (a stale finding the heuristic misses):** acceptable — the fallback is
  today's behavior (the finding stays). No regression vs. current state.
- **Gate/summary contradiction** (placeholder rows dropped but the gate line still says
  "unfilled"): mitigated by the reconciliation note; full gate recomputation deferred to a
  follow-up if it proves necessary.
- **Reconcile bug breaking chat:** mitigated by try/except → inject review unchanged.

### Known limitations (accepted — heuristic quote-vs-doc matching)

Reconciliation is a verbatim, normalized substring match of a quoted token against the current
document. Two low-probability over-drop cases fall out of that and are **accepted**, not fixed, in
v1 (both need the review to *bracket* text and the doc to differ verbatim — uncommon, since review
quotes are usually offending prose, and the failure only ever removes a finding, never invents one):

- **Bracketed defined term in a substantive finding.** A capitalized bracketed term like
  `[Confidential Information]` matches the bare-label pattern; if the body uses it *unbracketed*,
  the quote is "absent" → classed filled → that (possibly substantive) row is dropped. Qualifies
  the "substantive findings are never dropped" claim to "unless a substantive finding quotes a
  bracketed term the doc renders unbracketed."
- **Quote drift.** If a still-blank field is *relabeled* more verbosely in the doc
  (`Party: [Legal Name]` → `Party: [Legal Name of Counterparty]`), the exact quote is absent →
  the still-unfilled row is dropped.

Both are candidates for the future tightening "only treat a bare labeled bracket as a placeholder
when it also appears inside a backtick current-wording quote" (deferred; not needed for v1).

## Follow-ups (out of scope)

- Recompute / neutralize the "No Signature Checklist Result" gate verdict when all its placeholder
  blockers are reconciled away.
- Re-review nudge when the doc diverges beyond blank-fills (needs a doc-change signal).
- Reconcile substantive findings via clause-level retrieval (needs the structured-JSON review work).
