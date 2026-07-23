# Attorney Preference Memory (`USER.md`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each attorney a durable, human-owned `USER.md` preference file the agent reads as grounding on every review/chat turn, edits in a new Word "Preferences" tab, and (on explicit request) suggests additions to.

**Architecture:** Backend stores one `USER.md` per attorney (`memory/preferences.py`), assembled into a prompt block by the shared `skills/grounding.py`, injected **early / never last** into both the chat (`_run_doc_chat`) and review (`contract_review`) system messages so it stays subordinate to the playbook. Two new endpoints (`GET`/`PUT /api/preferences`) serve the file, keyed by the existing `resolve_user_id` seam. The Word add-in gains a Preferences tab (the cabinet) and a chat suggestion card. Explicit capture rides a ` ```preference ` block parsed in code (mirrors the existing `proposed_edits` pipeline).

**Tech Stack:** Python 3.12 / FastAPI / Pydantic-settings / pytest (backend); React + Vite + TS, tsx test scripts (Word add-in).

**Spec:** `docs/superpowers/specs/2026-07-21-attorney-preference-memory-design.md`

## Global Constraints

- **All imports at top of file.** No lazy imports inside functions.
- **SKILL.md/playbook is the ceiling.** Preferences shape emphasis only, never override a rating or firm policy — enforced by `_PREFERENCES_DIRECTIVE` + **early** injection (never the last system message; playbook / `_OUTPUT_CONSTRAINTS` / `_MSA_COMPARISON_DIRECTIVE` / No-Signature gate keep final authority).
- **Best-effort grounding.** A preference read/parse failure must NEVER break a turn and must NOT set `memory_degraded` (soft enhancement, not a core store).
- **Path safety.** `attorney_id` becomes a directory name — allow only `[A-Za-z0-9_-]`, reject otherwise.
- **Identity via the existing seam.** `attorney_id = state["user_id"] = resolve_user_id(...)`. No new identity mechanism.
- **No backwards-compat shims.** New feature; change call sites directly.
- **Config-gated:** `preferences_enabled` (default `True`) → off means endpoints inert + no injection.
- **The one prompt change is an output-contract rule** (define the `preference` block), the same category as the existing edit-JSON instruction — not model coaching. All parsing stays in code (`[[feedback-fix-in-code-not-prompt]]`).
- **Restart `bash scripts/start.sh`** after backend changes — `uvicorn` does not auto-reload.
- Full backend suite is **347** at branch base; every task keeps it green.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `config.py` | modify | 3 settings: `preferences_enabled`, `preferences_dir`, `preferences_max_chars` |
| `memory/preferences.py` | create | raw file IO — load/save `USER.md`, sanitize id |
| `skills/grounding.py` | modify | `load_attorney_preferences_block` + `preferences_block_for_state` + directive |
| `api/models.py` | modify | `PreferencesUpdate` request model |
| `api/routes/preferences.py` | create | `GET`/`PUT /api/preferences` |
| `api/main.py` | modify | register `preferences_router` |
| `skills/legal_research.py` | modify | inject prefs (chat) + parse `preference` blocks + prompt rule |
| `skills/contract_review/contract_review.py` | modify | inject prefs (review), never last |
| `graph/state.py` | modify | `proposed_preferences: list[str]` |
| `graph/nodes/output_formatter.py` | modify | surface `report["proposed_preferences"]` |
| `clients/word/src/preferences.ts` | create | `getPreferences` / `savePreferences` / `appendPreference` |
| `clients/word/src/parsePreferenceBlocks.ts` (+`.test.ts`) | create | extract `preference` blocks |
| `clients/word/src/components/PreferencesTab.tsx` | create | the cabinet (view/edit/save) |
| `clients/word/src/components/PreferenceSuggestionCard.tsx` | create | chat suggestion card |
| `clients/word/src/components/Tabs.tsx` | modify | + `preferences` tab |
| `clients/word/src/App.tsx` | modify | mount PreferencesTab + wire suggestion refresh |
| `clients/word/src/components/ChatTab.tsx` | modify | render suggestion cards → one-click Add |
| `clients/word/src/api.ts` | modify | report type += `proposed_preferences?: string[]` |

Tasks 1–6 are backend (pytest, offline). Tasks 7–8 are frontend (tsx test scripts where pure, `npm run typecheck`, + a Word sideload smoke).

---

### Task 1: Config + preference storage

**Files:**
- Modify: `config.py` (after line 64, the `conversation_max_messages` line)
- Create: `memory/preferences.py`
- Test: `tests/test_preferences_store.py`

**Interfaces:**
- Produces: `load_preferences(base_dir, attorney_id) -> str`, `save_preferences(base_dir, attorney_id, markdown) -> None`, `PreferenceTooLargeError`, `_safe_attorney_id(attorney_id) -> str`.

- [ ] **Step 1: Write the failing test** — `tests/test_preferences_store.py`

```python
import pytest

from memory.preferences import (
    PreferenceTooLargeError,
    _MAX_WRITE_CHARS,
    load_preferences,
    save_preferences,
)


def test_save_then_load_round_trip(tmp_path):
    save_preferences(str(tmp_path), "atty-1", "# Prefs\n- Always flag uncapped indemnity.")
    assert load_preferences(str(tmp_path), "atty-1") == "# Prefs\n- Always flag uncapped indemnity."


def test_load_missing_returns_empty(tmp_path):
    assert load_preferences(str(tmp_path), "nobody") == ""


def test_save_creates_nested_dir(tmp_path):
    base = tmp_path / "does" / "not" / "exist"
    save_preferences(str(base), "atty-2", "hi")
    assert (base / "atty-2" / "USER.md").read_text(encoding="utf-8") == "hi"


