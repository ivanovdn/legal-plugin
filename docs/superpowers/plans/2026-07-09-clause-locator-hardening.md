# Clause-Locator Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Word add-in clause locator from matching short anchors mid-word, and replace its scary "couldn't locate" message with honest copy for findings that describe (rather than quote) a section.

**Architecture:** Two frontend-only changes, both in `clients/word/src/word.ts`. Fix 1 adds a pure, exported `shouldMatchWholeWord(trial)` and threads Word's native `matchWholeWord` search option into `searchFirst` for short (≤ 2-word) trials only. Fix 2 introduces a shared `NO_MATCH_MESSAGE` constant and swaps it into the three callers' null-range `fail(...)`. No parser, component, backend, graph, prompt, or LLM change.

**Tech Stack:** TypeScript, Office.js (`Word.SearchOptions`), React (untouched here). Tests are standalone `.ts` files run with `npx tsx`; typecheck via `tsc --noEmit`.

## Global Constraints

- **Frontend-only.** Touch only `clients/word/src/word.ts`, `clients/word/src/word.test.ts`, and (Task 3) `docs/wiki.md`. No backend/graph/prompt/parser/component change — review outputs must stay byte-identical.
- **All imports at top of file** (repo hard rule) — no lazy imports inside functions.
- **Never emit `.js` into `clients/word/src/`** — tsconfig is `noEmit:true`; run typecheck only.
- **`shouldMatchWholeWord` is pure and exported** (unit-tested). The Office.js wiring around it is smoke-tested, not unit-tested.
- **Whole-word is whole-word-ONLY for short trials** — no plain-literal fallback after a whole-word miss (that would re-admit the `"entitled"` bug). The longer bridging prefixes and multi-paragraph clauses keep today's behavior.
- **"Short" = normalized trial with ≤ 2 whitespace-separated words**, and > 0 words (empty/whitespace → not short → `false`).
- **All three callers share the softened message** (`goToClause`, `showInDocument`, `acceptRedline`) for the null-range case. `acceptRedline`'s *separate* completeness-guard message (the `MATCH_COMPLETENESS_THRESHOLD` failure) stays exactly as-is — only the `if (!range)` message is replaced.
- Commands run from `clients/word/`.

---

### Task 1: Whole-word matching for short anchors

**Files:**
- Modify: `clients/word/src/word.ts` (add `shouldMatchWholeWord`; thread `matchWholeWord` into `searchFirst`)
- Test: `clients/word/src/word.test.ts` (add asserts + import)

**Interfaces:**
- Consumes: `normalizeForSearch` from `./normalize` (already imported in `word.ts`); `WORD_WILDCARD_META`, `escapeWordWildcards` (already in `word.ts`).
- Produces: `export function shouldMatchWholeWord(trial: string): boolean` — used by `searchFirst`; unit-tested from `word.test.ts`.

- [ ] **Step 1: Write the failing tests**

Edit `clients/word/src/word.test.ts`. Change the import line to add `shouldMatchWholeWord`:

```ts
import { escapeWordWildcards, isAmbiguousBlankPlaceholder, shouldMatchWholeWord } from "./word";
```

Append these asserts to the end of the file:

