# Clause-locator hardening (whole-word matching + label-only findings) — design

> **Status:** approved design, ready for an implementation plan.
> **Scope:** two frontend-only fixes to the Word add-in clause locator. **No backend,
> graph, prompt, or LLM change** — so zero risk to review quality.
> **Source:** the `docs/wiki.md` follow-up "Clause-locator hardening for placeholder /
> short anchors," surfaced by the 2026-07-09 click-to-jump sideload smoke test.

## Problem

The Word add-in locator (`findClauseRangeFromAnchors` in
[`clients/word/src/word.ts`](../../../clients/word/src/word.ts)) is shared by three
callers — `goToClause` (navigation), `showInDocument` (navigation + audit comment),
and `acceptRedline` (writes a tracked change). The 2026-07-09 smoke test on the model
NDA exposed two real failures:

1. **Mid-word mislocation on short/generic anchors.** `buildAnchors`
   ([`parser.ts:188`](../../../clients/word/src/parser.ts)) emits clause-name segments
   such as `"Title"` or `"Entity"`. `searchCandidates`' `length ≥ 12` filter normally
   drops these, but its `candidates.length === 0` fallback ([`word.ts:138`](../../../clients/word/src/word.ts))
   lets a short parser anchor through anyway. `body.search` is **not** whole-word by
   default, so `"Title"` matches inside `"en·title·d"` — the jump/comment/redline lands
   in the wrong place.

2. **Loud "Couldn't locate this clause" on label-only findings.** A signature /
   Missing-Context finding with no quoted wording has only *descriptive* anchors
   (`"Execution Block / Entity"`, `"Signature Block"`) — text not present in the
   document verbatim. Every candidate misses and the locator surfaces
   *"Couldn't locate this clause in the document,"* which reads like a bug rather than
   the honest fact that the finding describes (does not quote) a section.

### Not in the problem set

The follow-up note also listed *"apply the existing `escapeWordWildcards` +
wildcard-mode retry to the navigation locator."* **That is already live.**
`goToClause` → `findClauseRangeFromAnchors` → `findClauseRange` → `searchFirst`, and
`searchFirst` ([`word.ts:218`](../../../clients/word/src/word.ts)) already retries in
wildcard mode with metacharacters escaped whenever a needle contains `[](){}` etc. A
bare `[__]` is therefore already located (it matches the **first** occurrence). No
wildcard work is needed.

## Guiding principle

Frontend-only, additive, and **precision-over-recall for last-resort anchors**. Extract
the one non-trivial decision into a **pure, exported, unit-tested** function
(`shouldMatchWholeWord`), per the repo rule "*touching the Word add-in? smoke-test by
sideloading — `tsc --noEmit` is not enough*"; the Office.js wiring around it is
smoke-tested.

---

## Fix 1 — whole-word matching for short anchors

### Behavior

`searchFirst`'s literal search runs with Word's native **`matchWholeWord: true`** option
**only** when the trial is a **single word** — the `"Title"` / `"Entity"` class that
arrives through `searchCandidates`' `length === 0` fallback and is the one that actually
mislocates mid-word. Multi-word and longer trials keep today's substring behavior
untouched.

> **Threshold narrowed from ≤ 2 words to single-word (final-review decision,
> 2026-07-09).** The originally-approved threshold was ≤ 2 words. The whole-branch review
> flagged that `matchWholeWord` on a **space-containing** (2-word) query is unverified in
> Word for Mac and can only *hurt*: a 2-word phrase realistically cannot match mid-word,
> while whole-word would stop `"Data Room"` from matching `"Data Rooms"` (a plural/possessive
> recall regression). A single-word query has no space, so `matchWholeWord` is well-defined
> and reliably honored. Narrowing to single-word fully fixes the observed bug (`"Title"`,
> `"Entity"` are both single-word) with none of that risk.

- **Whole-word only for these trials — no loose fallback.** Falling back to a plain
  literal search after a whole-word miss would re-admit the `"entitled"` match and
  defeat the fix. For a last-resort short anchor, a clean miss is better than a
  silently-wrong comment/redline: `findClauseRangeFromAnchors` still walks the finding's
  remaining anchors, and for navigation a "couldn't locate" is a safe outcome.
- **The wildcard-escape retry is unchanged.** Wildcard mode ignores `matchWholeWord`,
  and the bracketed-needle class it serves (`[__]`, `[Source: …]`) does not overlap the
  short-generic-word class. `searchFirst` keeps its existing two-step (literal, then
  escaped-wildcard) shape; only the `matchWholeWord` flag on the literal run becomes
  conditional.