def test_two_attorneys_isolated(tmp_path):
    save_preferences(str(tmp_path), "a", "alpha")
    save_preferences(str(tmp_path), "b", "beta")
    assert load_preferences(str(tmp_path), "a") == "alpha"
    assert load_preferences(str(tmp_path), "b") == "beta"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", "with space", "semi;colon"])
def test_unsafe_attorney_id_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        load_preferences(str(tmp_path), bad)
    with pytest.raises(ValueError):
        save_preferences(str(tmp_path), bad, "x")


def test_oversize_write_rejected(tmp_path):
    with pytest.raises(PreferenceTooLargeError):
        save_preferences(str(tmp_path), "atty-3", "x" * (_MAX_WRITE_CHARS + 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preferences_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.preferences'`.

- [ ] **Step 3: Create `memory/preferences.py`**

```python
"""Per-attorney preference memory — a plain `USER.md` file per attorney.

Stage 1 of the self-improving agent harness. The attorney OWNS the file (edits it
in the Word Preferences tab); the agent only READS it as grounding and SUGGESTS
additions the attorney commits. Because the human is the only writer, a raw
markdown file is safe — no concurrent agent writes, no LLM-regeneration drift.

Mirrors memory/review_store.py / memory/conversation_store.py: plain IO, base dir
passed per call. Storage only — prompt assembly lives in skills/grounding.py.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_FILENAME = "USER.md"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_WRITE_CHARS = 20000  # hard ceiling on a stored USER.md (abuse guard)


class PreferenceTooLargeError(Exception):
    """A save exceeded _MAX_WRITE_CHARS."""


def _safe_attorney_id(attorney_id: str) -> str:
    """Return attorney_id if it is a safe single path segment, else raise.

    attorney_id becomes a directory name; allow only [A-Za-z0-9_-] so it can
    never escape base_dir (no '..', no '/', no spaces). It is a UUID / oid in
    practice.
    """
    if not attorney_id or not _SAFE_ID_RE.match(attorney_id):
        raise ValueError(f"unsafe attorney_id: {attorney_id!r}")
    return attorney_id


def _user_md_path(base_dir: str, attorney_id: str) -> Path:
    return Path(base_dir) / _safe_attorney_id(attorney_id) / _FILENAME


def load_preferences(base_dir: str, attorney_id: str) -> str:
    """The attorney's USER.md contents, or '' when absent. Never raises on a
    missing file; raises ValueError only on an unsafe attorney_id."""
    path = _user_md_path(base_dir, attorney_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_preferences(base_dir: str, attorney_id: str, markdown: str) -> None:
    """Replace the attorney's USER.md (last-write-wins); creates the dir. Raises
    PreferenceTooLargeError over the hard cap, ValueError on an unsafe id."""
    if len(markdown) > _MAX_WRITE_CHARS:
        raise PreferenceTooLargeError(
            f"preferences {len(markdown)} chars exceed limit {_MAX_WRITE_CHARS}"
        )
    path = _user_md_path(base_dir, attorney_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    logger.info("Preferences saved: attorney_id=%s (%d chars)", attorney_id, len(markdown))
```

- [ ] **Step 4: Add config settings** — `config.py`, immediately after line 64 (`conversation_max_messages`):

```python
    # Attorney preference memory (USER.md) — stage 1 of the self-improving harness
    preferences_enabled: bool = True          # per-attorney USER.md; False = no store/injection
    preferences_dir: str = "data/attorneys"   # USER.md at <preferences_dir>/<attorney_id>/USER.md
    preferences_max_chars: int = 8000         # cap on prefs injected into a prompt (counts to chat budget)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_preferences_store.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add config.py memory/preferences.py tests/test_preferences_store.py
git commit -m "feat: per-attorney USER.md preference store + config"
```

---

### Task 2: Grounding assembly

**Files:**
- Modify: `skills/grounding.py`
- Test: `tests/test_preferences_grounding.py`

**Interfaces:**
- Consumes: `memory.preferences.load_preferences` (Task 1); `config.get_settings`.
- Produces:
  - `load_attorney_preferences_block(attorney_id, base_dir, max_chars) -> str` — pure; wraps in directive, truncates, `""` on empty/error.
  - `preferences_block_for_state(state) -> str` — settings-gated, pulls `state["user_id"]`. Both chat and review call this.

- [ ] **Step 1: Write the failing test** — `tests/test_preferences_grounding.py`

```python
from config import get_settings
from skills.grounding import (
    _PREFERENCES_DIRECTIVE,
    load_attorney_preferences_block,
    preferences_block_for_state,
)
from memory.preferences import save_preferences


def test_block_empty_when_no_file(tmp_path):
    assert load_attorney_preferences_block("atty-x", str(tmp_path), 8000) == ""


def test_block_wraps_directive(tmp_path):
    save_preferences(str(tmp_path), "atty-x", "- Always flag uncapped indemnity.")
    block = load_attorney_preferences_block("atty-x", str(tmp_path), 8000)
    assert _PREFERENCES_DIRECTIVE.strip()[:20] in block
    assert "Always flag uncapped indemnity" in block
    assert "END ATTORNEY PREFERENCES" in block


def test_block_truncates(tmp_path):
    save_preferences(str(tmp_path), "atty-x", "y" * 500)
    block = load_attorney_preferences_block("atty-x", str(tmp_path), 100)
    assert "truncated to 100" in block
    assert block.count("y") <= 100


def test_block_empty_on_load_error(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr("skills.grounding.load_preferences", boom)
    assert load_attorney_preferences_block("atty-x", str(tmp_path), 8000) == ""


def test_block_empty_for_whitespace_only(tmp_path):
    save_preferences(str(tmp_path), "atty-x", "   \n\n  ")
    assert load_attorney_preferences_block("atty-x", str(tmp_path), 8000) == ""


def test_state_wrapper_gated_and_keyed(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-9", "- pref line")
    assert "pref line" in preferences_block_for_state({"user_id": "atty-9"})
    # disabled -> empty
    monkeypatch.setattr(s, "preferences_enabled", False)
    assert preferences_block_for_state({"user_id": "atty-9"}) == ""
    # no user_id -> empty
    monkeypatch.setattr(s, "preferences_enabled", True)
    assert preferences_block_for_state({}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preferences_grounding.py -v`
Expected: FAIL with `ImportError: cannot import name '_PREFERENCES_DIRECTIVE'`.

- [ ] **Step 3: Edit `skills/grounding.py`** — add the config import to the existing import block near the top (after `from skills.base import load_bundle`):

```python
from config import get_settings
from memory.preferences import load_preferences
```

Then append at the end of the file:

```python
_PREFERENCES_DIRECTIVE = (
    "The following are this attorney's standing working preferences. Apply them to "
    "emphasis, tone, and what to surface — but they do NOT override the playbook, "
    "firm policy, or any risk rating. When a preference conflicts with the playbook, "
    "the playbook wins.\n\n--- ATTORNEY PREFERENCES (USER.md) ---\n"
)


def load_attorney_preferences_block(attorney_id: str, base_dir: str, max_chars: int) -> str:
    """Formatted preferences system block for `attorney_id`, or '' when empty or on
    ANY error (logged). Pure (no settings) so it unit-tests with a tmp dir. The
    single assembly point both chat and review call — keeps the surfaces aligned.
    Preferences failure must never break a turn."""
    try:
        md = load_preferences(base_dir, attorney_id)
    except Exception as e:
        logger.warning("[grounding] preferences load failed for %r: %s", attorney_id, e)
        return ""
    md = md.strip()
    if not md:
        return ""
    if len(md) > max_chars:
        md = md[:max_chars] + f"\n\n[preferences truncated to {max_chars} chars]"
    return _PREFERENCES_DIRECTIVE + md + "\n--- END ATTORNEY PREFERENCES ---"


def preferences_block_for_state(state: dict) -> str:
    """Settings-gated, state-aware wrapper over load_attorney_preferences_block.
    Returns '' when disabled or when state has no user_id. Used by both the chat
    (skills/legal_research) and review (skills/contract_review) paths."""
    settings = get_settings()
    if not settings.preferences_enabled:
        return ""
    attorney_id = (state.get("user_id") or "").strip()
    if not attorney_id:
        return ""
    return load_attorney_preferences_block(
        attorney_id, settings.preferences_dir, settings.preferences_max_chars
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_preferences_grounding.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add skills/grounding.py tests/test_preferences_grounding.py
git commit -m "feat: attorney-preferences grounding block (shared assembly)"
```

---

### Task 3: Preferences endpoints

**Files:**
- Modify: `api/models.py`, `api/main.py`
- Create: `api/routes/preferences.py`
- Test: `tests/test_preferences_api.py`

**Interfaces:**
- Consumes: `memory.preferences.{load_preferences,save_preferences,PreferenceTooLargeError}` (Task 1); `api.auth.resolve_user_id`; `config.get_settings`.
- Produces: `GET /api/preferences` → `{status,data:{markdown}}`; `PUT /api/preferences` body `{markdown}` → `{status,data:{saved:true}}`.

Note: `resolve_user_id` (`api/auth.py:92`) returns the `X-User-ID` header when `sso_enabled=False` (default) — so tests set that header to pick an attorney.

- [ ] **Step 1: Write the failing test** — `tests/test_preferences_api.py`

```python
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    fake = SimpleNamespace(preferences_enabled=True, preferences_dir=str(tmp_path))
    monkeypatch.setattr("api.routes.preferences.get_settings", lambda: fake)
    return TestClient(app), fake


def test_put_then_get_round_trip(client):
    c, _ = client
    r = c.put("/api/preferences", json={"markdown": "- pref A"}, headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200 and r.json()["data"]["saved"] is True
    r = c.get("/api/preferences", headers={"X-User-ID": "atty-1"})
    assert r.json()["data"]["markdown"] == "- pref A"


def test_attorneys_isolated(client):
    c, _ = client
    c.put("/api/preferences", json={"markdown": "alpha"}, headers={"X-User-ID": "a"})
    c.put("/api/preferences", json={"markdown": "beta"}, headers={"X-User-ID": "b"})
    assert c.get("/api/preferences", headers={"X-User-ID": "a"}).json()["data"]["markdown"] == "alpha"
    assert c.get("/api/preferences", headers={"X-User-ID": "b"}).json()["data"]["markdown"] == "beta"


def test_get_missing_is_empty(client):
    c, _ = client
    assert c.get("/api/preferences", headers={"X-User-ID": "new"}).json()["data"]["markdown"] == ""


def test_disabled(client):
    c, fake = client
    fake.preferences_enabled = False
    assert c.get("/api/preferences", headers={"X-User-ID": "a"}).json()["data"]["markdown"] == ""
    assert c.put("/api/preferences", json={"markdown": "x"}, headers={"X-User-ID": "a"}).status_code == 403


def test_oversize_413(client):
    c, _ = client
    r = c.put("/api/preferences", json={"markdown": "x" * 20001}, headers={"X-User-ID": "a"})
    assert r.status_code == 413
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preferences_api.py -v`
Expected: FAIL — `GET /api/preferences` returns 404 (route not registered).

- [ ] **Step 3: Add the request model** — `api/models.py`, after `ResumeRequest`:

```python
class PreferencesUpdate(BaseModel):
    """Replace an attorney's USER.md."""
    markdown: str = Field("", description="Full markdown content of the attorney's USER.md")
```

- [ ] **Step 4: Create `api/routes/preferences.py`**

```python
# api/routes/preferences.py
"""Attorney preference memory endpoints — read/write the caller's USER.md.

Keyed by attorney_id (resolve_user_id → X-User-ID today, O365 oid when SSO on).
Stage 1 of the self-improving harness; storage in memory/preferences.py.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from api.auth import resolve_user_id
from api.models import ApiResponse, PreferencesUpdate
from config import get_settings
from memory.preferences import (
    PreferenceTooLargeError,
    load_preferences,
    save_preferences,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/preferences", response_model=ApiResponse)
def get_preferences(user_id: str = Depends(resolve_user_id)) -> ApiResponse:
    settings = get_settings()
    if not settings.preferences_enabled:
        return ApiResponse(status="ok", data={"markdown": ""})
    try:
        markdown = load_preferences(settings.preferences_dir, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(status="ok", data={"markdown": markdown})


@router.put("/preferences", response_model=ApiResponse)
def put_preferences(
    body: PreferencesUpdate, user_id: str = Depends(resolve_user_id)
) -> ApiResponse:
    settings = get_settings()
    if not settings.preferences_enabled:
        raise HTTPException(status_code=403, detail="preferences are disabled")
    try:
        save_preferences(settings.preferences_dir, user_id, body.markdown)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PreferenceTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    return ApiResponse(status="ok", data={"saved": True})
```

- [ ] **Step 5: Register the router** — `api/main.py`, alongside the existing imports (after line 50) and includes (after line 54):

```python
from api.routes.preferences import router as preferences_router
```
```python
app.include_router(preferences_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_preferences_api.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add api/models.py api/routes/preferences.py api/main.py tests/test_preferences_api.py
git commit -m "feat: GET/PUT /api/preferences endpoints"
```

---

### Task 4: Inject preferences into the chat path

**Files:**
- Modify: `skills/legal_research.py`
- Test: `tests/test_preferences_chat_injection.py`

**Interfaces:**
- Consumes: `skills.grounding.preferences_block_for_state` (Task 2).
- Produces: a preferences system message in `_run_doc_chat`, positioned **right after `CHAT_SYSTEM_PROMPT`** (before playbook / MSA / review — so it is early and subordinate).

- [ ] **Step 1: Write the failing test** — `tests/test_preferences_chat_injection.py`

```python
from config import get_settings
from memory.preferences import save_preferences
from skills import legal_research


def _capture(monkeypatch):
    """Monkeypatch the LLM call to capture the assembled messages."""
    seen = {}

    class _Resp:
        content = "Two directors sign per Section 9."

    def fake_invoke(llm, messages, name=None):
        seen["messages"] = messages
        return _Resp()

    monkeypatch.setattr(legal_research, "traced_invoke", fake_invoke)
    return seen


def test_preferences_injected_after_system_prompt(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-77", "- Always flag uncapped indemnity.")
    seen = _capture(monkeypatch)

    state = {
        "request": "Who signs this?",           # plain question → grounding gate off
        "user_id": "atty-77",
        "filters": {},
        "chat_history": [],
    }
    legal_research._run_doc_chat(state, "MUTUAL NON-DISCLOSURE AGREEMENT\n\n1. Term ...")

    systems = [m for m in seen["messages"] if m["role"] == "system"]
    assert "ATTORNEY PREFERENCES" in systems[1]["content"]          # right after CHAT_SYSTEM_PROMPT
    assert "Always flag uncapped indemnity" in systems[1]["content"]
    # never the last message (the user doc message is last)
    assert seen["messages"][-1]["role"] == "user"


def test_no_preferences_when_disabled(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", False)
    save_preferences(str(tmp_path), "atty-77", "- pref")
    seen = _capture(monkeypatch)

    state = {"request": "Who signs?", "user_id": "atty-77", "filters": {}, "chat_history": []}
    legal_research._run_doc_chat(state, "MUTUAL NON-DISCLOSURE AGREEMENT")
    assert all("ATTORNEY PREFERENCES" not in m["content"] for m in seen["messages"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preferences_chat_injection.py -v`
Expected: FAIL — no `ATTORNEY PREFERENCES` message.

- [ ] **Step 3: Import the helper** — `skills/legal_research.py:21`, extend the grounding import:

```python
from skills.grounding import (
    attach_parent_msa,
    detect_contract_type,
    load_playbook_bundle,
    preferences_block_for_state,
)
```

- [ ] **Step 4: Inject in `_run_doc_chat`** — replace the `system_messages` construction (currently line 781):

```python
    system_messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    prefs_block = preferences_block_for_state(state)
    if prefs_block:                     # early → subordinate to playbook/review (ceiling intact)
        system_messages.append({"role": "system", "content": prefs_block})
    if playbook:
        system_messages.append({"role": "system", "content": playbook})
    if msa_block:
        system_messages.append({"role": "system", "content": msa_block})
    if review_block:
        system_messages.append({"role": "system", "content": review_block})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_preferences_chat_injection.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add skills/legal_research.py tests/test_preferences_chat_injection.py
git commit -m "feat: inject attorney preferences into the chat path (early, subordinate)"
```

---

### Task 5: Inject preferences into the review path

**Files:**
- Modify: `skills/contract_review/contract_review.py`
- Test: `tests/test_preferences_review_injection.py`

**Interfaces:**
- Consumes: `skills.grounding.preferences_block_for_state` (Task 2).
- Produces: a preferences system message inserted **after the playbook, before `_OUTPUT_CONSTRAINTS`** — never last; `_MSA_COMPARISON_DIRECTIVE` remains the final system message when an MSA is attached.

- [ ] **Step 1: Write the failing test** — `tests/test_preferences_review_injection.py`

```python
from config import get_settings
from memory.preferences import save_preferences
from skills.contract_review import contract_review as cr


def test_preferences_after_playbook_before_output_constraints(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-5", "- Always flag uncapped indemnity.")

    state = {
        "request": "Review this contract.",
        "user_id": "atty-5",
        "uploaded_docs": [{"text": "MUTUAL NON-DISCLOSURE AGREEMENT\n\n1. Term ..."}],
        "filters": {},
    }
    out = cr.contract_review(state)
    systems = [m["content"] for m in out["messages"] if m["role"] == "system"]
    pref_idx = next(i for i, c in enumerate(systems) if "ATTORNEY PREFERENCES" in c)
    oc_idx = next(i for i, c in enumerate(systems) if c == cr._OUTPUT_CONSTRAINTS)
    assert 0 < pref_idx < oc_idx           # after playbook (index 0), before output constraints
    assert out["messages"][-1]["role"] == "user"   # prefs never last


def test_no_preferences_when_empty(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    state = {
        "request": "Review this contract.",
        "user_id": "atty-empty",
        "uploaded_docs": [{"text": "MUTUAL NON-DISCLOSURE AGREEMENT"}],
        "filters": {},
    }
    out = cr.contract_review(state)
    assert all("ATTORNEY PREFERENCES" not in m["content"] for m in out["messages"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preferences_review_injection.py -v`
Expected: FAIL — no `ATTORNEY PREFERENCES` message.

- [ ] **Step 3: Import the helper** — `skills/contract_review/contract_review.py:22`, extend the grounding import:

```python
from skills.grounding import (
    attach_parent_msa,
    detect_contract_type,
    load_playbook_bundle,
    preferences_block_for_state,
)
```

- [ ] **Step 4: Insert prefs into `system_messages`** — replace the block at lines 180–185:

```python
    system_messages = [{"role": "system", "content": playbook}]
    prefs_block = preferences_block_for_state(state)
    if prefs_block:                 # after playbook, before output constraints — never last
        system_messages.append({"role": "system", "content": prefs_block})
    system_messages.append({"role": "system", "content": _OUTPUT_CONSTRAINTS})
    if msa_attached:
        system_messages.append({"role": "system", "content": _MSA_COMPARISON_DIRECTIVE})
    state["messages"] = system_messages + [{"role": "user", "content": user_content}]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_preferences_review_injection.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add skills/contract_review/contract_review.py tests/test_preferences_review_injection.py
git commit -m "feat: inject attorney preferences into the review path (never last)"
```

---

### Task 6: Explicit preference suggestion (capture)

**Files:**
- Modify: `skills/legal_research.py` (prompt rule + parser + set state), `graph/state.py`, `graph/nodes/output_formatter.py`
- Test: `tests/test_preference_suggestion.py`

**Interfaces:**
- Produces: `_extract_proposed_preferences(prose) -> list[str]`; `state["proposed_preferences"]` set by `legal_research` on the doc-chat path; `report["proposed_preferences"]`.

- [ ] **Step 1: Write the failing test** — `tests/test_preference_suggestion.py`

```python
from graph.nodes.output_formatter import output_formatter
from skills.legal_research import _extract_proposed_preferences


def test_extract_single_line():
    prose = "Sure, I'll remember that.\n```preference\nAlways flag uncapped indemnity as Red.\n```"
    assert _extract_proposed_preferences(prose) == ["Always flag uncapped indemnity as Red."]


def test_extract_multiple_lines_and_strips_bullets():
    prose = "```preference\n- Delaware governing law fallback.\n- Surface auto-renewal.\n```"
    assert _extract_proposed_preferences(prose) == [
        "Delaware governing law fallback.",
        "Surface auto-renewal.",
    ]


def test_extract_none_when_no_block():
    assert _extract_proposed_preferences("Just a normal answer, no block.") == []


def test_extract_ignores_json_edit_block():
    prose = '```json\n{"action":"replace","target_text":"a","new_text":"b"}\n```'
    assert _extract_proposed_preferences(prose) == []


def test_output_formatter_surfaces_preferences():
    state = {"task_type": "research", "proposed_preferences": ["p1"]}
    out = output_formatter(state)
    assert out["report"]["proposed_preferences"] == ["p1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preference_suggestion.py -v`
Expected: FAIL — `_extract_proposed_preferences` undefined; report lacks the key.

- [ ] **Step 3: Add the parser** — `skills/legal_research.py`, near `_extract_proposed_edits` (after line 353). Add a module-level regex beside `_JSON_BLOCK_RE` (line 211):

```python
_PREFERENCE_BLOCK_RE = re.compile(r"```preference\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_proposed_preferences(prose: str) -> list[str]:
    """Pull ```preference``` fenced blocks into individual preference lines.

    Plain text, one preference per line (a leading '-'/'*' bullet is stripped) —
    deliberately NOT JSON, to avoid the edit-block parsing fragility. Non-fatal:
    no block → []. The suggestion the attorney approves; not a document edit.
    """
    prefs: list[str] = []
    for match in _PREFERENCE_BLOCK_RE.finditer(prose or ""):
        for line in match.group(1).splitlines():
            t = re.sub(r"^\s*[-*]\s+", "", line).strip()
            if t:
                prefs.append(t)
    return prefs
```

- [ ] **Step 4: Set state in `legal_research`** — in the doc-chat branch (after `state["proposed_edits"] = edits`, ~line 899) add:

```python
            state["proposed_preferences"] = _extract_proposed_preferences(content)
```

Also reset it with the other reset (after line 893 `state["proposed_edits"] = []`):

```python
    state["proposed_preferences"] = []
```

- [ ] **Step 5: Add the state field** — `graph/state.py`, in `LegalAgentState` (after the `proposed_edits` line):

```python
    proposed_preferences: list[str]        # NEW — plain-text preference suggestions parsed from chat output
```

- [ ] **Step 6: Surface in the report** — `graph/nodes/output_formatter.py`, add to the `state["report"]` dict (after the `proposed_edits` line):

```python
        "proposed_preferences": state.get("proposed_preferences", []),
```

- [ ] **Step 7: Add the prompt rule** — `skills/legal_research.py`, append to `CHAT_SYSTEM_PROMPT` (after line 89, inside the triple-quoted string, as a new trailing section):

```

REMEMBERING PREFERENCES (only when explicitly asked):
If — and ONLY if — the user explicitly asks you to remember a standing preference for the future (e.g. "always flag…", "remember that I want…", "from now on…"), then in addition to your normal answer, end your reply with a fenced ```preference``` block containing the preference as ONE short imperative line (use several lines for several preferences). Do NOT emit this block for one-off requests, ordinary questions, or edits, and NEVER propose a preference that contradicts the playbook or firm policy. This block is a suggestion the attorney approves — it does not change the current document.
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_preference_suggestion.py -v`
Expected: PASS (5 tests).

- [ ] **Step 9: Run the full backend suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (347 prior + new tests).

- [ ] **Step 10: Commit**

```bash
git add skills/legal_research.py graph/state.py graph/nodes/output_formatter.py tests/test_preference_suggestion.py
git commit -m "feat: explicit preference suggestion — parse ```preference blocks into report"
```

---

### Task 7: Word add-in — Preferences tab (the cabinet)

**Files:**
- Create: `clients/word/src/preferences.ts`, `clients/word/src/components/PreferencesTab.tsx`
- Modify: `clients/word/src/components/Tabs.tsx`, `clients/word/src/App.tsx`
- Verify: `cd clients/word && npm run typecheck` + Word sideload smoke

**Interfaces:**
- Consumes: `resolveAttorneyId` (`attorneyIdentity.ts`); backend `GET`/`PUT /api/preferences` (Task 3).
- Produces: `getPreferences()`, `savePreferences(md)`, `appendPreference(line)` (used by Task 8); a `preferences` tab.

- [ ] **Step 1: Create `clients/word/src/preferences.ts`**

```ts
// Preferences API client — reads/writes the attorney's USER.md via the backend.
// Keyed by the same X-User-ID attorney identity the query client sends.
import { resolveAttorneyId } from "./attorneyIdentity";

export async function getPreferences(): Promise<string> {
  const res = await fetch("/api/preferences", {
    headers: { "X-User-ID": resolveAttorneyId() },
  });
  if (!res.ok) throw new Error(`Backend returned ${res.status} ${res.statusText}`);
  const json = await res.json();
  return (json?.data?.markdown as string) ?? "";
}

export async function savePreferences(markdown: string): Promise<void> {
  const res = await fetch("/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-User-ID": resolveAttorneyId() },
    body: JSON.stringify({ markdown }),
  });
  if (!res.ok) throw new Error(`Backend returned ${res.status} ${res.statusText}`);
}

