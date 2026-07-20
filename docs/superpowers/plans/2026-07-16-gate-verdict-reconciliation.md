# Gate-Verdict Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After stale-recall reconciliation drops placeholder finding rows, reconcile the recalled review's `# No Signature Checklist Result` gate section so the chat model stops parroting a stale "unfilled / do not send" verdict — annotating always, and conditionally downgrading the verdict to `PENDING RE-REVIEW` when no substantive blocker survives, **never** asserting a signature go-ahead.

**Architecture:** Two new pure helpers in `skills/legal_research.py`. `_surviving_blocker_count` parses the structured `# Key Findings` table (the source of truth for blockers) and returns the count of surviving Red/Missing-Context rows (or `None` when unparseable). `_reconcile_gate_verdict` locates the gate section, inserts a correction note naming the filled tokens, and — only when the surviving-blocker count is exactly 0 — rewrites the `Overall status:` line. Both are wired into the existing `_reconcile_review_with_doc`, which additionally (a) protects the gate section from its row-drop pass so the gate's `Blocking items:` line is not dropped out from under the reconciler, and (b) drops one stale clause from its top note.

**Tech Stack:** Python 3.12, `re` + `unicodedata` (both already imported in the file), pytest. Offline pure functions — no LLM, no DB, no I/O.

## Global Constraints

