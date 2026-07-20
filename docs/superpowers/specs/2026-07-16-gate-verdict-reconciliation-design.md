# Gate-Verdict Reconciliation — Design

**Date:** 2026-07-16
**Status:** Approved (brainstorming) — ready for implementation plan
**Surface:** Backend only (`skills/legal_research.py`). No Word client / graph / config change.
**Builds on:** `feat/stale-recall-reconciliation` (2026-07-16). This is the deferred
"gate-verdict recomputation" follow-up from that spec's Follow-ups section.

---

## Problem

Stale-recall reconciliation drops placeholder finding *rows* from the recalled review when the
current document proves they were filled after the review (`_reconcile_review_with_doc`,
`skills/legal_research.py:435`). But the recalled review also contains a
`# No Signature Checklist Result` section — the gate verdict — whose text still says, e.g.:

```
# No Signature Checklist Result
Overall status: Do not send for signature
Blocking items: Signature block unfilled (`Signed by: [__]`); counterparty legal name blank (`[Legal Name]`)
Final recommendation: Fill signature block and legal name, then re-review.
```

After the placeholder rows are dropped, that gate block is **stale** — it names blockers that were
just reconciled away. The chat prompt injects the reconciled review with the directive *"answer
recall questions from this review; do not re-derive or contradict it"* (`_load_prior_review_block`,
`:503`), so the local model faithfully parrots a "signatures unfilled / do not send" verdict for a
document whose signatures are now filled.

Today's only defense is a top-of-block note ("If these were the only signature-block blockers,
re-review to confirm the No-Signature gate now passes"). That note sits far from the stale gate line
and the model may not connect the two. The shipped spec listed this exact "gate/summary
contradiction" as a residual risk and deferred gate recomputation as riskier than the note approach.

## Goal

On the chat recall path, after row-reconciliation has dropped placeholder findings, reconcile the
`# No Signature Checklist Result` section of the **injected** review so the model no longer reads a
stale placeholder-driven "do not send / unfilled" verdict — **without ever fabricating a
signature go-ahead**.

## Non-goals

- **Never assert a pass.** The strongest downgrade is `PENDING RE-REVIEW`. We never write
  "Ready for signature" / "signature may proceed" — flipping a legal gate to green from a heuristic
  is out of scope (this was the shipped spec's stated dangerous direction).
- **No reconciliation of the free-prose sections.** Stale "unfilled" mentions in `# Review Summary`
  or `# Business Questions` are not rewritten — they are diffuse prose, not the canonical verdict.
  The gate section is the one place the model is told not to contradict.
- **No change to the stored review or the Findings tab.** Reconciliation is chat-injection-only.
  The Findings tab re-derives from the current document each review (never stale); the stored review
  is the immutable record.
- **No prompt / graph / config / client change.** Code-side reconciliation, consistent with the
  "fix in code, not prompt" principle. `CHAT_SYSTEM_PROMPT` / `_JSON_RETRY_SYSTEM` are untouched.
- **No re-review nudge.** Detecting doc divergence *beyond* blank-fills needs a doc-fingerprint we do
  not persist at review time — that is the *next* branch, explicitly out of scope here.

## Approach (chosen)

**Annotate + conditional neutralize.** When row-reconciliation drops ≥1 placeholder token and the
gate cites at least one of those tokens:
1. **Annotate (always):** insert a factual correction note inside the gate section, adjacent to the
   stale line, naming the filled tokens.
2. **Neutralize (only when no substantive blocker survives):** additionally downgrade the
   `Overall status:` verdict to `PENDING RE-REVIEW`.

The confidence test for "no substantive blocker survives" reads the **structured Key Findings
table**, not gate prose: after the placeholder rows are dropped, count the surviving rows rated
**Red** or **Missing Context**; `count == 0` means the only blockers were the filled placeholders.
This mirrors the Word parser's `deriveBlockers` philosophy (blockers = Key Findings where
rating ∈ {Red, Missing Context}) — the source of truth wins.

Chosen over: (a) **recompute-to-pass** — fabricates a legal green-light from a heuristic, rejected;
(b) **strip the whole gate section** — simplest but discards any non-placeholder gate reason and
removes the gate even when partly valid; (c) **annotate-only** — safe but leaves the literal
"do not send" verdict for the model to parrot even in the clean all-placeholder case. Approach (2)'s
annotate step *is* option (c), so annotate-only is the guaranteed-safe subset it contains.

## Design

### Component: `_surviving_blocker_count` (new pure helper)

Location: `skills/legal_research.py`, adjacent to `_reconcile_review_with_doc` (`:435`).

```
_surviving_blocker_count(review_markdown: str) -> int | None
```

