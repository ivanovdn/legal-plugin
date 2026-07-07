# Word add-in quick UX wins (go-to-clause + findings filters) — design

> **Status:** approved design, ready for an implementation plan.
> **Scope:** two frontend-only quick wins in the Word add-in ("Bucket A"), shippable as
> one small PR. **No backend, graph, prompt, or LLM change** — so zero risk to review quality.
> **Source ideas:** `docs/legal-agent-upgrade-research.md` — competitor-landscape #1
> (go-to-clause jump) and #8 (findings filter/sort chips).

## Problem

The Findings tab presents a flat, static list of clause findings. Two friction points:

1. **No cheap navigation.** The only way to locate a finding's clause in the document is the
   **"Show in document"** button, which does `range.select()` **and** `range.insertComment(...)`
   ([`word.ts` `showInDocument`](../../../clients/word/src/word.ts), consumed by
   [`FindingCard.tsx`](../../../clients/word/src/components/FindingCard.tsx)). So *every* attempt
   to merely look at a clause **mutates the document** with a Word comment. Browsing 20 findings
   litters the doc with 20 comments. Navigation and commenting are fused when they should be
   separate intents.
2. **No triage.** On a long review the findings list can't be filtered or sorted — a reviewer
   who only wants the RED blockers, or only their own owned items, has to scroll and eyeball.

Both are pure presentation gaps. Leading tools (Spellbook, Legora) make findings **click-to-jump**
and **filterable** as table stakes.

## Guiding principle

Frontend-only, additive, and **non-destructive by default**. Navigation must never mutate the
document; the one existing mutation (the audit-trail comment) is preserved but becomes explicit
and opt-in. Extract any non-trivial logic into a **pure, unit-testable function** (Office.js UI
can only be smoke-tested), per the repo rule "*touching the Word add-in? smoke-test by
sideloading — `tsc --noEmit` is not enough.*"

---

## Feature 1 — Click-to-jump navigation

### Behavior

- **Clicking a finding's title** (`card-title` in `FindingCard`) scrolls Word to the clause and
  **selects** it — nothing else. No comment, no mutation. This is the fast, default interaction.
- The existing **"Show in document"** button is **renamed "Comment in doc"** and keeps its current
  behavior exactly (`showInDocument` = select + `insertComment`) — the audit-trail export, now
  explicit and opt-in.
- The same click-to-jump is extended to the **chat tab's proposed-edit cards**
  ([`EditProposalCard.tsx`](../../../clients/word/src/components/EditProposalCard.tsx)) so a
  reviewer can jump to the target text of a proposed edit before applying it — consistent behavior
  across both surfaces.

### New / changed components

1. **`word.ts` — new `goToClause(target: string | string[]): Promise<Result<string>>`** (new).
   Mirrors `showInDocument` but **select-only** — reuses `toAnchors` + `findClauseRangeFromAnchors`,
   calls `range.select()`, and **omits `insertComment`**. Returns `ok(range.text)` or a
   `fail(...)` when the clause can't be located. `showInDocument` is left untouched.
   - **Partial-match tolerance is acceptable here.** `findClauseRangeFromAnchors` falls back to
     shorter prefixes; for *navigation* a slightly-short selection is fine (no mutation), unlike
     `acceptRedline`, which guards at `MATCH_COMPLETENESS_THRESHOLD` (0.85) because it *writes*.
     No completeness guard is added to `goToClause`.

2. **`FindingCard.tsx`** (changed):
   - `card-title` becomes a clickable element (`role="button"`, `tabIndex=0`, Enter/Space handler,
     `cursor: pointer`) wired to a new `onJump` handler calling `goToClause(anchors)`.
   - A lightweight per-card `jump` state (`idle | running | error`) renders a small inline
     "Couldn't locate this clause" message on failure. **Success is silent** (the Word selection
     *is* the feedback) — no status line, no "Located ✓" clutter.
   - The `onShow` button’s label changes `"Show in document"` → `"Comment in doc"` and
     `"Locating…"` → `"Commenting…"`; its "done" message stays `"Commented ✓"`. Logic unchanged.
   - `anchors` derivation is unchanged (`finding.anchors.length > 0 ? finding.anchors :
     [finding.currentText]`).

3. **`EditProposalCard.tsx`** (changed): add a small **"Go to"** affordance that calls
   `goToClause` on the edit's target text (the same field the apply path locates), reusing the
   card's existing target string. Select-only; no mutation.

4. **`styles.css`** (changed): `.card-title` clickable affordance (pointer cursor, hover/focus
   ring using the existing Office `#0078D4` accent already in the sheet).

### Edge cases

- **Clause not found** → inline non-blocking error on that card; the rest of the list is
  unaffected.
