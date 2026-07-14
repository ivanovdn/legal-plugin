# Canonical Document UUID Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt a client-supplied, document-embedded UUID as the canonical `document_id` so the review store (write + recall) survives placeholder fills / redlines, fixing the silent review-recall fragility found in smoke testing.

**Architecture:** Document identity already resolves at one seam — `graph/nodes/intake.py` sets `state["document_id"]`, and both the review write (`memory_writer.py`) and the chat recall (`legal_research.py`) read it. The client mints/reads a UUID in `Office.context.document.settings` and sends it as `document_uuid`; the route seeds `state["document_id"]` from it; intake prefers that value over the preamble hash. Everything downstream inherits the fix.

**Tech Stack:** Python 3.12 + FastAPI + LangGraph (backend, `uv run pytest`); Word add-in React + TS + Vite + Office.js (frontend, `npm run typecheck`, standalone `.ts` tests via `npx tsx`). Spec: `docs/superpowers/specs/2026-07-14-canonical-document-uuid-design.md`.

## Global Constraints

- **Backward-compatible / additive.** No `document_uuid` in the request (Chainlit web client, unsaved doc, settings failure) → intake computes the preamble hash exactly as today. No caller breaks.
- **All imports at top of file** (repo hard rule #1). No lazy imports inside functions.
- **Never let `tsc` emit `.js`/`.tsbuildinfo` into `clients/word/src/`** (tsconfig `noEmit:true`; both gitignored). Frontend typecheck is `npm run typecheck` from `clients/word/`.
- **Frontend test convention:** standalone `.ts`, first line `// Run with: npx tsx src/<name>.test.ts`, helper `const pass = (cond, label) => console.log(...)`.
- **`uvicorn` does not auto-reload** — a human retesting the backend manually must restart `bash scripts/start.sh`; irrelevant to `pytest`.
- **Exact seam values:** request field `document_uuid: str = ""`; state field `document_id`; intake resolver `state["document_id"] = state.get("document_id") or resolve_document_id(text)`; Office settings key `legalTriageDocId`.
- **No migration** of existing preamble-hash review rows; **review store stays per-document**; **lazy UUID minting** (on first backend interaction).

---

### Task 1: Backend — adopt client `document_uuid` as the canonical `document_id`

**Files:**
- Modify: `graph/nodes/intake.py:39` (prefer a preset id over the hash)
- Modify: `api/models.py` (add `document_uuid` to `QueryRequest`)
- Modify: `api/routes/query.py:167` (seed `initial_state["document_id"]` from the request)
- Test: `tests/test_intake_document_id.py` (intake precedence), `tests/test_api.py` (request plumbing)

**Interfaces:**
- Consumes: existing `resolve_document_id(text)` from `memory/document_id.py`; the existing `_mock_graph_invoke` helper + `get_settings`, `patch`, `MagicMock`, `TestClient` imports already in `tests/test_api.py`.
- Produces: `QueryRequest.document_uuid: str`; `state["document_id"]` populated from the client UUID when present, else the preamble hash. (The review write and chat recall already read `state["document_id"]` — unchanged.)

- [ ] **Step 1: Write the failing intake test**

Add to `tests/test_intake_document_id.py` (the `_state` helper and imports already exist at the top of that file):

```python
def test_intake_prefers_client_supplied_document_id():
    # A client-supplied id (e.g. the Office settings UUID) wins over the hash,
    # so the id is stable even when the document text changes.
    text = "STATEMENT OF WORK\n\nAcme and Globex.\n\n1. Scope."
    state = _state(uploaded_docs=[{"text": text}], document_id="client-uuid-123")
    out = intake(state)
    assert out["document_id"] == "client-uuid-123"


def test_intake_falls_back_to_hash_when_client_id_empty():
    text = "MASTER SERVICES AGREEMENT\n\nA and B.\n\n1. Term."
    state = _state(uploaded_docs=[{"text": text}], document_id="")
    out = intake(state)
    assert out["document_id"] == resolve_document_id(text)
```

- [ ] **Step 2: Run the intake tests to verify the new one fails**

Run: `uv run pytest tests/test_intake_document_id.py -v`
Expected: `test_intake_prefers_client_supplied_document_id` FAILS — current `intake` unconditionally overwrites with `resolve_document_id(text)`, so `out["document_id"]` is the hash, not `"client-uuid-123"`. The other three pass.

- [ ] **Step 3: Change the intake resolver**

In `graph/nodes/intake.py`, change line 39 from:

```python
    state["document_id"] = resolve_document_id(text)
```

to:

```python
    # Prefer a client-supplied stable id (Office settings UUID, seeded into state
    # by the query route); fall back to the preamble hash for callers that don't
    # send one (Chainlit, unsaved docs).
    state["document_id"] = state.get("document_id") or resolve_document_id(text)
```

- [ ] **Step 4: Run the intake tests to verify they pass**

Run: `uv run pytest tests/test_intake_document_id.py -v`
Expected: all four PASS (the two new ones plus the two pre-existing, which pass a state with no `document_id` preset → `.get` is falsy → hash, unchanged).

- [ ] **Step 5: Write the failing API plumbing test**

Add to `tests/test_api.py` (reuses the module's existing `_mock_graph_invoke`, `patch`, `MagicMock`, `TestClient`, `get_settings`):

```python
def test_submit_query_passes_document_uuid_as_document_id(monkeypatch):
    """A client document_uuid reaches initial_state['document_id']."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        client.post(
            "/api/query",
            json={"request": "test", "document_uuid": "doc-uuid-abc"},
            headers={"X-User-ID": "attorney-1"},
        )

    state_arg = mock_graph.invoke.call_args.args[0]
    assert state_arg["document_id"] == "doc-uuid-abc"
```

- [ ] **Step 6: Run the API test to verify it fails**

Run: `uv run pytest tests/test_api.py::test_submit_query_passes_document_uuid_as_document_id -v`
Expected: FAIL — `QueryRequest` ignores the unknown `document_uuid` field and the route hardcodes `"document_id": ""`, so `state_arg["document_id"]` is `""`, not `"doc-uuid-abc"`.

- [ ] **Step 7: Add the model field and route plumbing**

In `api/models.py`, add the field to `QueryRequest` (after the `uploaded_text` field):

```python
    document_uuid: str = Field("", description="Client-supplied stable document id (Office custom setting); falls back to the server-side preamble hash when empty")
```

In `api/routes/query.py`, change the `initial_state` entry at line 167 from:

```python
        "document_id": "",
```

to:

```python
        "document_id": body.document_uuid,
```

- [ ] **Step 8: Run the API test + full backend suite to verify pass + no regression**

Run: `uv run pytest tests/test_api.py::test_submit_query_passes_document_uuid_as_document_id -v`
Expected: PASS.

Run: `uv run pytest -q`
Expected: all pass (no regression). Note the total for the wiki update in Task 3.

- [ ] **Step 9: Commit**

```bash
git add graph/nodes/intake.py api/models.py api/routes/query.py tests/test_intake_document_id.py tests/test_api.py
git commit -m "feat(backend): adopt client document_uuid as canonical document_id"
```

---

### Task 2: Client — mint/read the document UUID and send it

**Files:**
- Create: `clients/word/src/docIdentity.ts`
- Modify: `clients/word/src/api.ts` (`submitReview` + `chatQuery` send `document_uuid`)

**Interfaces:**
- Consumes: `Office.context.document.settings` (Office.js); the existing `postQuery` helper in `api.ts` (accepts an arbitrary `Record<string, unknown>` body).
- Produces: `resolveDocumentId(): Promise<string>` in `./docIdentity`; both API calls include a `document_uuid` field. The backend (Task 1) reads it.

> **No unit test for this task.** `resolveDocumentId` is `Office.context.document.settings` integration and there is no Office/DOM test harness in this repo (tests are pure-`.ts` only). The gate is `npm run typecheck` clean + the existing frontend suite still green + the human sideload smoke test (finishing phase). This mechanism was already **validated** during the 2026-07-14 debugging (the settings UUID survived a task-pane reopen).

- [ ] **Step 1: Create `docIdentity.ts`**

Create `clients/word/src/docIdentity.ts`:

```ts
// Stable per-document id for the backend `document_id`, stored INSIDE the .docx
// via Office.context.document.settings so it travels with the file (local,
// SharePoint, OneDrive) and is immune to content edits/redlines — unlike the
// server-side preamble hash, which drifts when the review workflow fills fields
// in the document's opening block. Validated to survive a task-pane reopen.
// Returns "" on any failure; the backend then falls back to the preamble hash.

const SETTINGS_KEY = "legalTriageDocId";

/** Read the document's stable id, creating + persisting one on first use. "" on failure. */
export async function resolveDocumentId(): Promise<string> {
  try {
    const settings = Office.context.document.settings;
    const existing = settings.get(SETTINGS_KEY);
    if (typeof existing === "string" && existing) return existing;
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `doc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    settings.set(SETTINGS_KEY, id);
    await new Promise<void>((resolve) => {
      try {
        settings.saveAsync(() => resolve());
      } catch {
        resolve();
      }
    });
    return id;
  } catch {
    return "";
  }
}
```

- [ ] **Step 2: Send `document_uuid` from both API calls**

In `clients/word/src/api.ts`, add the import at the top (with the other imports):

```ts
import { resolveDocumentId } from "./docIdentity";
```

Change `submitReview` to resolve and include the id:

```ts
export async function submitReview(docText: string, sessionId: string): Promise<QueryResponse> {
  const document_uuid = await resolveDocumentId();
  return postQuery({
    request: "Review this contract.",
    task_type: "contract_review",
    session_id: sessionId,
    filters: { client_id: "internal" },
    uploaded_text: docText,
    document_uuid,
  });
}
```

Change `chatQuery` the same way:

```ts
export async function chatQuery(
  question: string,
  docText: string,
  sessionId: string,
): Promise<QueryResponse> {
  const document_uuid = await resolveDocumentId();
  return postQuery({
    request: question,
    task_type: "research",
    session_id: sessionId,
    filters: { client_id: "internal" },
    uploaded_text: docText,
    document_uuid,
  });
}
```

- [ ] **Step 3: Typecheck**

Run (from `clients/word/`): `npm run typecheck`
Expected: no output (exit 0). Confirm `git status` shows only `src/docIdentity.ts` and `src/api.ts`, and no `.js`/`.tsbuildinfo` under `src/`.

- [ ] **Step 4: Regression — run the full frontend unit suite**

Run (from `clients/word/`):

```bash
for f in src/*.test.ts; do echo "== $f =="; npx tsx "$f"; done
```

Expected: every line `PASS`, no `FAIL` (this task adds no unit test; the existing 154 asserts must stay green). Report the observed PASS count.

- [ ] **Step 5: Commit**

```bash
git add clients/word/src/docIdentity.ts clients/word/src/api.ts
git commit -m "feat(word): send document-embedded UUID as document_uuid"
```

- [ ] **Step 6: Manual smoke checklist (required before merge — the human gate)**

Sideload in Word for Mac (`cd clients/word && npm run dev`). Verify the fix end-to-end:
1. Review a document, then in Chat ask a follow-up about the review → the prior review is recalled (baseline).
2. **Fill a placeholder in the document's opening block** (e.g. the receiving-party `[legal name]` or the effective date), then ask another chat follow-up about the review → the review is **still recalled** (previously it silently dropped — this is the fix).
3. Sanity: a fresh document with no prior review → chat works, no error (UUID minted, no stale recall).

---

### Task 3: Update `docs/wiki.md`

**Files:**
- Modify: `docs/wiki.md` (header line 3; "Shipped Since Last Update" table ~line 490; the "Office.js custom-document-property document_id" follow-up row + add slice-2/slice-3 follow-ups)

**Interfaces:**
- Consumes: nothing (documentation).
- Produces: nothing code-facing.

- [ ] **Step 1: Locate the anchors**

Run: `grep -n "Last updated:\|Shipped Since Last Update\|Office.js custom-document-property\|## Follow-ups" docs/wiki.md`
Use the printed line numbers to target the exact rows in the steps below.

- [ ] **Step 2: Update the header line (line 3)**

Change `Last updated: 2026-07-10` → `Last updated: 2026-07-14`, and update the pytest total to the number observed in Task 1 Step 8 (was `271 tests`; expected ~`274 tests` after +3 backend tests — use the actual observed count). Append to the end of the trailer: ` + canonical document UUID (review-store identity)`.

- [ ] **Step 3: Add a "Shipped Since Last Update" row**

Append to the table under `## Shipped Since Last Update (2026-05-15)` (header `| Feature | Commit / Branch | Notes |`), as the last row before `## Follow-ups / Roadmap`:

```markdown
| **Canonical document UUID (review-store identity)** | `feat/canonical-document-uuid` | Foundation slice of the server-side multi-attorney chat-continuity initiative. The Word add-in mints/reads a UUID in `Office.context.document.settings` (`legalTriageDocId`, new `docIdentity.ts`) and sends it as `document_uuid`; `QueryRequest` carries it, `query.py` seeds `initial_state["document_id"]`, and the single `intake.py` resolver now prefers it over the preamble hash (`state.get("document_id") or resolve_document_id(text)`). Because the review write (`memory_writer`) and chat recall (`legal_research`) already read `state["document_id"]`, both inherit the fix: **review recall now survives placeholder fills / redlines** — the fragility caught in the 2026-07-14 sideload smoke of the reverted localStorage branch (preamble hash drifted `05c297…`→`fae237…` once the opening block was filled). Backward-compatible: no `document_uuid` (Chainlit, unsaved docs) → preamble-hash fallback, unchanged. No migration of old rows; review store stays per-document. +3 backend tests. Spec: `docs/superpowers/specs/2026-07-14-canonical-document-uuid-design.md`; plan: `docs/superpowers/plans/2026-07-14-canonical-document-uuid.md`. **Sideload smoke-test required before merge.** |
```

- [ ] **Step 4: Resolve the "custom-document-property document_id" follow-up + add the next-slice follow-ups**

Find the follow-up row beginning `| Office.js custom-document-property` and replace that entire line with the resolved form plus the two next-slice follow-ups:

```markdown
| ~~Office.js custom-document-property `document_id`~~ | ~~Low (measured-need)~~ | **DONE (adapted)** — shipped in `feat/canonical-document-uuid` as an `Office.context.document.settings` UUID (`legalTriageDocId`) rather than a custom *property*; same durable, in-file, edit-immune identity. Now the canonical `document_id` (client-supplied, preamble-hash fallback). |
| Server-side per-attorney conversation store | Medium | Slice 2 of the chat-continuity initiative. Persist rendered conversations on the VM keyed by `(document_uuid, attorney_id)`, append-per-turn, fetch-on-open + render — delivers multi-attorney, cross-device chat continuity through the shared VM backend. Depends on the canonical UUID (shipped) + attorney identity (below). Needs its own brainstorm/spec. |
| O365 SSO attorney identity | Medium | Slice 3. Real `attorney_id` for per-attorney threads via Office SSO (`getAccessToken`); today `X-User-ID` is `word-addin` (not per-user). Stub locally until scale. Prerequisite for the per-attorney keying in slice 2. |
```

- [ ] **Step 5: Verify the edits landed**

Run: `grep -n "2026-07-14\|canonical-document-uuid\|~~Office.js custom-document-property\|per-attorney conversation store\|O365 SSO attorney" docs/wiki.md`
Expected: matches on the header, the shipped row, the struck-through resolved follow-up, and the two new follow-up rows — with no leftover un-struck `| Office.js custom-document-property` line.

- [ ] **Step 6: Commit**

```bash
git add docs/wiki.md
git commit -m "docs(wiki): ship canonical document UUID; resolve + add identity follow-ups"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Spec "Components" #1 `docIdentity.ts` → Task 2 Step 1; #2 `api.ts` → Task 2 Step 2; #3 `QueryRequest` → Task 1 Step 7; #4 `query.py` → Task 1 Step 7; #5 `intake.py` resolver → Task 1 Step 3.
- Spec "Default decisions" (fallback, no migration, per-document, lazy mint) → Global Constraints + the `or`-fallback (Task 1 Step 3) + the fallback test (Task 1 Step 1, `..._empty`) + Task 3 notes.
- Spec "Testing" (intake precedence, request plumbing, client smoke) → Task 1 Steps 1/5, Task 2 Step 6.
- Spec "Edge cases" (unsaved/settings failure → `""` → fallback) → `resolveDocumentId` `catch → ""` (Task 2 Step 1) + intake fallback (Task 1 Step 3).
- Spec "Non-goals" (conversation store, SSO, Chainlit UUID, no migration) → not implemented; recorded as follow-ups (Task 3 Step 4).

**2. Placeholder scan** — no `TBD`/`TODO`/"handle edge cases"/"similar to"; every code step shows complete code; every run step has the exact command + expected output. The one deliberately-observed value (pytest total in Task 3 Step 2) is explicitly "use the actual observed count."

**3. Type consistency** — `document_uuid` (request field, `str`) is defined in Task 1 Step 7 and sent under the same name in Task 2 Step 2. `state["document_id"]` is the single state key throughout. `resolveDocumentId(): Promise<string>` is defined in Task 2 Step 1 and awaited identically in both `api.ts` call sites (Task 2 Step 2). The intake resolver expression matches the spec verbatim.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-14-canonical-document-uuid.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — batch execution in this session with checkpoints.

**Which approach?**