### Why it is safe for all three callers

`matchWholeWord` only *rejects* matches bounded by word characters on either side —
i.e. mid-word matches, which are always wrong. A legitimate standalone occurrence is
bounded by spaces/punctuation and still matches. So:

- **`goToClause` / `showInDocument`** — strictly more accurate; a short anchor can no
  longer select/comment mid-word.
- **`acceptRedline`** — strictly *safer*: a mid-word target it used to (mis)match and
  write into is now rejected, and its `MATCH_COMPLETENESS_THRESHOLD` (0.85) guard is
  unchanged. No legitimate redline that previously applied is lost, because a correct
  redline target matches at word boundaries.

### New / changed code

1. **`word.ts` — new exported `shouldMatchWholeWord(trial: string): boolean`** (pure):
   normalizes the trial (via `normalizeForSearch`) and returns `true` when it is exactly
   **one** whitespace-separated word. Empty/whitespace and multi-word → `false`.
2. **`word.ts` — `searchFirst`** (changed): its literal `run(trial, …)` passes
   `matchWholeWord: shouldMatchWholeWord(trial)` instead of the implicit `false`. The
   escaped-wildcard retry run is unchanged.

### Edge cases

- **Possessive / hyphenated short anchor** (`"Party"` vs doc `"Party's"`) → whole-word
  may miss; acceptable, because clause-name anchors are headings (`"Term"`,
  `"Confidentiality"`), not possessives, and a miss falls through to the next anchor.
- **1-word long token** (`"Confidentiality"`) → whole-word matches the heading and
  cannot match a longer word (there is none); no behavior change in practice.
- **2+ word trial** (`"Effective Date"`, `"Data Room"`) → `shouldMatchWholeWord` returns
  `false`; keeps tolerant substring matching (still matches `"Data Rooms"`), behavior
  identical to today.

---

## Fix 2 — calmer copy for label-only findings

### Behavior

When the locator returns no range, the failure message becomes honest rather than
alarming. The current shared string
*"Couldn't locate this clause in the document."* is replaced (in the three callers'
`fail(...)` for the no-range case) with wording that states the finding describes a
section rather than quoting it — e.g.:

> **"No exact match — this finding describes a section rather than quoting it, so
> there's no text to jump to."**

The affordance stays clickable (the click simply reports the calm message). No
suppression, no new `Finding` field, no parser change.

### Why not predictive suppression

Hiding the jump/comment for "likely-unlocatable" findings would need a parser-side
heuristic, but the parser has no document access — the predictor would be a fuzzy label
blocklist that risks hiding jumps that *would* have matched (false negatives). The
messaging fix captures the value (no "looks broken" moment) with zero false-negatives
and is model-neutral. Predictive suppression is deferred.

### New / changed code

- **`word.ts`** — update the no-range `fail(...)` message string in `goToClause`,
  `showInDocument`, and `acceptRedline`. (Same replacement text in each; `acceptRedline`
  keeps its separate, more detailed completeness-guard failure message unchanged — only
  the "range was null" message is softened.)

---

## Non-goals / Out of scope

- **`[__]` first-occurrence disambiguation** — a bare recurring blank still matches the
  first one; disambiguating needs surrounding-context matching. Deferred.
- **Predictive suppression of label-only findings** (Fix 2 Approach B) — deferred, see
  above.
- **Any parser / backend / prompt / graph / model change** — this is frontend-only.

## Testing

- **`shouldMatchWholeWord`** — new asserts in
  [`word.test.ts`](../../../clients/word/src/word.test.ts) following the existing
  `pass(cond, label)` pattern: 1-word `true` (incl. whitespace-padded), 2-word `false`,
  3-word `false`, 5-word `false`, empty/whitespace-only `false`.
- **`tsc --noEmit`** clean (`npm run typecheck`).
- **Smoke test (required before merge):** sideload in Word for Mac on the model NDA and
  verify —
  (a) a short-anchor finding (`"Title"`, `"Entity"`) selects the correct heading, **not**
  a mid-word hit inside `"entitled"`;
  (b) a label-only signature finding shows the **calm** message, not the old scary one;
  (c) `acceptRedline` still applies a normal-length redline unchanged (whole-word does
  not block a legitimate target).

## Risks / rollback

- **Risk: low.** Frontend-only, additive. The one behavioral change (short anchors now
  whole-word) is strictly more precise and, for `acceptRedline`, strictly safer — no
  legitimate match is lost. Review outputs are byte-identical (no graph/prompt/model
  surface touched).
- **Rollback:** revert the frontend commit(s); nothing else depends on these changes.