- **Click target isolation** — only the title is clickable; existing buttons keep their own
  handlers. No whole-card click (avoids event conflicts with buttons and text selection/copy).
- **Word unavailable** (add-in opened outside Word) → `goToClause` returns the existing
  `isWordAvailable()` failure message.

---

## Feature 2 — Findings filter / sort bar

### Behavior

A thin control bar above the findings list ([`FindingsTab.tsx`](../../../clients/word/src/components/FindingsTab.tsx),
`Results`), operating purely on the already-parsed `result.findings`:

- **Severity toggles** — RED / MISSING / YELLOW / GREEN chips; toggle any subset (default: all on).
- **"Blockers only"** switch — shorthand for severity ∈ {RED, MISSING_CONTEXT}.
- **Owner** dropdown — `All` + the distinct `finding.owner` values present (empty owners grouped
  as `Unassigned`); hidden when no finding has an owner.
- **Sort by** — `Severity` (default, via the existing `RISK_ORDER` map in `parser.ts`) or
  `Clause name` (A–Z).
- A **"showing X of Y"** count so an active filter is never mistaken for "no findings."

The **existing summary chips** (`{counts.red} RED …`), the **`GateBanner`**, the **`BlockerList`**
card, and **Business Questions** are **unaffected** — they summarize the whole review, not the
filtered subset. Only the per-finding `FindingCard` list is filtered/sorted.

### New / changed components

1. **`parser.ts` (or new `findingFilters.ts`) — pure `applyFindingFilters(findings, filters)`**
   (new, exported): takes `Finding[]` + a `FindingFilters` object
   (`{ severities: Set<Risk>; owner: string | "all"; sortBy: "severity" | "clause" }`) and returns
   the filtered+sorted array. **Pure and unit-tested.** ("Blockers only" is expressed by the caller
   as `severities = {RED, MISSING_CONTEXT}`, so the pure function stays simple.)

2. **`FindingsTab.tsx` `Results`** (changed): holds filter state (`useState`), renders a new
   `<FilterBar>`, derives owner options from `findings`, and maps
   `applyFindingFilters(findings, filters)` instead of raw `findings`. On empty result →
   "No findings match the current filters."
   - **Reset on new review:** a `useEffect` keyed on the review identity resets filters to default
     when `setResult` delivers a new parse, so a stale filter can't hide fresh findings.

3. **`FilterBar` component** (new, small — inline in `FindingsTab.tsx` or its own file): renders the
   chips / switch / dropdown / sort control; controlled by the parent's filter state.

4. **`styles.css`** (changed): filter-bar layout + chip active/inactive states (reuse existing
   `.badge` color classes for the severity chips).

### Edge cases

- **No findings at all** → the existing "No clause findings parsed…" message still shows; the
  filter bar renders only when `findings.length > 0`.
- **All severities off** → treated as "none match" → empty-state message (not "all"); the count
  reads `showing 0 of Y`.
- **Owner list churn** across re-reviews handled by the reset-on-new-review effect.

---

## Non-goals / Out of scope

- **Filter by clause *type*** — the `Finding` model has no `clause_type` field (only `clause`
  name); adding it is a backend/parser change, deferred.
- **Bulk actions** ("Apply all RED"), **stale-findings banner** — landscape #5/#12, separate work.
- **Structured JSON findings** (#15) and anything that changes what the model outputs.
- **Persisting filter preferences** across pane reopen — session-local only for now.

## Testing

- **`tsc --noEmit`** clean (necessary, not sufficient).
- **Unit tests** for the pure `applyFindingFilters` (new asserts in `parser.test.ts` or a new
  `findingFilters.test.ts`): severity subset filtering, blockers-only, owner filter, both sort
  orders, empty-result, all-off. Follows the existing frontend-assert pattern
  (`parser.test.ts`, `parseEditBlocks.test.ts`).
- **`goToClause`** is a thin Office.js wrapper (like `showInDocument`) — covered by **smoke test**,
  not unit test.
- **Smoke test (required):** sideload in Word for Mac and verify on a real review —
  (a) click a finding title → Word scrolls + selects the clause, **no comment added**;
  (b) "Comment in doc" still inserts the comment; (c) severity/owner/blockers-only filters and both
  sorts behave; (d) "showing X of Y" is correct; (e) chat-tab edit card "Go to" selects the target.

## Risks / rollback

- **Risk: near-zero.** Frontend-only, additive; the one behavior change (button rename +
  navigation no longer auto-comments) is intentional and preserves the comment as an explicit
  action. No graph/prompt/model surface touched → review outputs are byte-identical.
- **Rollback:** revert the frontend commit(s); nothing else depends on these changes.