- **Backend-only.** Change only `skills/legal_research.py` + `tests/test_stale_recall_reconciliation.py`. No prompt / graph / config / client / Word change. `CHAT_SYSTEM_PROMPT` / `_JSON_RETRY_SYSTEM` untouched.
- **Pure helpers.** `_surviving_blocker_count` and `_reconcile_gate_verdict` do no I/O, no LLM, no DB, no `get_settings()`. Fully unit-testable offline.
- **Imports at top of file only** (project rule #1) — `re` and `unicodedata` are already imported; add no lazy imports.
- **No backwards-compat shim** (project rule #5) — `_reconcile_review_with_doc` / `_load_prior_review_block` keep their signatures; the gate step is internal.
- **Never assert a pass.** The strongest downgrade is `PENDING RE-REVIEW`. The output must **never** contain "ready for signature" or "signature may proceed" (case-insensitive). Asserted in tests.
- **Neutralize only when certain.** Downgrade the verdict only when `_surviving_blocker_count(...) == 0`. `None` (unparseable) or any surviving Red/Missing-Context finding → annotate-only, DO-NOT-SEND untouched. Never delete the `Blocking items:` text.
- **Chat-injection-only.** Stored review + Findings-tab path untouched (they re-derive / are immutable).
- **Never break the turn.** The reconcile call already runs inside `_load_prior_review_block`'s try/except (injects the review unchanged on error, does NOT set `memory_degraded`). Add no new uncaught failure surface.
- **Full suite stays green** (currently 330 backend tests).

---

## File Structure

- `skills/legal_research.py` — **modify.** Add two module-level helpers + their regex/string constants in the "Placeholder reconciliation" region (after `_reconcile_review_with_doc`, before `_load_prior_review_block` at :503). Modify `_reconcile_review_with_doc`'s row-drop loop (gate protection) and tail (call the gate reconciler; simplify the note).
- `tests/test_stale_recall_reconciliation.py` — **modify.** Append gate + blocker-count tests. Existing imports suffice: `import skills.legal_research as lr` and `from skills.legal_research import _reconcile_review_with_doc`. No new imports.

### Notes / refinements from the spec

- **Gate-section protection in the row-drop loop** is a mechanism the spec implies ("leave the `Blocking items:` text in place") but did not spell out as a loop change. Without it, the shipped row-drop pass drops the gate's `Blocking items:` line (it contains a now-filled token) *before* `_reconcile_gate_verdict` runs — orphaning the verdict. Task 2 adds an `in_gate` guard so the gate section is owned solely by `_reconcile_gate_verdict`. This also corrects a latent gate-orphaning interaction in the shipped stale-recall feature. No existing test exercised a gate section, so behavior for all shipped tests is byte-identical (the guard is only ever active inside a `# No Signature Checklist Result` section).

---

### Task 1: `_surviving_blocker_count` — the confidence test

**Files:**
- Modify: `skills/legal_research.py` (insert after `_reconcile_review_with_doc`, which currently ends at :500, before `_load_prior_review_block` at :503)
- Test: `tests/test_stale_recall_reconciliation.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks. Uses stdlib `re` (already imported).
- Produces: `_surviving_blocker_count(review_markdown: str) -> int | None` — count of Key Findings rows rated Red or Missing Context; `None` when the Key Findings table or its rating column can't be located/parsed. Consumed by Task 2's `_reconcile_gate_verdict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stale_recall_reconciliation.py`:

```python
def test_surviving_blocker_count_counts_red_and_missing_context():
    md = (
        "# Key Findings\n"
        "| Issue ID | Clause | Rating | Issue |\n"
        "| --- | --- | --- | --- |\n"
        "| R-1 | Indemnity | Red | broad indemnity |\n"
        "| MC-1 | Signature | Missing Context | block blank |\n"
        "| Y-1 | Term | Yellow | auto-renew |\n"
        "| G-1 | Law | Green | fine |\n"
    )
    assert lr._surviving_blocker_count(md) == 2   # Red + Missing Context only


def test_surviving_blocker_count_accepts_risk_header_and_mc_spellings():
    md = (
        "# Key Findings\n"
        "| ID | Risk | Issue |\n"
        "| -- | -- | -- |\n"
        "| A | missing-context | x |\n"
        "| B | RED | y |\n"
    )
    assert lr._surviving_blocker_count(md) == 2   # "Risk" header + odd MC spelling + case


def test_surviving_blocker_count_none_when_no_key_findings():
    assert lr._surviving_blocker_count("# Review Summary\nAll clear.\n") is None


def test_surviving_blocker_count_none_when_no_rating_column():
    md = (
        "# Key Findings\n"
        "| Issue ID | Clause | Issue |\n"
        "| -- | -- | -- |\n"
        "| A | Indemnity | broad |\n"
    )
    assert lr._surviving_blocker_count(md) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -k surviving_blocker_count -v`
Expected: FAIL — `AttributeError: module 'skills.legal_research' has no attribute '_surviving_blocker_count'`.

- [ ] **Step 3: Write the implementation**

In `skills/legal_research.py`, insert immediately after the end of `_reconcile_review_with_doc` (currently :500) and before `def _load_prior_review_block` (:503):

```python
# Gate-verdict reconciliation --------------------------------------------------
# After placeholder rows are dropped, the "No Signature Checklist Result" gate can
# still cite the now-filled placeholders. _surviving_blocker_count reads the
# structured Key Findings table (the source of truth for blockers, mirroring the
# Word parser's deriveBlockers) to decide whether any substantive blocker remains.
_KEY_FINDINGS_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*key\s+findings\s*$", re.IGNORECASE)
_BLOCKER_RATINGS = {"red", "missing context", "missing-context", "missing_context"}


def _surviving_blocker_count(review_markdown: str) -> int | None:
    """Count Key Findings rows rated Red or Missing Context. Returns None when the
    Key Findings table or its rating column can't be located/parsed — callers treat
    None as 'blockers may remain' (conservative: no gate downgrade). Runs on the
    row-reconciled markdown, so already-dropped placeholder rows never inflate it."""
    lines = review_markdown.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _KEY_FINDINGS_HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return None
    rows: list[str] = []
    for line in lines[start:]:
        if re.match(r"^\s{0,3}#{1,6}\s", line):          # next section heading -> stop
            break
        if line.strip().startswith("|"):
            rows.append(line)
    if not rows:
        return None
    header = [c.strip().lower() for c in rows[0].strip().strip("|").split("|")]
    rating_idx = next((i for i, c in enumerate(header) if c in ("rating", "risk")), None)
    if rating_idx is None:
        return None
    count = 0
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) <= rating_idx:
            continue
        cell = cells[rating_idx].lower()
        if not cell or set(cell) <= {"-", ":", " "}:     # separator row (---, :---:)
            continue
        if cell in _BLOCKER_RATINGS:
            count += 1
    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -k surviving_blocker_count -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full reconciliation file to confirm no regression**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -v`
Expected: PASS (17 passed — 13 existing + 4 new).

- [ ] **Step 6: Commit**

```bash
git add skills/legal_research.py tests/test_stale_recall_reconciliation.py
git commit -m "feat: _surviving_blocker_count — Key Findings blocker count for gate reconciliation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `_reconcile_gate_verdict` + wire into `_reconcile_review_with_doc`

**Files:**
- Modify: `skills/legal_research.py` — add `_reconcile_gate_verdict` + constants (after `_surviving_blocker_count`); modify `_reconcile_review_with_doc`'s row-drop loop and tail.
- Test: `tests/test_stale_recall_reconciliation.py` (append)

**Interfaces:**
- Consumes: `_surviving_blocker_count(review_markdown) -> int | None` (Task 1); `_normalize_for_match(text) -> str` (existing, :396).
- Produces: `_reconcile_gate_verdict(review_markdown: str, dropped_tokens: list[str]) -> str`. Called internally by `_reconcile_review_with_doc`; no external signature change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stale_recall_reconciliation.py`:

```python
# --- _reconcile_gate_verdict (unit) ---

def test_gate_neutralized_when_no_blockers_survive():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| Y-1 | Term | Yellow | auto-renew 3y | Flag | Legal |\n"   # non-blocker survivor
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled; `[Legal Name]` blank\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]", "[Legal Name]"])
    assert "PENDING RE-REVIEW" in out
    assert "Do not send for signature" not in out          # verdict rewritten
    assert "Reconciled:" in out                            # correction note added
    assert "ready for signature" not in out.lower()        # never green-lights
    assert "signature may proceed" not in out.lower()
    assert "Blocking items:" in out                        # blocking text preserved


