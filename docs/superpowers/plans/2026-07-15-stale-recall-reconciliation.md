# Stale-Recall Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the chat review-recall path, deterministically drop placeholder findings from the recalled review when the current document proves they were filled after the review — so chat stops reporting already-filled fields as unfilled.

**Architecture:** One new pure helper `_reconcile_review_with_doc` in `skills/legal_research.py` extracts the placeholder tokens the recalled review quotes, checks each (normalized, whole-quoted-string) against the current document text, and removes the finding lines whose tokens are gone, prepending a factual reconciliation note. `_load_prior_review_block` gains an `uploaded_text` parameter and calls the helper (wrapped in try/except, falling back to the unchanged review). No LLM, no DB in the helper.

**Tech Stack:** Python 3.12, `re` + `unicodedata` (stdlib), pytest. No new dependencies.

## Global Constraints

- **All imports at top of file** — no lazy imports inside functions (CLAUDE.md rule #1).
- **No backwards-compat shims** — change the call site directly (CLAUDE.md rule #5). `_load_prior_review_block` gains a required 2nd param; update its one caller.
- **Fix in code, not prompt** — do NOT change `CHAT_SYSTEM_PROMPT`, `_JSON_RETRY_SYSTEM`, or any prompt text. Reconciliation is entirely code-side and model-neutral.
- **Backend-only** — no change to `graph/`, `config.py`, `clients/word/`, or the playbook bundle.
- **The pure helper is pure** — no I/O, no LLM, no DB, no `get_settings()`. Deterministic and offline-testable.
- **Scope: placeholders only** — substantive (non-placeholder) findings are never dropped. Section headings (`#…`) are never dropped. No gate-verdict recomputation.
- **All tests offline.** Full suite must stay green: **317 → 327** backend tests (+10).
- **Never break the turn** — a reconciliation error injects the review unchanged; it does NOT set `memory_degraded` (reserved for real store failures).

---

### Task 1: Pure reconciliation helper

**Files:**
- Modify: `skills/legal_research.py` — add `import unicodedata` (top); add `_normalize_for_match`, `_placeholder_candidates`, `_reconcile_review_with_doc` immediately after `_strip_redlines_section` (currently ends at `:381`, before `_load_prior_review_block` at `:384`).
- Test: `tests/test_stale_recall_reconciliation.py` (create)

**Interfaces:**
- Consumes: nothing from other tasks. `re` (already imported at `:6`), `unicodedata` (add).
- Produces (Task 2 relies on these exact signatures):
  - `_normalize_for_match(text: str) -> str`
  - `_placeholder_candidates(review_markdown: str) -> list[str]`
  - `_reconcile_review_with_doc(review_markdown: str, doc_text: str) -> tuple[str, list[str]]` — returns `(reconciled_markdown, filled_tokens)`; returns the input unchanged with `[]` when nothing is stale.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stale_recall_reconciliation.py`:

```python
"""Stale-recall reconciliation: drop placeholder findings the current doc proves are filled."""
from skills.legal_research import _reconcile_review_with_doc


def test_filled_placeholder_dropped_and_note_added():
    review = (
        "# Red and Missing Context Items\n"
        "| MC-1 | Signature | `Signed by: [__]` is unfilled | Missing Context |\n"
        "| MC-2 | Date | `Effective Date: [__]` is unfilled | Missing Context |\n"
    )
    doc = "Signed by: Suzy Quatro\nEffective Date: [__]\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out              # filled -> finding row dropped
    assert "MC-2" in out                  # still unfilled -> kept
    assert "Auto-reconciled" in out
    assert "Signed by: [__]" in filled
    assert "Effective Date: [__]" not in filled


def test_still_unfilled_kept_unchanged():
    review = "| MC-1 | Party | `[Legal Name]` blank | Missing Context |\n"
    doc = "Party: [Legal Name]\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert out == review                  # nothing stale -> byte-identical
    assert filled == []


def test_generic_blank_disambiguation():
    review = (
        "| MC-1 | Sig | `Signed by: [__]` unfilled | Missing Context |\n"
        "| MC-2 | Witness | `Witness: [__]` unfilled | Missing Context |\n"
    )
    doc = "Signed by: Suzy Quatro\nWitness: [__]\n"   # first filled, second not
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out
    assert "MC-2" in out
    assert filled == ["Signed by: [__]"]


def test_no_placeholders_returns_unchanged():
    review = "# Summary\nStandalone review, all clear.\n"
    doc = "Any document text.\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert out == review
    assert filled == []


def test_substantive_finding_and_lowercase_bracket_untouched():
    review = (
        "| R-1 | Indemnity | Broad indemnity; add carve-outs [e.g. gross negligence] | Red |\n"
        "| MC-1 | Sig | `Signed by: [__]` unfilled | Missing Context |\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "R-1" in out                   # substantive finding kept
    assert "[e.g. gross negligence]" in out   # lowercase bracket never a placeholder
    assert "MC-1" not in out              # placeholder filled -> dropped
    assert filled == ["Signed by: [__]"]


def test_source_tag_never_a_candidate():
    review = "| G-1 | Draft | `[Source: abc123]` heading tag | Green |\n"
    doc = "Contract text without that tag.\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "G-1" in out                   # not dropped
    assert filled == []


def test_normalization_tolerates_nbsp():
    review = "| MC-1 | Party | `[Legal Name]` blank | Missing Context |\n"
    doc = "Party: [Legal\u00a0Name]\n"    # non-breaking space inside
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" in out                  # nbsp-normalized match -> not stale -> kept
    assert filled == []


def test_malformed_markdown_does_not_crash():
    review = "```\nunterminated `backtick and [Bracket without close\n| bad | row\n"
    doc = "whatever\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert isinstance(out, str)
    assert isinstance(filled, list)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -v`
Expected: FAIL — `ImportError: cannot import name '_reconcile_review_with_doc' from 'skills.legal_research'`

- [ ] **Step 3: Add the `unicodedata` import**

In `skills/legal_research.py`, the top imports currently are (`:4-6`):

```python
import json
import logging
import re
```

Change to:

```python
import json
import logging
import re
import unicodedata
```

- [ ] **Step 4: Implement the helpers**

In `skills/legal_research.py`, immediately AFTER `_strip_redlines_section` (which ends at `:381`) and BEFORE `_load_prior_review_block` (`:384`), insert:

```python
# Placeholder reconciliation ---------------------------------------------------
# A backtick span qualifies as a placeholder quote if it contains any bracket
# token or underscore blank. A BARE bracket token is only treated as a
# placeholder when it is LABELED (starts with an uppercase letter, e.g.
# "[Legal Name]", "[Date]") — a generic blank like "[__]" is ambiguous across
# fields, so it is only ever considered inside its full backtick context.
_MARKER_IN_SPAN_RE = re.compile(r"\[[^\]]{0,40}\]|_{2,}")
_BARE_LABEL_RE = re.compile(r"\[[A-Z][^\]]{0,38}\]")
_SOURCE_TAG_RE = re.compile(r"\[\s*source\s*:", re.IGNORECASE)


def _normalize_for_match(text: str) -> str:
    """NFC + curly->straight quotes + nbsp/whitespace collapse, for tolerant
    substring matching between a review quote and the current document."""
    text = unicodedata.normalize("NFC", text)
    text = (
        text.replace("\u2019", "'").replace("\u2018", "'")   # curly single quotes
        .replace("\u201c", '"').replace("\u201d", '"')       # curly double quotes
        .replace("\u00a0", " ")                              # non-breaking space
    )
    return re.sub(r"\s+", " ", text).strip()


def _placeholder_candidates(review_markdown: str) -> list[str]:
    """Distinct placeholder strings quoted in the review: full backtick spans that
    carry a marker (e.g. `Signed by: [__]`) plus bare LABELED bracket tokens
    (e.g. [Legal Name]). Excludes generated-draft [Source: id] tags."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        s = raw.strip()
        if not s or s in seen or _SOURCE_TAG_RE.search(s):
            return
        seen.add(s)
        candidates.append(s)

    for m in re.finditer(r"`([^`]+)`", review_markdown):      # full field context
        span = m.group(1)
        if _MARKER_IN_SPAN_RE.search(span):
            _add(span)
    for m in _BARE_LABEL_RE.finditer(review_markdown):        # bare labeled tokens
        _add(m.group(0))
    return candidates


def _reconcile_review_with_doc(review_markdown: str, doc_text: str) -> tuple[str, list[str]]:
    """Drop placeholder findings the current doc proves are filled.

    Returns (reconciled_markdown, filled_tokens). A candidate is 'filled' when its
    normalized form is no longer a substring of the normalized document. A line is
    dropped only when EVERY placeholder it references is filled (a line still
    holding a live placeholder is kept); section headings are never dropped. When
    nothing is stale the input is returned unchanged with an empty list.
    """
    if not review_markdown or not doc_text:
        return review_markdown, []
    candidates = _placeholder_candidates(review_markdown)
    if not candidates:
        return review_markdown, []
    norm_doc = _normalize_for_match(doc_text)
    norm_cand = {c: _normalize_for_match(c) for c in candidates}
    filled = [c for c in candidates if norm_cand[c] not in norm_doc]
    if not filled:
        return review_markdown, []
    filled_set = set(filled)

    kept: list[str] = []
    for line in review_markdown.splitlines():
        if line.lstrip().startswith("#"):        # never drop section headings
            kept.append(line)
            continue
        norm_line = _normalize_for_match(line)
        refs = [c for c in candidates if norm_cand[c] in norm_line]
        if refs and all(c in filled_set for c in refs):
            continue                             # every placeholder here is filled -> drop
        kept.append(line)

    note = (
        f"> **Auto-reconciled:** {len(filled)} placeholder(s) flagged in the prior "
        f"review were filled in the document afterward and have been removed from the "
        f"recalled findings: {', '.join('`' + c + '`' for c in filled)}. If these were "
        f"the only signature-block blockers, re-review to confirm the No-Signature gate "
        f"now passes.\n\n"
    )
    return note + "\n".join(kept), filled
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `uv run pytest tests/ -q`
Expected: 325 passed (317 prior + 8 new)

- [ ] **Step 7: Commit**

```bash
git add skills/legal_research.py tests/test_stale_recall_reconciliation.py
git commit -m "feat: pure placeholder reconciliation helper for chat review recall"
```

---

### Task 2: Wire reconciliation into the review-recall path

**Files:**
- Modify: `skills/legal_research.py` — change `_load_prior_review_block` (`:384-405`) signature + body; update its caller in `_run_doc_chat` (`:530`).
- Test: `tests/test_stale_recall_reconciliation.py` (append 2 tests)

**Interfaces:**
- Consumes: `_reconcile_review_with_doc(review_markdown, doc_text) -> tuple[str, list[str]]` (Task 1).
- Produces: `_load_prior_review_block(state: LegalAgentState, uploaded_text: str) -> str` (new required 2nd param).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stale_recall_reconciliation.py`:

```python
import skills.legal_research as lr

_REVIEW_MD = (
    "# Red and Missing Context Items\n"
    "| MC-1 | Sig | `Signed by: [__]` unfilled | Missing Context |\n"
)


def _fake_latest(_db, _doc_id):
    return {"markdown": _REVIEW_MD, "timestamp": "t", "session_id": "s", "contract_type": "nda"}


def test_load_prior_review_block_injects_reconciled(monkeypatch):
    monkeypatch.setattr(lr, "load_latest_review", _fake_latest)
    block = lr._load_prior_review_block({"document_id": "d1"}, "Signed by: Suzy Quatro\n")
    assert "PRIOR REVIEW" in block
    assert "MC-1" not in block            # stale finding reconciled out
    assert "Auto-reconciled" in block


def test_load_prior_review_block_survives_reconcile_error(monkeypatch):
    monkeypatch.setattr(lr, "load_latest_review", _fake_latest)

    def _boom(_review, _doc):
        raise ValueError("reconcile bug")

    monkeypatch.setattr(lr, "_reconcile_review_with_doc", _boom)
    block = lr._load_prior_review_block({"document_id": "d1"}, "Signed by: Suzy Quatro\n")
    assert "PRIOR REVIEW" in block
    assert "MC-1" in block                # falls back to the raw review, unchanged
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -k "load_prior_review_block" -v`
Expected: FAIL — `TypeError: _load_prior_review_block() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Update `_load_prior_review_block`**

Replace the whole function (currently `:384-405`):

```python
def _load_prior_review_block(state: LegalAgentState) -> str:
    """Latest stored review for this document, as a system block. Empty string
    when none exists. On a store-read failure, flags memory_degraded and returns
    empty — tracing/memory must never break the chat turn."""
    document_id = state.get("document_id", "")
    if not document_id:
        return ""
    try:
        latest = load_latest_review(get_settings().sqlite_path, document_id)
    except Exception as e:
        logger.error("[legal_research] prior-review load failed: %s", e)
        state["memory_degraded"] = True
        return ""
    if not latest:
        return ""
    review_text = _strip_redlines_section(latest["markdown"])
    return (
        "--- PRIOR REVIEW (most recent, this document) ---\n"
        "Answer recall questions from this review; do not re-derive or contradict it.\n\n"
        f"{review_text}\n"
        "--- END PRIOR REVIEW ---"
    )
```

with:

```python
def _load_prior_review_block(state: LegalAgentState, uploaded_text: str) -> str:
    """Latest stored review for this document, as a system block. Empty string
    when none exists. On a store-read failure, flags memory_degraded and returns
    empty — tracing/memory must never break the chat turn.

    Reconciles the recalled review against the current document (uploaded_text):
    placeholder findings the document proves were filled after the review are
    dropped, so chat does not report an already-filled field as unfilled. A
    reconciliation error injects the review unchanged (never fails the turn) and
    does NOT flag memory_degraded — that is reserved for real store failures."""
    document_id = state.get("document_id", "")
    if not document_id:
        return ""
    try:
        latest = load_latest_review(get_settings().sqlite_path, document_id)
    except Exception as e:
        logger.error("[legal_research] prior-review load failed: %s", e)
        state["memory_degraded"] = True
        return ""
    if not latest:
        return ""
    review_text = _strip_redlines_section(latest["markdown"])
    if uploaded_text:
        try:
            review_text, _filled = _reconcile_review_with_doc(review_text, uploaded_text)
        except Exception as e:
            logger.warning(
                "[legal_research] review reconciliation failed: %s — injecting review unchanged", e
            )
    return (
        "--- PRIOR REVIEW (most recent, this document) ---\n"
        "Answer recall questions from this review; do not re-derive or contradict it.\n\n"
        f"{review_text}\n"
        "--- END PRIOR REVIEW ---"
    )
```

- [ ] **Step 4: Update the caller in `_run_doc_chat`**

At `skills/legal_research.py:530`, change:

```python
    review_block = _load_prior_review_block(state)
```

to:

```python
    review_block = _load_prior_review_block(state, uploaded_text)
```

(`uploaded_text` is already the 2nd parameter of `_run_doc_chat` and is in scope here.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_stale_recall_reconciliation.py -v`
Expected: PASS (10 passed)

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `uv run pytest tests/ -q`
Expected: 327 passed (317 prior + 10 new)

- [ ] **Step 7: Commit**

```bash
git add skills/legal_research.py tests/test_stale_recall_reconciliation.py
git commit -m "feat: reconcile recalled review against current doc on chat recall path"
```

---

## Notes for the implementer

- **Grep before you insert.** Confirm `_strip_redlines_section` ends where expected and `_load_prior_review_block` immediately follows; line numbers may have drifted. Anchor on the function names, not the numbers.
- **`_run_doc_chat` has exactly one call** to `_load_prior_review_block` (at `:530`). Verify with `grep -n "_load_prior_review_block" skills/legal_research.py` — there should be the definition and one call; update only the call.
- **Do not touch** `CHAT_SYSTEM_PROMPT`, `_JSON_RETRY_SYSTEM`, `_build_chat_grounding`, `_load_prior_conversation`, or any config/graph/client file.
- **uvicorn does not auto-reload** Python changes — irrelevant for tests, but a manual smoke would need `bash scripts/start.sh` restarted.

## Refinements from the spec (intentional — both serve the spec's false-drop risk priority)

The spec's algorithm is implemented with two deliberate tightenings. They are NOT gaps — each reduces false drops, which the spec names as the primary risk:

1. **Underscore blanks are recognized only inside a backtick span**, not as bare standalone tokens. A bare `_{2,}` run outside backticks collides with Markdown emphasis (`__bold__`) and rules, so treating it as a fillable blank would risk dropping unrelated lines. In practice the review quotes current wording in backticks (the Layer-1 "quote current wording" cue), so blanks are captured via their full span. Bare **bracket** tokens are still candidates, but only when **labeled** (`[A-Z]…`, e.g. `[Legal Name]`) — never a bare `[__]`, which is ambiguous across fields and only ever matched inside its full backtick context.
2. **A line is dropped only when EVERY placeholder it references is filled** (spec step 4 said "references a stale candidate"). A line mixing a filled and a still-unfilled placeholder is **kept**, so a genuinely live warning is never lost. This is strictly safer than the literal spec wording and consistent with the spec's "still-unfilled → kept" intent.