- Locates the `# Key Findings` section, parses its GFM table, finds the `Rating` column (header
  `rating` or `risk`, case-insensitive), skips the header + separator (`---`) rows, and counts data
  rows whose normalized rating is `red` or `missing context` (accept `missing-context` /
  `missing_context`, matching the Word parser's `normalizeRisk`).
- **Returns `None`** when the Key Findings section or its rating column can't be located/parsed —
  the caller treats `None` as "blockers may remain" (conservative: no downgrade).
- **Pure:** no I/O, no LLM, no DB. Runs on the row-dropped markdown, so already-removed placeholder
  rows never inflate the count.

### Component: `_reconcile_gate_verdict` (new pure helper)

Location: `skills/legal_research.py`, adjacent to `_reconcile_review_with_doc`.

```
_reconcile_gate_verdict(review_markdown: str, dropped_tokens: list[str]) -> str
```

- **Returns** the review markdown with the gate section reconciled (or unchanged). Pure.

Algorithm:

1. If `dropped_tokens` is empty → return `review_markdown` unchanged.
2. Locate the gate section: the first heading line matching `# No Signature Checklist Result`
   (tolerant — normalized heading contains `no signature checklist`; any `#` level). The section
   spans from that heading to the next heading line (`#…`) or end of document. If no gate heading is
   found → return unchanged.
3. Normalize the gate body and each dropped token (reuse `_normalize_for_match`, `:396`). If the gate
   body contains **none** of the dropped tokens as a normalized substring → return unchanged (the
   gate's blockers are about something other than what we filled).
4. `neutralize = (_surviving_blocker_count(review_markdown) == 0)` — `None` ⇒ `False`.
5. **Annotate (always in this branch):** insert, immediately after the gate heading line, a note:
   > `> **Reconciled:** placeholder blocker(s) cited in this gate — <tokens> — were filled in the`
   > `> current document after this review; treat them as resolved. The current document governs the gate.`

   `<tokens>` = the dropped tokens the gate actually references, backtick-wrapped, comma-joined.
6. **Neutralize (only when `neutralize`):** within the gate section, find the line beginning
   (case-insensitively, after optional list/`**` markup) with `Overall status:` and replace its
   value with:
   > `Overall status: PENDING RE-REVIEW — the placeholder blockers recorded here were filled after`
   > `this review and no other blockers remain; re-review to confirm signature readiness. (Do not`
   > `treat as approved for signature.)`

   If no `Overall status:` line is found, skip this step (degrade to annotate-only). Leave
   `Blocking items:` / `Missing context:` / `Final recommendation:` lines in place — never delete
   them, so a stray substantive reason can't be lost.
7. The output **never** contains "ready for signature" or "signature may proceed" (invariant,
   asserted in tests).

### Call-site change: `_reconcile_review_with_doc`

Currently (`:490-500`) it builds `"\n".join(kept)`, prepends the top note when `dropped_tokens` is
non-empty, and returns `(note + body, dropped_tokens)`. New flow:

1. `body = "\n".join(kept)`
2. `body = _reconcile_gate_verdict(body, dropped_tokens)` — new step.
3. Simplify the existing top note: **drop its trailing "If these were the only signature-block
   blockers, re-review to confirm the No-Signature gate now passes." clause** — the gate section now
   carries the precise, co-located correction. The note becomes a plain statement of what was
   removed:
   > `> **Auto-reconciled:** N placeholder(s) flagged in the prior review were filled in the`
   > `> document afterward and have been removed from the recalled findings: <tokens>.`
4. Return `(note + body, dropped_tokens)`.

No signature change to `_reconcile_review_with_doc` or `_load_prior_review_block`; the gate step is
internal. No backwards-compat shim.

### Data flow

```
_reconcile_review_with_doc(review_markdown, doc_text)
  ├─ _placeholder_candidates(...) / row-drop pass          # unchanged (shipped)
  ├─ body = "\n".join(kept)
  ├─ _reconcile_gate_verdict(body, dropped_tokens)         # NEW
  │    └─ _surviving_blocker_count(body)                   # NEW: confidence test
  └─ (top-note + reconciled body, dropped_tokens)
```

### Error handling

Consistent with the shipped feature — reconciliation is best-effort and must never break the turn.
The whole `_reconcile_review_with_doc` call already runs inside the `_load_prior_review_block`
try/except (`:526`), which on any exception injects the review unchanged and does **not** set
`memory_degraded`. The new helpers add no new failure surface beyond that net; on any internal
ambiguity they fail toward the conservative branch (no downgrade).

## Testing

Pure-function tests (no LLM, no DB), added to `tests/test_stale_recall_reconciliation.py`:

`_reconcile_gate_verdict`:
1. **Neutralize** — gate cites two dropped tokens; Key Findings has 0 surviving Red/Missing-Context
   rows → `Overall status` becomes `PENDING RE-REVIEW`, correction note present, output contains
   neither "ready for signature" nor "signature may proceed".
2. **Annotate-only (surviving blocker)** — gate cites dropped tokens but a surviving **Red** finding
   remains in Key Findings → status line unchanged (`Do not send for signature`), note present, no
   downgrade text.
3. **Gate cites a non-dropped token only** — the gate references a still-blank field we did not drop
   → returned unchanged, no note.
4. **No gate section** → returned unchanged.
5. **Gate present, no `Overall status:` line** → note inserted, no crash, no fabricated status.
6. **Key Findings unparseable / absent** → `_surviving_blocker_count` returns `None` →
   annotate-only (no downgrade) even though no blockers are detectable.
7. **Empty `dropped_tokens`** → byte-identical output.
8. **Never green-lights** — a dedicated assert that neutralize output matches neither
   `/ready for signature/i` nor `/signature may proceed/i`.

`_surviving_blocker_count`:
9. Counts **Red + Missing Context**, ignores Yellow/Green, skips header + separator rows; accepts
   `Rating`/`Risk` header and the `missing context` / `missing-context` / `missing_context` spellings.
10. Returns `None` when the Key Findings table (or its rating column) is absent.

`_reconcile_review_with_doc` (end-to-end, extends existing integration tests):
11. Filled placeholders + 0 surviving blockers → output has row drops + top note + a
    `PENDING RE-REVIEW` gate.
12. Filled placeholder + a surviving Red finding → row dropped + gate **annotated** with
    `Do not send for signature` retained.

All tests offline. Full suite must stay green (currently 330 backend → ~341).

## Files

- **Modify:** `skills/legal_research.py` — add `_surviving_blocker_count` + `_reconcile_gate_verdict`;
  call the gate reconciler in `_reconcile_review_with_doc`; simplify the top note.
- **Test:** `tests/test_stale_recall_reconciliation.py` — add the gate + blocker-count tests.

## Risks & mitigations

- **Under-stating a real blocker (the dangerous direction):** neutralize fires only when the
  structured Key Findings table has **zero** surviving Red/Missing-Context rows; any doubt
  (unparseable table, missing status line, mixed blockers) falls back to annotate-only with the
  DO-NOT-SEND verdict intact. `Blocking items:` text is never deleted. **The rating cell is stripped
  of emphasis markup before the blocker match** — `*`/backtick globally, plus `.strip("_")` for
  *surrounding* underscores (italic wraps like `_Red_`) — added after the final review, where a
  bolded `**Red**` (and, on re-review, an italic `_Red_`) was being missed, dropping a live blocker
  from the count and wrongly neutralizing the gate. Only *surrounding* underscores are stripped, so
  the `missing_context` spelling (internal `_`) is preserved (a global `_` strip, as the Word
  parser's `normalizeRisk` does, would collapse it to `missingcontext`).
- **Over-annotating:** the note only appears when the gate actually references a dropped token, so a
  gate about unrelated blockers is left alone.
- **Gate blocker present only in gate prose, not in Key Findings (LLM non-conformance):** rare;
  mitigated because neutralize preserves the `Blocking items:` text verbatim, so the reason stays
  visible even if the status is softened.
- **Reconcile bug breaking chat:** covered by the existing `_load_prior_review_block` try/except →
  inject review unchanged.

### Known limitations (accepted — documented, not fixed)

- Stale "unfilled" language in `# Review Summary` / `# Business Questions` is not reconciled — only
  the `# No Signature Checklist Result` verdict is. Candidate for a future extension if it proves to
  mislead chat in practice.
- **Co-located substantive reason in a dropped placeholder row.** If a single Key Findings row bundles
  a real substantive blocker *and* the only placeholder it references is later filled, the shipped
  row-drop pass removes the whole row, so `_surviving_blocker_count` under-counts and the gate may be
  neutralized while a real reason existed. Bounded by the safety net (never emits a pass; `Blocking
  items:` text preserved). Same family as "blocker only in gate prose"; low-probability (well-formed
  reviews give each blocker its own row).
- **Empty rating cell in a genuine data row** is skipped like a separator row → not counted. An
  unrated row is not a machine-detectable blocker (the Word `deriveBlockers` wouldn't count it
  either); bounded by the same safety net.
- **"Never green-lights" is guaranteed for text the code *generates*.** The neutral status string and
  notes contain neither forbidden phrase. Two *echo* edges require pathological/non-conformant input
  and are not the code asserting a pass: a placeholder token that literally contains "ready for
  signature" (e.g. a `[Ready for signature]` label) echoed in a note, and an LLM that leaves the
  unfilled slash-template gate line (`Overall status: Ready for signature / Do not send for
  signature`) verbatim on the annotate-only path.

## Follow-ups (out of scope)

- **Re-review nudge** when the document diverges beyond blank-fills — needs a doc fingerprint
  persisted at review time (the *next* branch).
- Reconcile the free-prose summary/business-question sections if they prove to carry stale verdicts.
- Reconcile substantive findings via clause-level retrieval (needs structured-JSON review output).