/** Append one preference line to the stored USER.md (GET → append → PUT). */
export async function appendPreference(line: string): Promise<void> {
  const current = (await getPreferences()).replace(/\s+$/, "");
  const next = (current ? current + "\n" : "") + `- ${line}`;
  await savePreferences(next);
}
```

- [ ] **Step 2: Add the tab key** — `clients/word/src/components/Tabs.tsx`, replace lines 1–11:

```ts
export type TabKey = "findings" | "chat" | "preferences";

interface Props {
  active: TabKey;
  onChange: (key: TabKey) => void;
}

const TABS: { key: TabKey; label: string }[] = [
  { key: "findings", label: "Findings" },
  { key: "chat", label: "Chat" },
  { key: "preferences", label: "Preferences" },
];
```

- [ ] **Step 3: Create `clients/word/src/components/PreferencesTab.tsx`**

```tsx
import { useEffect, useState } from "react";
import { getPreferences, savePreferences } from "../preferences";

interface Props {
  markdown: string;
  setMarkdown: React.Dispatch<React.SetStateAction<string>>;
  loaded: boolean;
  setLoaded: React.Dispatch<React.SetStateAction<boolean>>;
}

export default function PreferencesTab({ markdown, setMarkdown, loaded, setLoaded }: Props) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch once (and again whenever `loaded` is reset to false — e.g. after the
  // Chat tab appends a suggested preference, so the cabinet reflects it).
  useEffect(() => {
    if (loaded) return;
    (async () => {
      try {
        setMarkdown(await getPreferences());
        setLoaded(true);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [loaded, setMarkdown, setLoaded]);

  const save = async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await savePreferences(markdown);
      setStatus("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tab-content preferences">
      <p className="subtitle">
        Your standing preferences (USER.md). The assistant reads these on every review and chat —
        they shape emphasis but never override the firm playbook.
      </p>
      <textarea
        className="preferences-editor"
        rows={16}
        value={markdown}
        onChange={(e) => setMarkdown(e.target.value)}
        placeholder={"# My preferences\n- Always flag uncapped indemnity as Red.\n- Governing-law fallback is Delaware."}
        disabled={busy}
      />
      <div className="preferences-actions">
        <button className="primary" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        {status && <span className="status">{status}</span>}
        {error && <span className="status error">Error: {error}</span>}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Mount the tab** — `clients/word/src/App.tsx`. Add the import (after the `ChatTab` import):

```tsx
import PreferencesTab from "./components/PreferencesTab";
```

Add lifted state (after the `chatMessages` state, line 19):

```tsx
  const [prefMarkdown, setPrefMarkdown] = useState<string>("");
  const [prefLoaded, setPrefLoaded] = useState<boolean>(false);
```

Add the tab pane (after the chat `tab-pane` div, before `<FinalizeBar />`):

```tsx
      <div className={`tab-pane ${tab === "preferences" ? "" : "hidden"}`}>
        <PreferencesTab
          markdown={prefMarkdown}
          setMarkdown={setPrefMarkdown}
          loaded={prefLoaded}
          setLoaded={setPrefLoaded}
        />
      </div>
```

- [ ] **Step 5: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add clients/word/src/preferences.ts clients/word/src/components/PreferencesTab.tsx clients/word/src/components/Tabs.tsx clients/word/src/App.tsx
git commit -m "feat(word): Preferences tab (cabinet) for USER.md view/edit/save"
```

---

### Task 8: Word add-in — chat suggestion card (one-click Add)

**Files:**
- Create: `clients/word/src/parsePreferenceBlocks.ts`, `clients/word/src/parsePreferenceBlocks.test.ts`, `clients/word/src/components/PreferenceSuggestionCard.tsx`
- Modify: `clients/word/src/api.ts`, `clients/word/src/components/ChatTab.tsx`, `clients/word/src/App.tsx`
- Verify: `npx tsx src/parsePreferenceBlocks.test.ts`, `npm run typecheck`, Word sideload smoke

**Interfaces:**
- Consumes: `appendPreference` (Task 7); backend `report.proposed_preferences` (Task 6).
- Produces: preference suggestion cards in the Chat tab; `App` passes `onPreferenceAdded` so the Preferences tab reloads.

- [ ] **Step 1: Write the failing test** — `clients/word/src/parsePreferenceBlocks.test.ts`

```ts
// Run with: npx tsx src/parsePreferenceBlocks.test.ts
import { extractPreferenceBlocks } from "./parsePreferenceBlocks";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

{
  const { cleanedProse, preferences } = extractPreferenceBlocks(
    "Noted.\n```preference\nAlways flag uncapped indemnity.\n```",
  );
  pass(preferences.length === 1 && preferences[0] === "Always flag uncapped indemnity.", "single");
  pass(!cleanedProse.includes("```"), "block stripped from prose");
}
{
  const { preferences } = extractPreferenceBlocks(
    "```preference\n- Delaware fallback.\n- Surface auto-renewal.\n```",
  );
  pass(preferences.length === 2 && preferences[1] === "Surface auto-renewal.", "multi + bullets");
}
{
  const { preferences } = extractPreferenceBlocks("no block here");
  pass(preferences.length === 0, "none");
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd clients/word && npx tsx src/parsePreferenceBlocks.test.ts`
Expected: error — module not found.

- [ ] **Step 3: Create `clients/word/src/parsePreferenceBlocks.ts`**

```ts
// Extract ```preference fenced blocks from assistant prose into individual
// preference suggestions. Plain text (one preference per line, leading bullet
// stripped), NOT JSON — mirrors the backend's _extract_proposed_preferences.

const PREFERENCE_BLOCK_RE = /```preference\s*\n([\s\S]*?)```/gi;

export function extractPreferenceBlocks(
  prose: string,
): { cleanedProse: string; preferences: string[] } {
  const preferences: string[] = [];
  const cleanedProse = (prose || "")
    .replace(PREFERENCE_BLOCK_RE, (_m, body: string) => {
      for (const line of body.split("\n")) {
        const t = line.trim().replace(/^[-*]\s+/, "");
        if (t) preferences.push(t);
      }
      return "";
    })
    .trim();
  return { cleanedProse, preferences };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx tsx src/parsePreferenceBlocks.test.ts`
Expected: all `PASS`.

- [ ] **Step 5: Extend the API response type** — `clients/word/src/api.ts`, in the `report` object type (after `proposed_edits?: EditProposal[];`, line 20):

```ts
      proposed_preferences?: string[];
```

- [ ] **Step 6: Create `clients/word/src/components/PreferenceSuggestionCard.tsx`**

```tsx
import { useState } from "react";
import { appendPreference } from "../preferences";

interface Props {
  text: string;
  onAdded?: () => void;
}

export default function PreferenceSuggestionCard({ text, onAdded }: Props) {
  const [state, setState] = useState<"idle" | "saving" | "added" | "error">("idle");

  const add = async () => {
    setState("saving");
    try {
      await appendPreference(text);
      setState("added");
      onAdded?.();
    } catch {
      setState("error");
    }
  };

  return (
    <div className="preference-suggestion">
      <span className="preference-icon">💡</span>
      <span className="preference-text">Remember this preference? “{text}”</span>
      {state === "added" ? (
        <span className="status">Added ✓</span>
      ) : (
        <button className="secondary" onClick={add} disabled={state === "saving"}>
          {state === "saving" ? "Adding…" : "Add"}
        </button>
      )}
      {state === "error" && <span className="status error">Couldn't save</span>}
    </div>
  );
}
```

- [ ] **Step 7: Wire into ChatTab** — `clients/word/src/components/ChatTab.tsx`:

  (a) imports (top):

```tsx
import { extractPreferenceBlocks } from "../parsePreferenceBlocks";
import PreferenceSuggestionCard from "./PreferenceSuggestionCard";
```

  (b) extend `ChatMessage` (after `proposedEdits?`):

```tsx
  proposedPreferences?: string[];
```

  (c) add a prop for the refresh callback — extend `Props`:

```tsx
  onPreferenceAdded?: () => void;
```
and the destructure: `export default function ChatTab({ sessionId, messages, setMessages, onPreferenceAdded }: Props) {`

  (d) in `send()`, after `const proposedEdits = normalizeProposals(...)` (line 83), parse preferences (backend-first, frontend fallback), and strip the block from the displayed prose:

```tsx
      const backendPrefs = res.data?.report?.proposed_preferences ?? [];
      const { cleanedProse: prose2, preferences: fePrefs } = extractPreferenceBlocks(finalProse);
      const proposedPreferences = backendPrefs.length > 0 ? backendPrefs : fePrefs;
```
Then change the pushed message to use `prose2` for content and carry the prefs:

```tsx
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: prose2 || finalProse,
          proposedEdits: proposedEdits.length > 0 ? proposedEdits : undefined,
          proposedPreferences: proposedPreferences.length > 0 ? proposedPreferences : undefined,
          promisedEditMissing,
          rawResponse: rawAnswer,
        },
      ]);
```

  (e) render the cards (after the `proposedEdits?.map(...)` block, line 125):

```tsx
            {m.proposedPreferences?.map((p, j) => (
              <PreferenceSuggestionCard key={`pref-${i}-${j}`} text={p} onAdded={onPreferenceAdded} />
            ))}
```

- [ ] **Step 8: Pass the refresh callback** — `clients/word/src/App.tsx`, on the `<ChatTab .../>`:

```tsx
          onPreferenceAdded={() => setPrefLoaded(false)}
```
(Resetting `prefLoaded` makes PreferencesTab re-fetch, so a chat-added preference shows in the cabinet.)

- [ ] **Step 9: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add clients/word/src/parsePreferenceBlocks.ts clients/word/src/parsePreferenceBlocks.test.ts clients/word/src/components/PreferenceSuggestionCard.tsx clients/word/src/api.ts clients/word/src/components/ChatTab.tsx clients/word/src/App.tsx
git commit -m "feat(word): chat preference-suggestion card with one-click Add"
```

---

### Final: Word sideload smoke (required — frontend changed)

Per `CLAUDE.md`, `tsc --noEmit` is not enough for add-in changes. After Tasks 7–8, sideload in Word for Mac and confirm:

1. **Preferences tab loads** — open it; existing `USER.md` renders (empty first time).
2. **Edit + Save** persists — type a preference, Save, switch tabs and back → it's still there; `sqlite3`-style check: `cat data/attorneys/<attorney_id>/USER.md`.
3. **Grounding works** — with a preference like "always flag uncapped indemnity as Red," run a review / ask a chat question where it should shape emphasis; confirm it influences the answer (and does **not** override a playbook rating).
4. **Suggestion → Add** — in Chat say *"remember that I always want auto-renewal flagged"*; a 💡 card appears; click **Add**; switch to Preferences → the line is present (the reload fired).
5. **Ceiling intact** — a preference that contradicts the playbook does not flip a rating.

---

## Self-Review (author)

- **Spec coverage:** store (T1), serve (T3), edit/cabinet (T7), ground chat+review (T4/T5), explicit suggest (T6/T8), config gate + path safety + ceiling placement + best-effort (Global Constraints, exercised in T2/T4/T5). Harness explicitly out of scope (spec §10). ✅
- **Type consistency:** `preferences_block_for_state(state)` used identically in T4/T5; `load_attorney_preferences_block(attorney_id, base_dir, max_chars)` signature matches T2 tests; `proposed_preferences` field name identical across state/output_formatter/api.ts/ChatTab. ✅
- **Placeholder scan:** every code step carries complete code; line numbers are anchors to current `main` (verified during planning) — implementers should match on surrounding text, not the number. ✅
- **Ordering invariant** (ceiling): chat prefs right after `CHAT_SYSTEM_PROMPT` (T4); review prefs after playbook, before `_OUTPUT_CONSTRAINTS`, MSA directive still last (T5) — both asserted by tests.

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks. Model plan: T1–T6 backend have complete code → transcription+testing → cheap-tier implementers, mid-tier reviewers; T7–T8 frontend integration → mid-tier implementer + smoke; final whole-branch review on the most capable model, pointed at the ceiling-placement invariant and the LLM-parsing surface in T6/T8.
2. **Inline Execution** — batch with checkpoints.

Which approach?

---

## Post-implementation notes (plan ≡ code)

Executed via subagent-driven-development (8 tasks, cheap-tier implementers + per-task sonnet
reviews + opus whole-branch review = READY TO MERGE, zero Critical/Important). Deltas from the
plan as written, for anyone diffing plan vs code:

- **Task 2 test assertion.** The plan's `test_block_truncates` asserted `block.count("y") <= 100`,
  which is impossible — the `_PREFERENCES_DIRECTIVE` text itself contains ~8 "y"s, so the real
  block has ~108. Shipped as `assert "y" * 101 not in block` (genuinely fails if truncation is
  skipped). Same recurring authoring miss: hand-written plan assertions that don't run.
- **Fix wave (commit 1d64ba7).** Added the SOW+MSA review-injection ordering test (the MSA-last
  legal-safety invariant now has a regression test — needed `importlib.import_module` +
  `monkeypatch.setattr` on the real module object, because `skills.contract_review` re-exports the
  `contract_review` function and shadows the submodule); hardened `_safe_attorney_id` from `.match`
  to `.fullmatch` (rejects a trailing "\n"); removed an unused `logger` in the route module; added
  minimal CSS for the new `.preferences*` / `.preference-suggestion*` classes.
- **Final backend suite: 380** (base 347 + this feature) + `parsePreferenceBlocks` tsx (4) +
  frontend typecheck clean.
- **Smoke (Word for Mac, 2026-07-22/23):** passed — see the wiki Shipped row (traces `f8788b0b`,
  `fc051464`, `401e2da9`, `fa3a1bf0`). One documented caveat: the relaxing-preference ceiling test
  was inconclusive (no baseline blocker to observe suppression) → logged as a follow-up.