```ts
// --- shouldMatchWholeWord: short clause-name anchors search whole-word-only ---
// so "Title" can't match mid-word inside "entitled". Short = <= 2 words.
pass(shouldMatchWholeWord("Title"), "wholeword: 1-word anchor -> true");
pass(shouldMatchWholeWord("Effective Date"), "wholeword: 2-word anchor -> true");
pass(shouldMatchWholeWord("Execution   Block"), "wholeword: collapses whitespace -> 2 words true");
pass(!shouldMatchWholeWord("Limitation of Liability"), "wholeword: 3-word phrase -> false");
pass(!shouldMatchWholeWord("The Receiving Party shall not"), "wholeword: 5-word phrase -> false");
pass(!shouldMatchWholeWord(""), "wholeword: empty -> false");
pass(!shouldMatchWholeWord("   "), "wholeword: whitespace-only -> false");
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd clients/word && npx tsx src/word.test.ts`
Expected: the run **errors** with `shouldMatchWholeWord is not a function` (the symbol isn't exported yet), aborting before the new asserts print. That is the expected pre-implementation failure.

- [ ] **Step 3: Add the pure `shouldMatchWholeWord` function**

In `clients/word/src/word.ts`, add this directly after `escapeWordWildcards` (just before `isAmbiguousBlankPlaceholder`, around line 62). `normalizeForSearch` is already imported at the top of the file:

```ts
// Short clause-name anchors ("Title", "Entity", "Effective Date") arrive via
// searchCandidates' last-resort fallback. body.search is NOT whole-word by
// default, so "Title" matches inside "en-title-d" and the jump/comment/redline
// lands mid-word in the wrong place. For these short, last-resort anchors we
// search whole-word-ONLY (precision over recall): a clean miss falls through to
// the finding's next anchor — better than a silently-wrong hit. "Short" = a
// normalized trial of <= 2 whitespace-separated words. Exported for unit testing.
export function shouldMatchWholeWord(trial: string): boolean {
  const words = normalizeForSearch(trial).split(/\s+/).filter(Boolean);
  return words.length > 0 && words.length <= 2;
}
```

- [ ] **Step 4: Thread `matchWholeWord` into `searchFirst`**

In `clients/word/src/word.ts`, `searchFirst` (around lines 188-222). Add a `matchWholeWord` parameter to the inner `run` helper, pass it into `body.search`, and set it from `shouldMatchWholeWord(trial)` on the literal run (and `false` on the escaped-wildcard retry — wildcard mode ignores whole-word, and the bracketed-needle class it serves is not the short-generic-word class).

Replace the whole function body:

```ts
async function searchFirst(
  context: Word.RequestContext,
  trial: string,
): Promise<Word.Range | null> {
  const run = async (query: string, matchWildcards: boolean, matchWholeWord: boolean) => {
    try {
      const results = context.document.body.search(query, {
        matchCase: false,
        matchWildcards,
        matchWholeWord,
      });
      results.load("items");
      await context.sync();
      return results.items.length > 0 ? results.items[0] : null;
    } catch (e) {
      // body.search throws on malformed candidates (>255 chars, certain
      // wildcard-char combinations the API rejects post-queue, etc.). Treat as
      // "no match" so the search loop moves on rather than aborting the whole
      // find/delete/redline flow. Logged at warn for DevTools visibility.
      console.warn("[word.ts] body.search rejected candidate:", query.slice(0, 80), e);
      return null;
    }
  };

  // Short clause-name anchors ("Title") search whole-word-only so they can't
  // match mid-word ("entitled"). Longer trials keep today's substring behavior.
  const literal = await run(trial, false, shouldMatchWholeWord(trial));
  if (literal) return literal;

  // Word for Mac mis-reads [](){}<>?* etc. as wildcards even in literal mode, so
  // a needle with brackets (a blank "[__]" field, a "[Source: …]" tag) silently
  // misses. Retry in wildcard mode with the metacharacters escaped, which makes
  // body.search match the literal characters. Whole-word is irrelevant/ignored
  // in wildcard mode, so pass false.
  if (WORD_WILDCARD_META.test(trial)) {
    return run(escapeWordWildcards(trial), true, false);
  }
  return null;
}
```

- [ ] **Step 5: Run the unit tests to verify they pass**

Run: `cd clients/word && npx tsx src/word.test.ts`
Expected: all asserts print `PASS:` (the original `escapeWordWildcards` / `isAmbiguousBlankPlaceholder` lines plus the 7 new `wholeword:` lines). Zero `FAIL:`.

- [ ] **Step 6: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: exits 0, no output. (`matchWholeWord` is a standard `Word.SearchOptions` property — no type error.)

- [ ] **Step 7: Commit**

```bash
git add clients/word/src/word.ts clients/word/src/word.test.ts
git commit -m "fix(word): whole-word matching for short clause anchors

Short (<=2-word) anchors like 'Title' matched mid-word ('entitled') because
body.search is not whole-word by default. Add pure shouldMatchWholeWord() and
run whole-word-only for short trials in searchFirst — precision over recall for
last-resort anchors. Shared by goToClause/showInDocument/acceptRedline; the
85% completeness guard on acceptRedline is unchanged and a mid-word target it
used to write into is now rejected (strictly safer).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Calmer message for label-only findings

**Files:**
- Modify: `clients/word/src/word.ts` (add `NO_MATCH_MESSAGE`; swap it into 3 `fail(...)` sites)

**Interfaces:**
- Consumes: nothing new.
- Produces: module-level `const NO_MATCH_MESSAGE` (internal; not exported).

- [ ] **Step 1: Add the shared message constant**

In `clients/word/src/word.ts`, add a module-level constant near the other search
constants (e.g. just after `SEARCH_MAX_LEN`, around line 76):

```ts
// Shown when the locator finds NO range at all (distinct from acceptRedline's
// too-short-partial-match message). Many findings — signature/execution blocks,
// Missing-Context items — describe a section rather than quoting it verbatim, so
// there is genuinely nothing in the document to locate. Say that plainly instead
// of the old blunt not-found wording, which read like a bug report.
const NO_MATCH_MESSAGE =
  "No exact match in the document — this finding describes a section rather than quoting it verbatim, so there's nothing to locate.";
```

- [ ] **Step 2: Swap the constant into the three null-range failures**

In `clients/word/src/word.ts`, replace the message in each of the three callers'
`if (!range)` guard. There are exactly three occurrences of the literal
`fail("Couldn't locate this clause in the document.")`:

1. `showInDocument` (around line 359)
2. `goToClause` (around line 385)
3. `acceptRedline` (around line 416)

In each, change:

```ts
      if (!range) return fail("Couldn't locate this clause in the document.");
```

to:

```ts
      if (!range) return fail(NO_MATCH_MESSAGE);
```

Do **not** touch `acceptRedline`'s later completeness-guard `fail(...)` (the
`"Couldn't find the exact target text…"` message gated by
`MATCH_COMPLETENESS_THRESHOLD`) — that describes a different failure and stays as-is.

- [ ] **Step 3: Verify no stale copy remains**

Run: `cd clients/word && grep -rn "Couldn't locate this clause" src/`
Expected: **no output** (all three occurrences replaced).

Run: `cd clients/word && grep -rn "NO_MATCH_MESSAGE" src/word.ts`
Expected: 4 lines — the `const` definition plus three `fail(NO_MATCH_MESSAGE)` uses.

- [ ] **Step 4: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: exits 0, no output.

- [ ] **Step 5: Run the unit tests (no regression)**

Run: `cd clients/word && npx tsx src/word.test.ts`
Expected: all `PASS:`, zero `FAIL:` (unchanged from Task 1 — this task adds no asserts because the edited code is Office.js wrapper messaging, covered by the smoke test).

- [ ] **Step 6: Commit**

```bash
git add clients/word/src/word.ts
git commit -m "fix(word): honest 'no match' copy for described-not-quoted findings

Findings that describe a section (signature/execution blocks, Missing-Context)
have no verbatim doc text to locate. Replace the alarming 'Couldn't locate this
clause' with a shared NO_MATCH_MESSAGE that says the finding describes rather
than quotes. Navigation + redline null-range case only; acceptRedline's separate
completeness-guard message is unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Update wiki (shipped + follow-up resolved)

**Files:**
- Modify: `docs/wiki.md` (add a "Shipped Since Last Update" row; mark the "Clause-locator hardening for placeholder / short anchors" follow-up resolved)

**Interfaces:** none (docs only).

- [ ] **Step 1: Read the two target regions**

Run: `grep -n "Clause-locator hardening for placeholder\|Shipped Since Last Update\|Last updated:" docs/wiki.md`
Read the "Shipped Since Last Update" table region and the follow-up row so the edits match the existing table format exactly.

- [ ] **Step 2: Add a Shipped row**

In the "Shipped Since Last Update" table, add a row describing this change. Match the existing column layout of that table. Content to convey (adapt to the table's columns):

> **Word add-in: clause-locator hardening.** `searchFirst` now searches **whole-word-only** for short (≤ 2-word) anchors via new pure `shouldMatchWholeWord()` (`word.ts`, unit-tested) — `"Title"` no longer matches inside `"entitled"`. Shared by `goToClause`/`showInDocument`/`acceptRedline`; strictly safer for redline (85% guard unchanged, mid-word targets now rejected). Null-range failures now show a calm `NO_MATCH_MESSAGE` (finding describes rather than quotes a section) instead of "Couldn't locate this clause." The wildcard-retry the follow-up mentioned was already live in `searchFirst`. Frontend-only; +7 frontend asserts. Spec/plan in `docs/superpowers/`. **Smoke-confirm pending** (appended after sideload).

- [ ] **Step 3: Mark the follow-up resolved**

Find the follow-up row `Clause-locator hardening for placeholder / short anchors`. Mark it resolved in the manner the wiki already uses for closed follow-ups (e.g. strike-through, a "Resolved (2026-07-09)" note, or moving it out of the open list — match whatever convention that file already uses). Note the two carve-outs that remain deferred: `[__]` first-occurrence disambiguation and predictive suppression of label-only findings.

- [ ] **Step 4: Update the "Last updated" line / test count if present**

If the wiki header carries a date or an assert count (e.g. "133 frontend asserts"), bump the date to 2026-07-09 and the assert count by +7 (→ 140). If no such line exists, skip.

- [ ] **Step 5: Commit**

```bash
git add docs/wiki.md
git commit -m "docs(wiki): clause-locator hardening shipped; follow-up resolved

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Fix 1 (whole-word for short anchors) → Task 1. ✅
- Fix 2 (calmer null-range message, all three callers, `acceptRedline` completeness message untouched) → Task 2. ✅
- Pure/exported/unit-tested `shouldMatchWholeWord`; ≤ 2-word threshold; whole-word-only, no loose fallback → Task 1 Steps 3-5, Global Constraints. ✅
- Out-of-scope items (`[__]` disambiguation, predictive suppression) → noted in Task 3 Step 3; no task implements them. ✅
- Wiki update on ship (CLAUDE.md rule) → Task 3. ✅

**Placeholder scan:** No TBD/TODO. Every code step shows the exact code; every run step shows the exact command and expected output. ✅

**Type consistency:** `shouldMatchWholeWord(trial: string): boolean` is defined in Task 1 Step 3 and consumed with that signature in Step 4 and the tests in Step 1. `NO_MATCH_MESSAGE: string` defined in Task 2 Step 1, used in Step 2. `matchWholeWord` is a standard `Word.SearchOptions` boolean. ✅

**Task independence:** A reviewer could accept the matching fix (Task 1) while rejecting the message wording (Task 2) — the split is justified. Task 3 is docs-only.