def test_gate_annotated_only_when_blocker_survives():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| R-1 | Indemnity | Red | broad indemnity | Escalate | Legal |\n"  # surviving blocker
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert "Reconciled:" in out                            # note added
    assert "Do not send for signature" in out             # verdict retained
    assert "PENDING RE-REVIEW" not in out                  # not downgraded


def test_gate_unchanged_when_not_citing_dropped_token():
    review = (
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Effective Date: [__]` blank\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])  # token not in gate
    assert out == review


def test_gate_unchanged_when_no_gate_section():
    review = (
        "# Key Findings\n"
        "| Issue ID | Rating |\n| --- | --- |\n| R-1 | Red |\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert out == review


def test_gate_annotate_when_neutralize_eligible_but_no_status_line():
    review = (
        "# Key Findings\n"
        "| Issue ID | Rating |\n| --- | --- |\n| G-1 | Green |\n"   # 0 blockers -> eligible
        "# No Signature Checklist Result\n"
        "Blocking items: `Signed by: [__]` unfilled\n"             # no 'Overall status:' line
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert "Reconciled:" in out                            # note inserted
    assert "PENDING RE-REVIEW" not in out                  # nothing to downgrade -> annotate


def test_gate_no_downgrade_when_key_findings_absent():
    review = (
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert "Reconciled:" in out
    assert "Do not send for signature" in out             # None count -> conservative
    assert "PENDING RE-REVIEW" not in out


def test_gate_empty_dropped_tokens_byte_identical():
    review = "# No Signature Checklist Result\nOverall status: Do not send for signature\n"
    assert lr._reconcile_gate_verdict(review, []) == review


# --- _reconcile_review_with_doc (end-to-end) ---

def test_gate_blocking_items_line_survives_row_drop():
    review = (
        "# Red and Missing Context Items\n"
        "| MC-1 | Signature | `Signed by: [__]` unfilled | Missing Context |\n"
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, dropped = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out                                       # finding row dropped
    assert "Blocking items: `Signed by: [__]`" in out             # gate line NOT row-dropped
    assert dropped == ["Signed by: [__]"]


def test_reconcile_end_to_end_neutralizes_gate():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| MC-1 | Signature | `Signed by: [__]` unfilled | Missing Context | Fill | Legal |\n"
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, dropped = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out                       # Key Findings placeholder row dropped
    assert "Auto-reconciled" in out                # top note present
    assert "PENDING RE-REVIEW" in out              # gate neutralized (0 blockers survive)
    assert "ready for signature" not in out.lower()
    assert "signature may proceed" not in out.lower()


def test_reconcile_end_to_end_annotates_when_blocker_survives():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| R-1 | Indemnity | Broad indemnity | Red | Escalate | Legal |\n"
        "| MC-1 | Signature | `Signed by: [__]` unfilled | Missing Context | Fill | Legal |\n"
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, dropped = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out                        # placeholder finding dropped
    assert "R-1" in out                             # substantive Red finding kept
    assert "Do not send for signature" in out       # verdict retained (blocker survives)
    assert "PENDING RE-REVIEW" not in out
    assert "Reconciled:" in out                      # gate annotated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -k "gate or end_to_end" -v`
Expected: FAIL — `AttributeError: ... has no attribute '_reconcile_gate_verdict'` (and the end-to-end tests fail because the gate is not yet reconciled / the gate line is row-dropped).

- [ ] **Step 3: Add `_reconcile_gate_verdict` + constants**

In `skills/legal_research.py`, insert immediately after `_surviving_blocker_count` (before `def _load_prior_review_block`):

```python
_GATE_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*no[\s-]*signature\s+checklist", re.IGNORECASE)
_OVERALL_STATUS_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?\s*overall status\s*:?", re.IGNORECASE)
_GATE_NEUTRAL_STATUS = (
    "Overall status: PENDING RE-REVIEW — the placeholder blockers recorded here were "
    "filled after this review and no other blockers remain; re-review to confirm "
    "signature readiness. (Do not treat as approved for signature.)"
)


def _reconcile_gate_verdict(review_markdown: str, dropped_tokens: list[str]) -> str:
    """Reconcile the No-Signature gate section after placeholder rows were dropped.

    Gate absent, or citing none of the dropped tokens -> unchanged. Otherwise insert
    a correction note naming the filled tokens (annotate), and — ONLY when zero Key
    Findings rows survive rated Red/Missing Context — rewrite 'Overall status:' to
    PENDING RE-REVIEW (neutralize). Never writes a signature go-ahead; never deletes
    the Blocking items text."""
    if not dropped_tokens:
        return review_markdown
    lines = review_markdown.splitlines()
    gate_start = next(
        (i for i, ln in enumerate(lines) if _GATE_HEADING_RE.match(ln)), None
    )
    if gate_start is None:
        return review_markdown
    gate_end = len(lines)
    for j in range(gate_start + 1, len(lines)):
        if re.match(r"^\s{0,3}#{1,6}\s", lines[j]):      # next section heading
            gate_end = j
            break
    norm_body = _normalize_for_match("\n".join(lines[gate_start + 1:gate_end]))
    cited = [t for t in dropped_tokens if _normalize_for_match(t) in norm_body]
    if not cited:
        return review_markdown

    neutralize = _surviving_blocker_count(review_markdown) == 0
    note = (
        "> **Reconciled:** placeholder blocker(s) cited in this gate — "
        + ", ".join("`" + t + "`" for t in cited)
        + " — were filled in the current document after this review; treat them as "
        "resolved. The current document governs the gate."
    )
    section = lines[gate_start:gate_end]
    if neutralize:
        for k in range(1, len(section)):
            if _OVERALL_STATUS_RE.match(section[k]):
                section[k] = _GATE_NEUTRAL_STATUS
                break
    section = [section[0], note] + section[1:]
    return "\n".join(lines[:gate_start] + section + lines[gate_end:])
```

- [ ] **Step 4: Protect the gate section in the row-drop loop**

In `_reconcile_review_with_doc`, find the loop preamble (currently :459-465):

```python
    kept: list[str] = []
    dropped_tokens: list[str] = []
    seen_dropped: set[str] = set()
    for line in review_markdown.splitlines():
        if line.lstrip().startswith("#"):        # never drop section headings
            kept.append(line)
            continue
        norm_line = _normalize_for_match(line)
```

Replace it with (adds `in_gate` tracking + a gate-section skip):

```python
    kept: list[str] = []
    dropped_tokens: list[str] = []
    seen_dropped: set[str] = set()
    in_gate = False
    for line in review_markdown.splitlines():
        if line.lstrip().startswith("#"):        # never drop section headings
            in_gate = bool(_GATE_HEADING_RE.match(line))  # gate owned by _reconcile_gate_verdict
            kept.append(line)
            continue
        if in_gate:                              # gate reconciled separately; never row-drop it
            kept.append(line)
            continue
        norm_line = _normalize_for_match(line)
```

- [ ] **Step 5: Wire the gate reconciler + simplify the top note**

In `_reconcile_review_with_doc`, find the tail (currently :490-500):

```python
    if not dropped_tokens:                        # filled candidates but no row removed
        return review_markdown, []                # -> no change, no over-claiming note

    note = (
        f"> **Auto-reconciled:** {len(dropped_tokens)} placeholder(s) flagged in the "
        f"prior review were filled in the document afterward and have been removed from "
        f"the recalled findings: {', '.join('`' + c + '`' for c in dropped_tokens)}. If "
        f"these were the only signature-block blockers, re-review to confirm the "
        f"No-Signature gate now passes.\n\n"
    )
    return note + "\n".join(kept), dropped_tokens
```

Replace with (call the gate reconciler; drop the trailing "If these were the only… gate now passes." clause, now handled precisely in the gate section):

```python
    if not dropped_tokens:                        # filled candidates but no row removed
        return review_markdown, []                # -> no change, no over-claiming note

    body = _reconcile_gate_verdict("\n".join(kept), dropped_tokens)
    note = (
        f"> **Auto-reconciled:** {len(dropped_tokens)} placeholder(s) flagged in the "
        f"prior review were filled in the document afterward and have been removed from "
        f"the recalled findings: {', '.join('`' + c + '`' for c in dropped_tokens)}.\n\n"
    )
    return note + body, dropped_tokens
```

- [ ] **Step 6: Run the gate + end-to-end tests to verify they pass**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -k "gate or end_to_end" -v`
Expected: PASS (10 passed).

- [ ] **Step 7: Run the whole reconciliation file (no regression on shipped tests)**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -v`
Expected: PASS (27 passed — 13 existing + 4 Task-1 + 10 Task-2).

- [ ] **Step 8: Run the full backend suite**

Run: `uv run pytest tests/ -q`
Expected: PASS — 344 passed (330 prior + 14 new). (One pre-existing LangGraph deprecation warning is expected and unrelated.)

- [ ] **Step 9: Commit**

```bash
git add skills/legal_research.py tests/test_stale_recall_reconciliation.py
git commit -m "feat: gate-verdict reconciliation on chat recall path

After placeholder rows are dropped, reconcile the No-Signature gate: annotate the
filled tokens, and downgrade the verdict to PENDING RE-REVIEW only when zero
Red/Missing-Context findings survive. Protect the gate section from the row-drop
pass so its Blocking items line is not orphaned. Never asserts a signature
go-ahead. Chat-injection-only, pure, model-neutral.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- `_surviving_blocker_count` (spec "Component: `_surviving_blocker_count`") → Task 1. ✅ (Red/MC count, Rating/Risk header, MC spellings, None on missing table/column.)
- `_reconcile_gate_verdict` algorithm steps 1-7 (spec) → Task 2 Step 3: empty tokens → unchanged; locate gate + section end; cited-token gate check; `neutralize = count == 0`; always-annotate note; conditional status rewrite; degrade-to-annotate when no status line; never green-lights. ✅
- Call-site change + gate protection + note simplification (spec "Call-site change") → Task 2 Steps 4-5. ✅
- Testing list (spec items 1-12) → mapped: neutralize (1) `test_gate_neutralized_when_no_blockers_survive` + `test_reconcile_end_to_end_neutralizes_gate`; annotate-only surviving blocker (2) `test_gate_annotated_only_when_blocker_survives` + `test_reconcile_end_to_end_annotates_when_blocker_survives`; non-dropped token (3) `test_gate_unchanged_when_not_citing_dropped_token`; no gate (4) `test_gate_unchanged_when_no_gate_section`; no status line (5) `test_gate_annotate_when_neutralize_eligible_but_no_status_line`; unparseable KF (6) `test_gate_no_downgrade_when_key_findings_absent`; empty tokens (7) `test_gate_empty_dropped_tokens_byte_identical`; never green-lights (8) folded into neutralize asserts; blocker-count vocab (9) `test_surviving_blocker_count_counts_red_and_missing_context` + `..._accepts_risk_header_and_mc_spellings`; None cases (10) `..._none_when_no_key_findings` + `..._none_when_no_rating_column`; end-to-end (11,12) the two `test_reconcile_end_to_end_*`. Plus `test_gate_blocking_items_line_survives_row_drop` covering the gate-protection refinement. ✅ No gaps.
- Non-goals (no prompt/graph/config/client; no pass; chat-only; no re-review nudge) → Global Constraints. ✅

**2. Placeholder scan:** No "TBD"/"TODO"/"handle edge cases"/"similar to". Every code step shows complete code; every run step shows exact command + expected output. ✅

**3. Type consistency:** `_surviving_blocker_count(str) -> int | None` defined in Task 1, consumed in Task 2 via `_surviving_blocker_count(review_markdown) == 0` (None == 0 is False → annotate — correct). `_reconcile_gate_verdict(str, list[str]) -> str` defined Task 2, called as `_reconcile_gate_verdict("\n".join(kept), dropped_tokens)` — types match. `_normalize_for_match(str) -> str` and `_GATE_HEADING_RE` reused consistently across the loop and the reconciler. ✅

---

## Post-implementation notes (plan ≡ code)

The shipped code differs from the plan's literal blocks in three reviewed, intentional ways — recorded here so the plan matches the branch:

1. **Task 2 end-to-end fixtures (column swap fix).** In `test_reconcile_end_to_end_neutralizes_gate` and `test_reconcile_end_to_end_annotates_when_blocker_survives`, the plan's Key Findings rows had the `Issue` and `Rating` cells transposed (e.g. `| R-1 | Indemnity | Broad indemnity | Red | … |`), so the `Rating` column held prose and the row wasn't recognized as a blocker — the annotate-only test would have failed against correct code. The implementer corrected the rows to header order (`Rating` = `Red` / `Missing Context`); the assertions were unchanged. Verified by the Task-2 reviewer (empirically reproduced the failure with the original fixture).

2. **Final-review fix — rating-cell emphasis strip (commit `ac40c42`).** `_surviving_blocker_count` now strips `*` and backtick from the rating cell before the blocker-set check (`cells[rating_idx].lower().replace("*","").replace("`","").strip()`), so a bolded `**Red**` is still counted — without it a live blocker was missed and the gate wrongly neutralized (the dangerous direction). `_` is deliberately **not** stripped (would break the `missing_context` spelling). Added `test_surviving_blocker_count_strips_rating_emphasis` + `test_end_to_end_bold_rating_blocker_prevents_neutralize`.

3. **Final-review fix — shared `_HEADING_RE` (commit `ac40c42`).** Generic heading/section-boundary detection is unified on one module constant `_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")`, used by the row-drop loop (was `line.lstrip().startswith("#")`), `_surviving_blocker_count`, and `_reconcile_gate_verdict`. The specific `_KEY_FINDINGS_HEADING_RE` / `_GATE_HEADING_RE` matchers are unchanged.

4. **Final-review re-review follow-up — underscore-italic emphasis (commit in the fix range).** The opus re-review found `_Red_` (underscore-italic) ratings still slipped past the `*`/backtick strip. Completed with `.strip("_")` (surrounding underscores only, so internal `missing_context` survives) + `test_surviving_blocker_count_strips_underscore_italic_rating`. Closes the rating-markup class in the dangerous direction.

Final suite: **347** backend tests (330 prior + 14 tasks + 3 fix-wave). Two documented residual undercount risks + the "never-green-lights applies to generated text" invariant caveat live in the spec's Risks section.
