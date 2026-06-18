# SOW-vs-MSA Review — Design

**Date:** 2026-06-18
**Status:** Approved (design); pending implementation plan
**Branch:** `feat/sow-vs-msa-review`

## Goal

When `contract_review` detects the uploaded document is a **SOW**, automatically fetch the
governing **MSA** from Qdrant and add it to the review prompt as a "GOVERNING MSA" reference,
together with a directive to flag SOW terms that conflict with the MSA. The MSA is **strictly
additive** — every failure path degrades to today's standalone SOW review.

This is a backend-only change: no Word add-in or API request-model change is required.

## Why

A SOW is a child document issued under a parent MSA; its terms apply only to that SOW and must
not conflict with the MSA. Today `contract_review` reviews each document in isolation
([contract_review.py:192-198](../../../skills/contract_review/contract_review.py#L192-L198)),
so SOW/MSA conflicts are invisible. The RAG/Qdrant layer already exists but is underused for
review — this feature is the natural home for "the firm's master agreements are on file."

## Architecture

```
Demo prep (one-time):
  uv run python -m scripts.ingest_demo_msa
      → ingest_document("data/Trinetix Model MSA 2025 (3)-1.docx",
                        client_id="internal", doc_type="msa", ...) → Qdrant "legal_docs"

Runtime (Word "Review this contract" on a SOW — client UNCHANGED):
  submitReview(sowText) ──POST /api/query──▶ contract_review(state)
     │
     ├─ _detect_contract_type(sowText) → "sow"          [existing, contract_review.py:166]
     ├─ IF contract_type == "sow":
     │     msa = get_parent_msa(client_id)               [NEW helper → Qdrant scroll]
     │     IF msa: inject "--- GOVERNING MSA ---" block into user_content
     │             add _MSA_COMPARISON_DIRECTIVE system message
     │             trace: msa_attached=true, msa_doc_title=...
     │     ELSE:   log "no governing MSA on file"; proceed standalone; msa_attached=false
     └─ build messages → llm_caller → review output (Key Findings now include MSA conflicts)
```

The trigger (`contract_type == "sow"`) and `client_id` (via `filters`,
[api.ts:49](../../../clients/word/src/api.ts#L49)) are already present, so `submitReview` and
`QueryRequest` are untouched.

## Components

### 1. `rag/related_docs.py` (new) — parent-MSA retrieval

```python
def get_parent_msa(client_id: str, collection: str = "legal_docs") -> tuple[str, str] | None:
    """Return (doc_title, full_text) of the governing MSA on file for client_id, or None.

    Thin wrapper over scroll_by_filter (rag/vector_store.py:144). Filters by
    doc_type="msa" + client_id, sorts chunks by chunk_index, concatenates text.
    Returns None when no MSA chunks are found. When more than one distinct MSA
    doc_id is present, picks the first by doc_id and logs (one-MSA-per-client
    demo assumption; party-name matching is future work).
    """
```

- **What it does:** retrieve the single governing MSA's full text for a client.
- **How it's used:** called by `contract_review` only on the SOW path.
- **Depends on:** `rag.vector_store.scroll_by_filter`.

### 2. `skills/contract_review/contract_review.py` — injection

- New module constant `_MSA_COMPARISON_DIRECTIVE` (sibling of `_OUTPUT_CONSTRAINTS`).
- New module constant `_MSA_MAX_CHARS = 24000` (size guard; promote to settings when scaling).
- In `contract_review`, after `contract_type` is known and `uploaded_text` is built: when
  `contract_type == "sow"`, call `get_parent_msa(client_id)` inside a `try/except` (retrieval
  must never break the review). On success, append a `--- GOVERNING MSA ---` block to
  `user_content` (truncated to `_MSA_MAX_CHARS` with an inline "[MSA truncated…]" note when over)
  and add `_MSA_COMPARISON_DIRECTIVE` as a system message placed **last** (most-recent instruction
  before the user content). Surface `msa_attached` / `msa_doc_title` on the Langfuse trace
  (best-effort, alongside the existing `update_current_trace`).
- `client_id` is read from `state["filters"].get("client_id")`.

Resulting message order when an MSA is attached:
```
[playbook, _OUTPUT_CONSTRAINTS, _MSA_COMPARISON_DIRECTIVE, user_content(SOW + GOVERNING MSA)]
```

### 3. `scripts/ingest_demo_msa.py` (new) — demo prep

A small committed utility that ingests **only** the one MSA with the correct metadata
(`doc_type="msa"`, `client_id="internal"`, `collection="legal_docs"`), avoiding the
directory-pollution problem of pointing `ingest_all.py` at `data/` (which also holds the NDA and
the playbook `.docx`). Calls `ingest.pipeline.ingest_document` directly with the MSA path.

## The comparison directive (verbatim intent)

Structural and model-neutral — it tells the model to *use* the supplied MSA and grounds the
hierarchy in the playbook's own words ("SOW terms prevail only for that SOW"), per hard-rule #2
(SKILL.md is the ceiling; no improvising legal positions from pretraining):

> GOVERNING MSA COMPARISON — this SOW is issued under the Master Services Agreement included
> below as "GOVERNING MSA":
> 1. The MSA is the parent framework. Per the playbook, SOW terms apply only to this SOW and must
>    not conflict with or override the MSA.
> 2. Flag, as findings, any SOW term that (a) contradicts an MSA term, (b) purports to
>    override/weaken an MSA protection, or (c) is required by the MSA but missing/inconsistent
>    (e.g. MSA date/version reference, payment, IP, confidentiality, liability cap). Cite the
>    relevant MSA clause in the Issue, and apply the SOW playbook's risk rating + approval rules
>    as usual.
> 3. Do not invent MSA terms. Base every MSA-conflict finding only on text present in the
>    GOVERNING MSA below; if the MSA is silent on a point, say so rather than assuming.

Rule 3 prevents hallucinated "MSA says X" findings on the local LLM. The directive lives in tracked
code, not the gitignored `data/contract_review_skills/` source.

## Error handling / edge cases

| Case | Behavior |
|---|---|
| No MSA in Qdrant for `client_id` | Log "no governing MSA on file"; review SOW standalone; no block/directive; `msa_attached=false` |
| Qdrant retrieval error | Caught, logged; proceed standalone (retrieval never breaks the review) |
| MSA text > `_MSA_MAX_CHARS` | Truncate; warn; mark "[MSA truncated to N chars]" in the block |
| Non-SOW (nda/msa/baa) | `get_parent_msa` never called — an MSA review does not pull a SOW |
| >1 distinct MSA for client | Pick first by `doc_id` + log; party-name matching is future work |
| `client_id` missing from filters | Treat as no MSA found; review standalone |

## Testing

Deterministic unit tests in `tests/test_skills.py` (where the existing `test_contract_review_*`
tests live) plus the helper test, mocked Qdrant + LLM (matches repo style — no live services):

- **`get_parent_msa`**: out-of-order chunks → sorted-by-`chunk_index` + concatenated `(title, text)`;
  no chunks → `None`; chunks from two `doc_id`s → picks one deterministically.
- **`contract_review` (SOW + MSA present)**: monkeypatch `get_parent_msa` → returns text; assert
  `user_content` contains `GOVERNING MSA` + the MSA text, the `_MSA_COMPARISON_DIRECTIVE` system
  message is present and last among system messages, `contract_type_detected == "sow"`.
- **`contract_review` (SOW + no MSA)**: `get_parent_msa` → `None`; assert no `GOVERNING MSA` block,
  no directive, review messages still built (standalone path intact).
- **`contract_review` (NDA upload)**: assert `get_parent_msa` is not called / its result never
  injected; no MSA block.
- **`contract_review` (oversized MSA)**: MSA text > `_MSA_MAX_CHARS` → block truncated +
  truncation marker present.
- **`contract_review` (retrieval raises)**: `get_parent_msa` raises → review still built standalone
  (no exception propagates).

## Out of scope / future-scale (noted, not built)

- Match the *specific* parent MSA by party name or an explicit MSA reference inside the SOW,
  instead of "the one MSA on file for this client_id".
- RAG-select only the MSA clauses relevant to the SOW (instead of full text) when MSAs grow large.
- Promote `_MSA_MAX_CHARS` to `config.Settings`.
- Authoring formal MSA-vs-SOW comparison rules into the SOW `SKILL.md` source (legal-team-owned;
  the current directive is a thin structural framing, not legal substance).

## Files

| File | Change |
|---|---|
| `rag/related_docs.py` | **New** — `get_parent_msa()` |
| `skills/contract_review/contract_review.py` | Inject MSA + directive on the SOW path; new constants |
| `scripts/ingest_demo_msa.py` | **New** — one-time demo ingest of the MSA |
| `tests/test_skills.py` | `contract_review` injection + edge-case tests (alongside existing `test_contract_review_*`) |
| `tests/test_related_docs.py` | **New** — `get_parent_msa` unit tests |
| `docs/wiki.md` | "Shipped" row on merge |
