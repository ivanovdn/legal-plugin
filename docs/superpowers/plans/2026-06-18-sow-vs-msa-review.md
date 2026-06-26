# SOW-vs-MSA Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `contract_review` detects a SOW, auto-fetch the governing MSA from Qdrant and inject it into the review prompt as a "GOVERNING MSA" reference plus a comparison directive, so the SOW is reviewed against its parent.

**Architecture:** Backend-only, strictly additive. A new `rag/related_docs.py::get_parent_msa()` scrolls Qdrant by `doc_type="msa"` + `client_id`. `skills/contract_review/contract_review.py` calls it only on the SOW path, appends the MSA text to the user message (capped) and a structural directive as the last system message. A small `scripts/ingest_demo_msa.py` ingests the one demo MSA. Every failure path degrades to today's standalone SOW review.

**Tech Stack:** Python 3.12, Qdrant (`qdrant_client`), Langfuse `@observe`, pytest. Spec: [docs/superpowers/specs/2026-06-18-sow-vs-msa-review-design.md](../specs/2026-06-18-sow-vs-msa-review-design.md).

---

## Context (read before starting)

- **Injection point:** [skills/contract_review/contract_review.py:189-216](../../../skills/contract_review/contract_review.py#L189-L216). `contract_type` is already detected at line 166; `_OUTPUT_CONSTRAINTS` (line 93) is the pattern to mirror for the new directive constant. `langfuse_context` is already imported (line 20).
- **Qdrant access:** `scroll_by_filter(filter_conditions, collection, limit=100)` → `list[payload dict]`; `delete_document(doc_id, collection)` ([rag/vector_store.py:99,144](../../../rag/vector_store.py#L99)). Chunk payloads carry `doc_id`, `doc_title`, `chunk_index`, `text` ([ingest/chunk_models.py](../../../ingest/chunk_models.py)).
- **Ingest:** `ingest_document(filepath, client_id, jurisdiction, doc_type, sensitivity, collection) -> int` ([ingest/pipeline.py:23](../../../ingest/pipeline.py#L23)). Parsers assign **random UUID** `chunk_id`/`doc_id`, so a re-ingest leaves a *second* MSA unless prior chunks are cleared first.
- **Tests:** `tests/test_skills.py` holds the `contract_review` tests and the sample constants `_NDA_SAMPLE`, `_MSA_SAMPLE`, `_SOW_SAMPLE` (lines 881-895) + the `_make_state(**overrides)` helper (line 14, default `filters={"client_id": "internal"}`). Mocked Qdrant/LLM — no live services.
- **Run tests with:** `./.venv/bin/python -m pytest ... -v`.
- **Backend does NOT hot-reload Python** — restart `bash scripts/start.sh` after changes for live smoke.

## File structure

| File | Responsibility |
|---|---|
| `rag/related_docs.py` *(new)* | `get_parent_msa(client_id, collection)` — retrieve the governing MSA's `(title, full_text)` for a client. Single purpose; depends only on `scroll_by_filter`. |
| `skills/contract_review/contract_review.py` *(modify)* | On the SOW path only: attach the MSA + directive. New constants `_MSA_MAX_CHARS`, `_MSA_COMPARISON_DIRECTIVE`. |
| `scripts/ingest_demo_msa.py` *(new)* | One-time demo prep: clear prior MSA chunks, ingest the model MSA as `doc_type="msa"`. |
| `tests/test_related_docs.py` *(new)* | Unit tests for `get_parent_msa`. |
| `tests/test_skills.py` *(modify)* | `contract_review` SOW/MSA injection + edge-case tests. |
| `tests/test_ingest_demo_msa.py` *(new)* | Guard that the demo script tags `doc_type="msa"` and clears prior MSAs. |
| `docs/wiki.md`, `CLAUDE.md` *(modify)* | Shipped row + architecture note (Task 4). |

---

## Task 1: `get_parent_msa` retrieval helper

**Files:**
- Create: `rag/related_docs.py`
- Test: `tests/test_related_docs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_related_docs.py`:

```python
# tests/test_related_docs.py
"""Unit tests for parent-document retrieval (the governing MSA for a SOW)."""
from __future__ import annotations

import rag.related_docs as mod
from rag.related_docs import get_parent_msa


def test_get_parent_msa_concatenates_chunks_in_index_order(monkeypatch):
    # Returned out of order; must be sorted by chunk_index before joining.
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [
        {"doc_id": "msa-1", "doc_title": "Model MSA", "chunk_index": 2, "text": "third"},
        {"doc_id": "msa-1", "doc_title": "Model MSA", "chunk_index": 0, "text": "first"},
        {"doc_id": "msa-1", "doc_title": "Model MSA", "chunk_index": 1, "text": "second"},
    ])
    result = get_parent_msa("internal")
    assert result == ("Model MSA", "first\n\nsecond\n\nthird")


def test_get_parent_msa_returns_none_when_no_chunks(monkeypatch):
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [])
    assert get_parent_msa("internal") is None


def test_get_parent_msa_returns_none_without_client_id(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(mod, "scroll_by_filter",
                        lambda **kw: called.__setitem__("n", called["n"] + 1) or [])
    assert get_parent_msa("") is None
    assert called["n"] == 0  # never queries Qdrant without a client_id


def test_get_parent_msa_picks_one_deterministically_when_multiple(monkeypatch):
    # Two MSAs on file → pick the first doc_id alphabetically, only its chunks.
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [
        {"doc_id": "b-msa", "doc_title": "B", "chunk_index": 0, "text": "from B"},
        {"doc_id": "a-msa", "doc_title": "A", "chunk_index": 0, "text": "from A"},
    ])
    title, text = get_parent_msa("internal")
    assert title == "A"
    assert text == "from A"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_related_docs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rag.related_docs'`.

- [ ] **Step 3: Implement `rag/related_docs.py`**

```python
# rag/related_docs.py
"""Retrieve related/parent documents for cross-document review.

Currently serves one case: the governing MSA for a SOW review. A SOW is a child
document issued under a parent MSA; `contract_review` uses this to pull the MSA
so the SOW can be reviewed against it.
"""
from __future__ import annotations

import logging

from rag.vector_store import scroll_by_filter

logger = logging.getLogger(__name__)

# Generous ceiling; one MSA is far fewer chunks than this.
_MAX_MSA_CHUNKS = 500


def get_parent_msa(client_id: str, collection: str = "legal_docs") -> tuple[str, str] | None:
    """Return (doc_title, full_text) of the governing MSA on file for client_id.

    Filters Qdrant by doc_type="msa" + client_id. When more than one MSA is
    present, picks the first by doc_id (a one-MSA-per-client demo assumption;
    matching the *specific* parent by party name is future work) and logs it.
    Sorts the chosen MSA's chunks by chunk_index and concatenates their text.
    Returns None when client_id is falsy or no MSA chunks are found.
    """
    if not client_id:
        return None

    chunks = scroll_by_filter(
        filter_conditions={"doc_type": "msa", "client_id": client_id},
        collection=collection,
        limit=_MAX_MSA_CHUNKS,
    )
    if not chunks:
        return None

    doc_ids = sorted({c.get("doc_id", "") for c in chunks})
    if len(doc_ids) > 1:
        logger.warning(
            "[get_parent_msa] %d MSAs on file for client_id=%s; using %r "
            "(party-name matching is future work)",
            len(doc_ids), client_id, doc_ids[0],
        )
    chosen = doc_ids[0]
    msa_chunks = [c for c in chunks if c.get("doc_id", "") == chosen]
    msa_chunks.sort(key=lambda c: c.get("chunk_index", 0))

    title = msa_chunks[0].get("doc_title", "Master Services Agreement")
    text = "\n\n".join(c.get("text", "") for c in msa_chunks)
    return title, text
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_related_docs.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 5: Commit**

```bash
git add rag/related_docs.py tests/test_related_docs.py
git commit -m "feat(rag): get_parent_msa — retrieve governing MSA for a client

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Inject the governing MSA into the SOW review

**Files:**
- Modify: `skills/contract_review/contract_review.py` (import at top; constants after line 109; block at lines 189-216)
- Test: `tests/test_skills.py` (append after `test_contract_review_msa_loads_msa_matrix`, ~line 1002)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills.py` (the `_SOW_SAMPLE` / `_NDA_SAMPLE` constants and `_make_state` are already defined in the file):

```python
# --- contract_review: governing-MSA attachment (SOW-vs-MSA) ---

def _patch_msa(monkeypatch, value):
    """Patch get_parent_msa as imported into the contract_review module."""
    monkeypatch.setattr(
        "skills.contract_review.contract_review.get_parent_msa",
        value,
    )


def test_contract_review_sow_attaches_governing_msa(monkeypatch):
    _patch_msa(monkeypatch, lambda client_id, **kw: ("Model MSA", "MSA LIABILITY CAP = 12 MONTHS FEES."))
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    assert result["contract_type_detected"] == "sow"
    user_msg = result["messages"][-1]["content"]
    assert "--- GOVERNING MSA (Model MSA) ---" in user_msg
    assert "MSA LIABILITY CAP = 12 MONTHS FEES." in user_msg
    # The comparison directive is the LAST system message (most-recent instruction).
    assert result["messages"][-2]["role"] == "system"
    assert "GOVERNING MSA COMPARISON" in result["messages"][-2]["content"]


def test_contract_review_sow_standalone_when_no_msa(monkeypatch):
    _patch_msa(monkeypatch, lambda client_id, **kw: None)
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    user_msg = result["messages"][-1]["content"]
    assert "GOVERNING MSA" not in user_msg
    sys_contents = [m["content"] for m in result["messages"] if m["role"] == "system"]
    assert not any("GOVERNING MSA COMPARISON" in c for c in sys_contents)
    # Standalone review still built: playbook + output constraints + user.
    assert len(result["messages"]) == 3


def test_contract_review_nda_does_not_attach_msa(monkeypatch):
    called = {"n": 0}

    def _spy(client_id, **kw):
        called["n"] += 1
        return ("X", "Y")

    _patch_msa(monkeypatch, _spy)
    state = _make_state(
        request="Review this NDA.",
        uploaded_docs=[{"text": _NDA_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    assert called["n"] == 0  # never looked up for a non-SOW
    assert "GOVERNING MSA" not in result["messages"][-1]["content"]


def test_contract_review_truncates_oversized_msa(monkeypatch):
    big = "Z" * 30000
    _patch_msa(monkeypatch, lambda client_id, **kw: ("Big MSA", big))
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    user_msg = result["messages"][-1]["content"]
    assert "[MSA truncated to 24000 chars for review]" in user_msg
    assert ("Z" * 30000) not in user_msg  # full text not injected


def test_contract_review_msa_lookup_error_reviews_standalone(monkeypatch):
    def _boom(client_id, **kw):
        raise RuntimeError("qdrant down")

    _patch_msa(monkeypatch, _boom)
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)  # must NOT raise

    assert result["contract_type_detected"] == "sow"
    assert "GOVERNING MSA" not in result["messages"][-1]["content"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k "governing_msa or attaches or standalone or oversized or lookup_error or nda_does_not" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'get_parent_msa'` (the name isn't imported into the module yet) / no `GOVERNING MSA` text.

- [ ] **Step 3: Add the import**

In `skills/contract_review/contract_review.py`, after `from skills.base import load_bundle` (line 23), add:

```python
from rag.related_docs import get_parent_msa
```

- [ ] **Step 4: Add the constants**

In `skills/contract_review/contract_review.py`, immediately after the `_OUTPUT_CONSTRAINTS = """..."""` block (ends line 109), add:

```python
# Max chars of MSA text injected into a SOW review. Guards the local LLM's
# context window so a huge MSA can't crowd out the SOW + playbook. Promote to
# config.Settings when scaling past the one-MSA demo.
_MSA_MAX_CHARS = 24000

# Added (as the LAST system message) only when a governing MSA is attached to a
# SOW review. Structural and model-neutral: it tells the model to USE the
# supplied MSA and grounds the hierarchy in the playbook's own words ("SOW terms
# prevail only for that SOW"); it does not encode legal positions (SKILL.md is
# the ceiling). Rule 3 stops the local LLM hallucinating "the MSA says X".
_MSA_COMPARISON_DIRECTIVE = """GOVERNING MSA COMPARISON — this SOW is issued \
under the Master Services Agreement included below as "GOVERNING MSA":

1. The MSA is the parent framework. Per the playbook, SOW terms apply only to \
this SOW and must not conflict with or override the MSA.

2. Flag, as findings, any SOW term that (a) contradicts an MSA term, (b) \
purports to override or weaken an MSA protection, or (c) is required by the MSA \
but missing or inconsistent in the SOW (e.g. the MSA date/version reference, \
payment terms, IP ownership, confidentiality, liability cap). Cite the relevant \
MSA clause in the Issue, and apply the SOW playbook's risk rating and approval \
rules as usual.

3. Do NOT invent MSA terms. Base every MSA-conflict finding only on text present \
in the GOVERNING MSA below; if the MSA is silent on a point, say so rather than \
assuming."""
```

- [ ] **Step 5: Replace the message-assembly block**

In `skills/contract_review/contract_review.py`, replace the block from `playbook = load_bundle(...)` through the `state["messages"] = [...]` assignment (lines 189-216) with:

```python
    playbook = load_bundle(_PLAYBOOK_DIR, contract_type)

    # Build user message with contract text if available
    if uploaded_text:
        user_content = (
            f"{request}\n\n"
            f"--- CONTRACT TEXT ---\n"
            f"{uploaded_text}\n"
            f"--- END CONTRACT TEXT ---"
        )
        # No need for RAG retrieval when contract is uploaded
        state["retrieval_query"] = ""
    else:
        user_content = request
        state["retrieval_query"] = request

    # SOW review: pull the governing MSA from Qdrant and attach it so the SOW is
    # reviewed against its parent. Strictly additive — any failure degrades to a
    # standalone SOW review. SOW path with an uploaded doc only.
    msa_attached = False
    msa_doc_title = ""
    if contract_type == "sow" and uploaded_text:
        client_id = (state.get("filters") or {}).get("client_id", "")
        try:
            parent = get_parent_msa(client_id)
        except Exception:  # retrieval must never break the review
            logger.exception(
                "[contract_review] parent-MSA lookup failed — reviewing SOW standalone"
            )
            parent = None
        if parent:
            msa_doc_title, msa_text = parent
            if len(msa_text) > _MSA_MAX_CHARS:
                logger.warning(
                    "[contract_review] MSA %r is %d chars — truncating to %d for review",
                    msa_doc_title, len(msa_text), _MSA_MAX_CHARS,
                )
                msa_text = (
                    msa_text[:_MSA_MAX_CHARS]
                    + f"\n\n[MSA truncated to {_MSA_MAX_CHARS} chars for review]"
                )
            user_content += (
                f"\n\n--- GOVERNING MSA ({msa_doc_title}) ---\n"
                f"{msa_text}\n"
                f"--- END GOVERNING MSA ---"
            )
            msa_attached = True
            logger.info(
                "[contract_review] attached governing MSA %r (%d chars)",
                msa_doc_title, len(msa_text),
            )
        else:
            logger.info(
                "[contract_review] no governing MSA on file for client_id=%s — "
                "reviewing SOW standalone",
                client_id,
            )

    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    system_messages = [
        {"role": "system", "content": playbook},
        {"role": "system", "content": _OUTPUT_CONSTRAINTS},
    ]
    if msa_attached:
        system_messages.append({"role": "system", "content": _MSA_COMPARISON_DIRECTIVE})
    state["messages"] = system_messages + [{"role": "user", "content": user_content}]

    # Surface MSA attachment on the trace (best-effort; never breaks the skill).
    try:
        langfuse_context.update_current_trace(
            metadata={"msa_attached": msa_attached, "msa_doc_title": msa_doc_title},
        )
    except Exception:  # pragma: no cover - observability is best-effort
        pass
```

(The trailing `logger.info("[contract_review] prepared: ...")` and `return state` at lines 218-226 are unchanged.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k "governing_msa or attaches or standalone or oversized or lookup_error or nda_does_not" -v`
Expected: PASS — 5 passed.

- [ ] **Step 7: Run the full contract_review suite (no regressions)**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k contract_review -v`
Expected: PASS — all existing `contract_review` tests still pass (the standalone path is unchanged when no MSA is attached).

- [ ] **Step 8: Commit**

```bash
git add skills/contract_review/contract_review.py tests/test_skills.py
git commit -m "feat(contract_review): attach governing MSA when reviewing a SOW

On the SOW path, pull the governing MSA from Qdrant (get_parent_msa) and
inject it as a GOVERNING MSA reference plus a structural comparison
directive (last system message). Strictly additive — no MSA / lookup
error / non-SOW all degrade to the standalone review. Oversized MSA is
truncated to _MSA_MAX_CHARS.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Demo-prep ingest script

**Files:**
- Create: `scripts/ingest_demo_msa.py`
- Test: `tests/test_ingest_demo_msa.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest_demo_msa.py`:

```python
# tests/test_ingest_demo_msa.py
"""The demo MSA ingest tags the doc as doc_type=msa and clears prior MSAs."""
from __future__ import annotations

import scripts.ingest_demo_msa as mod


def test_tags_document_as_msa(monkeypatch, tmp_path):
    fake = tmp_path / "msa.docx"
    fake.write_text("x")
    monkeypatch.setattr(mod, "_MSA_PATH", fake)
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [])
    captured: dict = {}
    monkeypatch.setattr(mod, "ingest_document", lambda **kw: captured.update(kw) or 7)

    rc = mod.main()

    assert rc == 0
    assert captured["doc_type"] == "msa"
    assert captured["client_id"] == "internal"
    assert captured["collection"] == "legal_docs"
    assert captured["filepath"] == fake


def test_clears_existing_msas_before_ingest(monkeypatch, tmp_path):
    fake = tmp_path / "msa.docx"
    fake.write_text("x")
    monkeypatch.setattr(mod, "_MSA_PATH", fake)
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [
        {"doc_id": "old-1"}, {"doc_id": "old-1"}, {"doc_id": "old-2"},
    ])
    deleted: list = []
    monkeypatch.setattr(mod, "delete_document", lambda doc_id, collection: deleted.append(doc_id))
    monkeypatch.setattr(mod, "ingest_document", lambda **kw: 3)

    mod.main()

    assert sorted(deleted) == ["old-1", "old-2"]


def test_missing_file_returns_error_code(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_MSA_PATH", tmp_path / "nope.docx")
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [])
    assert mod.main() == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_ingest_demo_msa.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.ingest_demo_msa'`.

- [ ] **Step 3: Implement `scripts/ingest_demo_msa.py`**

```python
#!/usr/bin/env python3
"""One-time demo prep: ingest the model MSA into Qdrant as the governing MSA.

Ingests `data/Trinetix Model MSA 2025 (3)-1.docx` with doc_type="msa" and
client_id="internal" so contract_review auto-attaches it when reviewing a SOW.
Clears any previously-ingested MSA chunks for the client first, because the
parsers assign random-UUID doc_ids (a naive re-ingest would otherwise leave a
second MSA on file).

    uv run python -m scripts.ingest_demo_msa
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.pipeline import ingest_document
from rag.vector_store import delete_document, scroll_by_filter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_MSA_PATH = Path(__file__).resolve().parent.parent / "data" / "Trinetix Model MSA 2025 (3)-1.docx"
_CLIENT_ID = "internal"
_COLLECTION = "legal_docs"


def main() -> int:
    if not _MSA_PATH.exists():
        logger.error("MSA file not found: %s", _MSA_PATH)
        return 1

    # Clear prior MSA chunks for this client so re-runs stay clean.
    existing = scroll_by_filter(
        filter_conditions={"doc_type": "msa", "client_id": _CLIENT_ID},
        collection=_COLLECTION,
        limit=1000,
    )
    for doc_id in sorted({c.get("doc_id", "") for c in existing if c.get("doc_id")}):
        delete_document(doc_id, _COLLECTION)
        logger.info("Cleared prior MSA doc_id=%s", doc_id)

    count = ingest_document(
        filepath=_MSA_PATH,
        client_id=_CLIENT_ID,
        jurisdiction="",
        doc_type="msa",
        sensitivity="internal",
        collection=_COLLECTION,
    )
    logger.info("Ingested %d chunks from %s as doc_type=msa", count, _MSA_PATH.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_ingest_demo_msa.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest_demo_msa.py tests/test_ingest_demo_msa.py
git commit -m "chore(scripts): ingest_demo_msa — load the demo MSA as doc_type=msa

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Full suite, live smoke, docs, merge

**Files:**
- Modify: `docs/wiki.md`, `CLAUDE.md`

- [ ] **Step 1: Full suite green**

Run: `./.venv/bin/python -m pytest tests/ -q`
Expected: PASS — prior suite + the new `get_parent_msa`, `contract_review` MSA, and demo-script tests; no regressions.

- [ ] **Step 2: Ingest the demo MSA into Qdrant**

Run (Qdrant must be up via `docker compose`):
```bash
uv run python -m scripts.ingest_demo_msa
```
Expected: log `Ingested N chunks from Trinetix Model MSA 2025 (3)-1.docx as doc_type=msa`.

Verify it's retrievable:
```bash
uv run python -c "from rag.related_docs import get_parent_msa; t,x=get_parent_msa('internal'); print(t, len(x))"
```
Expected: prints the MSA title and a non-zero char count.

- [ ] **Step 3: Restart the backend** (Python is not hot-reloaded)

Run: `bash scripts/start.sh`

- [ ] **Step 4: Live-smoke a SOW review**

Open the SOW in Word and click "Review this contract" (or `POST /api/query` with `task_type=contract_review`, `filters={"client_id":"internal"}`, `uploaded_text=<SOW text>`). Confirm:
- The review output references the MSA (e.g. flags a SOW term that conflicts with / is governed by the MSA, or notes the MSA date/version reference).
- In Langfuse, the `contract_review` span's trace metadata shows `msa_attached: true` and `msa_doc_title`.
- Sanity: review a SOW after temporarily clearing the MSA (or a doc with no MSA on file) → review still completes, `msa_attached: false`.

- [ ] **Step 5: Update `docs/wiki.md`**

Add a row under "Shipped Since Last Update": SOW reviews now auto-attach the governing MSA from Qdrant (`get_parent_msa` → `contract_review` GOVERNING MSA block + structural comparison directive); backend-only, strictly additive; demo prep via `scripts/ingest_demo_msa.py`. Bump the test count. Under follow-ups, add the future-scale items from the spec (match the *specific* parent MSA by party name; RAG-select MSA clauses for large MSAs; promote `_MSA_MAX_CHARS` to settings).

- [ ] **Step 6: Update `CLAUDE.md`**

Under the Backend section's solved-problems list, add a bullet: SOW reviews auto-attach the governing MSA — `contract_review` calls `rag/related_docs.py::get_parent_msa(client_id)` only when `contract_type == "sow"`, injects a `GOVERNING MSA` block (capped at `_MSA_MAX_CHARS`) + the `_MSA_COMPARISON_DIRECTIVE` (last system message). Strictly additive: no MSA / lookup error / non-SOW → standalone review. Comparison directive is structural/model-neutral (no legal substance — SKILL.md is still the ceiling); demo MSA loaded via `scripts/ingest_demo_msa.py` (`doc_type="msa"`).

- [ ] **Step 7: Commit docs**

```bash
git add docs/wiki.md CLAUDE.md
git commit -m "docs: SOW-vs-MSA review — wiki shipped row + CLAUDE.md note

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: Merge to main**

```bash
git checkout main
git merge --no-ff feat/sow-vs-msa-review
git branch -d feat/sow-vs-msa-review
```

---

## Self-review (completed)

**Spec coverage:** `get_parent_msa` (Task 1) ✓; SOW-only injection + directive + truncation + trace + graceful failure (Task 2) ✓; demo ingest with the directory-pollution fix (Task 3) ✓; all six spec test cases mapped to Task 1/2 tests ✓; future-scale items captured in Out of scope ✓.

**Type consistency:** `get_parent_msa(client_id, collection="legal_docs") -> tuple[str, str] | None` is defined in Task 1 and called identically in Task 2; `_MSA_MAX_CHARS` (24000) matches the truncation-marker assertion in the Task 2 test; `_MSA_COMPARISON_DIRECTIVE` text contains "GOVERNING MSA COMPARISON" asserted by the test; the injected block marker `--- GOVERNING MSA (title) ---` matches the test assertion.

**Placeholders:** none — every code/test step is complete and runnable.

## Out of scope (future-scale — noted, not built)

- Match the *specific* parent MSA by party name / explicit SOW reference instead of "the one on file for the client_id".
- RAG-select only MSA clauses relevant to the SOW (instead of full text) when MSAs grow large.
- Promote `_MSA_MAX_CHARS` to `config.Settings`.
- Author formal MSA-vs-SOW comparison rules into the SOW `SKILL.md` source (legal-team-owned).
