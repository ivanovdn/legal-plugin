# Per-user Identity + Memory Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each user a human-readable **name** (self-entered now, O365 SSO later) that flows into the audit log and trace metadata, while the **stable id stays the memory key** — then verify all memory systems work per-user on the VM.

**Architecture:** Client sends a new `X-User-Name` header alongside the existing `X-User-ID`. Backend captures it via a new `resolve_user_name` dependency into `state["user_name"]`, written to the `audit_log` (new column) and attached to the Langfuse trace as metadata. Memory keying is unchanged (still the stable id). A dormant SSO branch + an Azure app-registration request tee up verified identity later.

**Tech Stack:** Python 3.12 · FastAPI · psycopg3/Postgres (app-db) · React/TS (Vite) · pytest (testcontainers Postgres) · `npx tsx` for frontend unit tests.

## Global Constraints

- **Key on a stable id; name is display-only, NEVER a key.** Stable id = `X-User-ID` (per-install UUID) now, Entra `oid` under SSO. Memory keys (`data/attorneys/<id>/`, `conversation_store.attorney_id`) are UNCHANGED — no re-keying.
- **Identity never breaks a turn:** missing `X-User-Name` → `""` (mirrors the existing `X-User-ID` → `anonymous` fail-safe).
- **All imports at top of file** (CLAUDE.md rule #1) — EXCEPT `tests/test_query_auth.py`'s deferred `from api.main import app` (documented route-integration exception).
- **psycopg3 pool is autocommit** — single-statement writes, no explicit commit. Schema changes go in `memory/db.py::_STATEMENTS` (idempotent: `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). Tests require Docker (testcontainers Postgres).
- **Tenant (for the SSO teed-up section):** Trinetix Inc / trinetix.com, tenant id `3df46721-ba07-4b23-968c-cb40dee5230e`. SSO is **not executed** here (blocked on the Azure app registration — user lacks rights, portal 401).
- **Frontend tests** are plain `npx tsx src/<file>.test.ts` scripts (console `PASS/FAIL` + in-memory `localStorage` mock) — no vitest. Frontend acceptance = `npx tsc --noEmit` + the tsx test + sideload smoke.
- **Restart the backend** (`docker compose ... up -d --build --force-recreate backend` on the VM; `scripts/start.sh` locally) after backend changes — uvicorn doesn't auto-reload.
- **CLAUDE.md is at 149/150 lines** — if adding a line would exceed 150, consolidate instead.

## File Structure

**Modify (backend):**
- `memory/db.py` — add `user_name` column to `audit_log` DDL + idempotent ALTER.
- `memory/audit.py` — `write_audit_log(..., user_name="")`.
- `graph/nodes/memory_writer.py` — forward `state["user_name"]`.
- `api/auth.py` — new `resolve_user_name` + dormant `attorney_name_from_claims`.
- `graph/state.py` — add `user_name: str`.
- `api/routes/query.py` — consume `resolve_user_name` → trace metadata + `initial_state`.

**Modify (frontend, `clients/word/`):**
- `src/attorneyIdentity.ts` — `resolveAttorneyName` / `setAttorneyName` / `userHeaders`.
- `src/api.ts`, `src/preferences.ts` — use `userHeaders()`.
- `src/components/PreferencesTab.tsx` — "Your name" field.
- `manifest.xml` — distinct dev `<Id>` + "(Dev)" name (coexist with prod).

**Tests:** `tests/test_db.py`, `tests/test_audit.py`, `tests/test_memory_writer.py`, `tests/test_query_auth.py`, `clients/word/src/attorneyIdentity.test.ts`.

**Docs:** `docs/deploy-it-request.md` (Request 3 — Azure app), `docs/wiki.md`.

---

### Task 1: Backend — `user_name` through the stores

**Files:**
- Modify: `memory/db.py` (audit_log DDL + ALTER)
- Modify: `memory/audit.py` (`write_audit_log`)
- Modify: `graph/nodes/memory_writer.py` (forward the value)
- Test: `tests/test_db.py`, `tests/test_audit.py`, `tests/test_memory_writer.py`

**Interfaces:**
- Produces: `write_audit_log(session_id, user_id, skill_name, task_type, request_summary, risk_level="low", review_status="not_required", review_notes="", duration_ms=0, user_name="") -> None`; `audit_log` gains a `user_name TEXT NOT NULL DEFAULT ''` column.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py`:
```python
def test_audit_log_has_user_name_column():
    from memory.db import get_pool
    with get_pool().connection() as conn:
        cols = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_log'"
        ).fetchall()
    assert "user_name" in {c[0] for c in cols}
```

Add to `tests/test_audit.py`:
```python
def test_write_audit_log_persists_user_name():
    from memory.audit import write_audit_log
    from memory.db import get_pool
    write_audit_log(
        session_id="s-name", user_id="uuid-1", skill_name="research",
        task_type="research", request_summary="who signs?", user_name="Dmytro Ivanov",
    )
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT user_name FROM audit_log WHERE user_id = 'uuid-1'"
        ).fetchone()
    assert row[0] == "Dmytro Ivanov"
```

Add to `tests/test_memory_writer.py`:
```python
def test_audit_receives_user_name(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: captured.update(kw))
    captured = {}
    mod.memory_writer(_state(task_type="research", user_id="uuid-1",
                             user_name="Dmytro Ivanov", llm_response="a"))
    assert captured["user_name"] == "Dmytro Ivanov"
```
(If `_state` doesn't accept arbitrary keys, it already does via `base.update(kw)` — pass `user_name` through it. This test also needs the research-turn conversation write mocked; add `monkeypatch.setattr(mod, "append_turn", lambda **kw: None)` if not already covered by the file's shared fixture.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db.py::test_audit_log_has_user_name_column tests/test_audit.py::test_write_audit_log_persists_user_name tests/test_memory_writer.py::test_audit_receives_user_name -v`
Expected: FAIL — no `user_name` column / `write_audit_log` has no `user_name` param.

- [ ] **Step 3: Add the column in `memory/db.py`**

In `_STATEMENTS`, change the `audit_log` CREATE to end with the new column, and add an ALTER right after it (for already-existing tables):
```python
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        session_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        skill_name TEXT NOT NULL,
        task_type TEXT NOT NULL,
        request_summary TEXT NOT NULL,
        risk_level TEXT NOT NULL DEFAULT 'low',
        review_status TEXT NOT NULL DEFAULT 'not_required',
        review_notes TEXT NOT NULL DEFAULT '',
        duration_ms BIGINT NOT NULL DEFAULT 0,
        user_name TEXT NOT NULL DEFAULT ''
    )
    """,
    "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_name TEXT NOT NULL DEFAULT ''",
```

- [ ] **Step 4: Add the param in `memory/audit.py`**

```python
def write_audit_log(
    session_id: str,
    user_id: str,
    skill_name: str,
    task_type: str,
    request_summary: str,
    risk_level: str = "low",
    review_status: str = "not_required",
    review_notes: str = "",
    duration_ms: int = 0,
    user_name: str = "",
) -> None:
    """Write a single audit log entry."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, session_id, user_id, skill_name, task_type,
                request_summary, risk_level, review_status, review_notes, duration_ms, user_name)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                session_id, user_id, skill_name, task_type,
                request_summary, risk_level, review_status, review_notes, duration_ms, user_name,
            ),
        )
    logger.info("Audit log: %s/%s for user %s", skill_name, task_type, user_id)
```

- [ ] **Step 5: Forward the value in `graph/nodes/memory_writer.py`**

In the `write_audit_log(...)` call, add the last argument:
```python
    write_audit_log(
        session_id=state.get("session_id", ""),
        user_id=state.get("user_id", ""),
        skill_name=state.get("task_type", "unknown"),
        task_type=state.get("task_type", ""),
        request_summary=state.get("request", "")[:200],
        risk_level=state.get("risk_level", "low"),
        review_status=review_status,
        review_notes=state.get("attorney_notes", ""),
        duration_ms=0,
        user_name=state.get("user_name", ""),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py tests/test_audit.py tests/test_memory_writer.py -v`
Expected: PASS (Docker up). Note: the fresh testcontainer builds the table WITH the column; the ALTER is exercised on redeploy over an existing DB.

- [ ] **Step 7: Commit**

```bash
git add memory/db.py memory/audit.py graph/nodes/memory_writer.py tests/test_db.py tests/test_audit.py tests/test_memory_writer.py
git commit -m "feat(identity): audit_log carries user_name (display-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Backend — `resolve_user_name` + query/state wiring (+ dormant SSO name)

**Files:**
- Modify: `api/auth.py` (`resolve_user_name`, `attorney_name_from_claims`)
- Modify: `graph/state.py` (`user_name` field)
- Modify: `api/routes/query.py` (dependency + trace metadata + `initial_state`)
- Test: `tests/test_query_auth.py`

**Interfaces:**
- Consumes: `write_audit_log(..., user_name=...)` + `state["user_name"]` (Task 1).
- Produces: `resolve_user_name(...) -> str` (FastAPI dependency); `initial_state["user_name"]` populated on every `/api/query`.

- [ ] **Step 1: Write the failing tests** (in `tests/test_query_auth.py`, mirroring the existing captured-state pattern)

```python
def test_sso_off_captures_x_user_name(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", False, raising=False)
    captured = {}
    with patch("api.routes.query._get_graph", return_value=_fake_graph(captured)), \
         patch("api.routes.query.refresh_ttl", lambda s: None):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           headers={"X-User-ID": "uuid-1", "X-User-Name": "Dmytro Ivanov"},
                           json={"request": "hi", "task_type": "research"})
    assert resp.status_code == 200
    assert captured["state"]["user_name"] == "Dmytro Ivanov"


def test_missing_x_user_name_defaults_empty(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", False, raising=False)
    captured = {}
    with patch("api.routes.query._get_graph", return_value=_fake_graph(captured)), \
         patch("api.routes.query.refresh_ttl", lambda s: None):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           headers={"X-User-ID": "uuid-1"},
                           json={"request": "hi", "task_type": "research"})
    assert resp.status_code == 200
    assert captured["state"]["user_name"] == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_query_auth.py::test_sso_off_captures_x_user_name tests/test_query_auth.py::test_missing_x_user_name_defaults_empty -v`
Expected: FAIL — `KeyError: 'user_name'` (state has no such key yet).

- [ ] **Step 3: Add `resolve_user_name` + dormant name helper in `api/auth.py`**

After `attorney_id_from_claims` / `resolve_user_id`:
```python
def attorney_name_from_claims(claims: dict) -> str:
    """Human-readable display name from the token. Display only — never a key."""
    return claims.get("name", "") or ""


def resolve_user_name(
    authorization: str | None = Header(None),
    x_user_name: str = Header("", alias="X-User-Name"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Display name for this request (never a key).

    SSO off (default): the self-entered X-User-Name header (default "").
    SSO on: the token's `name` claim. Access is already gated by resolve_user_id;
    a name is display-only, so any token problem here yields "" rather than failing.
    """
    if not settings.sso_enabled:
        return x_user_name
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    token = authorization.split(" ", 1)[1].strip()
    try:
        return attorney_name_from_claims(validate_token(token, settings))
    except Exception:
        return ""
```

- [ ] **Step 4: Add the state field in `graph/state.py`**

At the end of `LegalAgentState` (after `memory_degraded`):
```python
    user_name: str                         # NEW — display-only name (self-entered now; SSO `name` claim later); NEVER a key
```

- [ ] **Step 5: Wire it in `api/routes/query.py`**

Add the import (with the existing `from api.auth import resolve_user_id`):
```python
from api.auth import resolve_user_id, resolve_user_name
```
Add the dependency + trace metadata + state field:
```python
def submit_query(
    body: QueryRequest,
    user_id: str = Depends(resolve_user_id),
    user_name: str = Depends(resolve_user_name),
):
    """Submit a legal request for graph execution."""
    session_id = body.session_id or str(uuid.uuid4())

    langfuse_context.update_current_trace(
        name=f"query:{body.task_type or 'auto'}",
        user_id=user_id,
        session_id=session_id,
        input=body.request,
        metadata={"user_name": user_name},
    )

    initial_state = {
        "request": body.request,
        "user_id": user_id,
        "user_name": user_name,
        # ... rest unchanged ...
```
(Add the `"user_name": user_name,` line to the `initial_state` dict; leave every other field as-is.)

- [ ] **Step 6: Run to verify pass + full suite**

Run: `uv run pytest tests/test_query_auth.py -v && uv run pytest tests/ -q`
Expected: the two new tests PASS; full suite green.

- [ ] **Step 7: Commit**

```bash
git add api/auth.py graph/state.py api/routes/query.py tests/test_query_auth.py
git commit -m "feat(identity): resolve_user_name -> state + trace (SSO name helper dormant)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Frontend — name field, header helper, dev/prod manifest coexistence

**Files:**
- Modify: `clients/word/src/attorneyIdentity.ts`
- Modify: `clients/word/src/api.ts`, `clients/word/src/preferences.ts`
- Modify: `clients/word/src/components/PreferencesTab.tsx`
- Modify: `clients/word/manifest.xml`
- Test: `clients/word/src/attorneyIdentity.test.ts`

**Interfaces:**
- Produces: `resolveAttorneyName()`, `setAttorneyName(name)`, `userHeaders()` from `attorneyIdentity.ts`; every backend call sends `X-User-Name`.

- [ ] **Step 1: Add the failing test** (append to `clients/word/src/attorneyIdentity.test.ts`, same console-`pass` style; add the import at the top with the existing one)

Top import line becomes:
```typescript
import { resolveAttorneyId, resolveAttorneyName, setAttorneyName, userHeaders } from "./attorneyIdentity";
```
Append at the end (reset the mock store first so it's clean):
```typescript
// name accessors + userHeaders
(globalThis as { localStorage?: unknown }).localStorage = new MemStore();
pass(resolveAttorneyName() === "", "name is empty by default");
setAttorneyName("Dmytro Ivanov");
pass(resolveAttorneyName() === "Dmytro Ivanov", "name round-trips through storage");
const h = userHeaders();
pass(typeof h["X-User-ID"] === "string" && h["X-User-ID"].length > 0, "userHeaders has X-User-ID");
pass(h["X-User-Name"] === "Dmytro Ivanov", "userHeaders has X-User-Name");
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd clients/word && npx tsx src/attorneyIdentity.test.ts`
Expected: fails to compile / `resolveAttorneyName is not a function` (exports don't exist yet).

- [ ] **Step 3: Add the accessors + helper in `attorneyIdentity.ts`**

Append (keep the existing `resolveAttorneyId`):
```typescript
const NAME_KEY = "legalTriageAttorneyName";

/** The attorney's self-entered display name (empty until set). Display only, never a key. */
export function resolveAttorneyName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAttorneyName(name: string): void {
  try {
    localStorage.setItem(NAME_KEY, name);
  } catch {
    /* ignore — name is display-only, must never break the app */
  }
}

/** Identity headers for every backend call: stable id (key) + display name. */
export function userHeaders(): Record<string, string> {
  return { "X-User-ID": resolveAttorneyId(), "X-User-Name": resolveAttorneyName() };
}
```

- [ ] **Step 4: Use `userHeaders()` in `api.ts` and `preferences.ts`**

`api.ts` — change the import line and `postQuery` headers:
```typescript
import { userHeaders } from "./attorneyIdentity";
// ...
async function postQuery(body: Record<string, unknown>): Promise<QueryResponse> {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify(body),
  });
  // ... unchanged ...
```
(Remove the now-unused `resolveAttorneyId` import from `api.ts` if nothing else uses it.)

`preferences.ts` — swap the import and both header blocks:
```typescript
import { userHeaders } from "./attorneyIdentity";

export async function getPreferences(): Promise<string> {
  const res = await fetch("/api/preferences", { headers: userHeaders() });
  // ... unchanged ...
}

export async function savePreferences(markdown: string): Promise<void> {
  const res = await fetch("/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify({ markdown }),
  });
  // ... unchanged ...
}
```

- [ ] **Step 5: Add the "Your name" field to `PreferencesTab.tsx`**

Add the import and a small local field above the textarea:
```tsx
import { resolveAttorneyName, setAttorneyName } from "../attorneyIdentity";
```
Inside the component, add state:
```tsx
  const [name, setName] = useState(resolveAttorneyName());
```
In the returned JSX, right after the `<p className="subtitle">…</p>`:
```tsx
      <label className="preferences-name">
        Your name
        <input
          type="text"
          value={name}
          onChange={(e) => { setName(e.target.value); setAttorneyName(e.target.value); }}
          placeholder="e.g. Dmytro Ivanov"
        />
      </label>
```

- [ ] **Step 6: Give the DEV manifest a distinct Id + name (coexist with prod)**

In `clients/word/manifest.xml` ONLY (NOT `manifest.template.xml`):
- Change `<Id>D57831EF-B615-4D73-B777-8D6E06DDA59C</Id>` → `<Id>A8A5F2CD-4CDB-4B06-B909-EEBADBF24B31</Id>`
- Change `<DisplayName DefaultValue="Legal Triage" />` → `<DisplayName DefaultValue="Legal Triage (Dev)" />`

Verify the prod template is untouched (still the original Id, so the deployed prod add-in keeps working):
```bash
grep -c "D57831EF-B615-4D73-B777-8D6E06DDA59C" clients/word/manifest.template.xml
```
Expected: `1`. And `grep -c "A8A5F2CD" clients/word/manifest.xml` → `1`.

- [ ] **Step 7: Verify — test + typecheck**

Run:
```bash
cd clients/word && npx tsx src/attorneyIdentity.test.ts && npx tsc --noEmit
```
Expected: all `PASS:` lines, no `FAIL:`; `tsc` clean (no errors).

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/attorneyIdentity.ts clients/word/src/attorneyIdentity.test.ts clients/word/src/api.ts clients/word/src/preferences.ts clients/word/src/components/PreferencesTab.tsx clients/word/manifest.xml
git commit -m "feat(identity): self-entered name in Preferences + X-User-Name header; dev/prod manifest coexist

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Deploy to the VM, verify memory per-user, docs + Azure request

**Files:**
- Modify: `docs/deploy-it-request.md` (Request 3 — Azure app registration)
- Modify: `docs/wiki.md` (Shipped entry)
- (No app-code changes — this task deploys + verifies + documents.)

**Interfaces:**
- Consumes: everything from Tasks 1–3, merged to `main`.

- [ ] **Step 1: Merge to main + push** (after Tasks 1–3 are reviewed)

```bash
git checkout main && git merge --no-ff feat/per-user-identity -m "Merge feat/per-user-identity: name bridge + memory verification"
uv run pytest tests/ -q   # full suite green on merged main
git push origin main && git push ado main
```

- [ ] **Step 2: Local sanity in real Word (dev add-in)** — before touching the VM

Run local dev (`bash scripts/start.sh` + `cd clients/word && npm run dev`), sideload the DEV manifest so **"Legal Triage (Dev)"** appears alongside the prod one, set a name in the Preferences tab, run a review, then:
```bash
docker compose exec app-db psql -U legal -d legal -c \
  "SELECT DISTINCT user_id, user_name FROM audit_log ORDER BY user_name;"
```
Expected: a row with your entered name against your dev UUID. (If local app-db isn't the one the dev backend uses, check `DATABASE_URL`.)

- [ ] **Step 3: Deploy to the VM**

On `SRV-AGENT-01`:
```bash
cd ~/legal-plugin && git pull
docker run --rm -v "$PWD/clients/word":/w -w /w node:20 sh -c "npm ci && npm run build"
docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --build --force-recreate backend caddy
```
`--force-recreate backend` ensures `init_db()` runs the `ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_name` on the existing app-db. Confirm the column landed:
```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml exec app-db \
  psql -U legal -d legal -c "\d audit_log" | grep user_name
```
Expected: a `user_name | text` line.

- [ ] **Step 4: VM memory verification pass** (in real Word against the VM — the prod "Legal Triage" add-in already sideloaded on your Mac; rebuild the pane cache if needed)

Run and record each:
1. **Preferences (long-term, files):** Preferences tab → set your **name** + a preference → Save → reopen the pane → both persist. On the VM: `ls ~/legal-plugin/data/attorneys/` shows your id dir with `USER.md`.
2. **Short-term (Redis):** in one session, ask a follow-up that depends on the prior turn → context retained.
3. **Long-term reviews:** run a review; in chat ask "what did the review flag?" → recalled (not re-derived).
4. **Long-term conversations:** chat a couple of turns, close/reopen the pane → prior turns recalled.
5. **Identity in audit:** `psql ... "SELECT DISTINCT user_id, user_name FROM audit_log;"` shows your id ↔ name.
6. **Per-user isolation:** (if a second tester/name is available) two names → separate `USER.md` dirs + separate conversation rows.

- [ ] **Step 5: Write Request 3 (Azure app registration) into `docs/deploy-it-request.md`**

Append a new section (forwardable to a Global/Entra admin — the user hit 401, so it's their task):
```markdown
## Request 3 — Entra app registration for SSO (later)

**Subject: Entra app registration for the Legal Triage Word add-in (Office SSO)**

To let the add-in identify users by their verified O365 identity, please register an app in the **Trinetix Inc** tenant (`3df46721-ba07-4b23-968c-cb40dee5230e`):
1. **Single-tenant** app registration named "Legal Triage".
2. **Expose an API:** set the Application ID URI to `api://<add-in-hostname>/<client-id>` (hostname = the internal DNS name from Request 1); add a delegated scope `access_as_user`.
3. **Pre-authorize the Office host client IDs** for that scope (the standard Office desktop/web/mobile app IDs) so `getAccessToken()` works without a per-user consent prompt.
4. **Grant admin consent** for the scope.
5. Return the **Application (client) ID**.
6. Confirm **SRV-AGENT-01 (`172.20.1.10`) can reach `login.microsoftonline.com`** (JWKS validation) — its egress is currently restricted.

We then set `sso_enabled=True`, `sso_tenant_id`, `sso_client_id`, add `WebApplicationInfo` to the manifest, and wire the client `getAccessToken()`. Until then, users self-enter their name.
```

- [ ] **Step 6: Update `docs/wiki.md`**

Add a "Shipped Since Last Update" entry: per-user identity — self-entered name (`X-User-Name` header → `state["user_name"]` → `audit_log.user_name` + trace metadata), stable id still the memory key; dev/prod manifest coexistence; SSO teed up (Request 3). Note memory verified per-user on the VM; Langfuse-UI + RAG seeding remain follow-ups.

- [ ] **Step 7: Commit + push**

```bash
git add docs/deploy-it-request.md docs/wiki.md
git commit -m "docs(identity): Azure app-registration request (SSO) + wiki shipped entry"
git push origin main && git push ado main
```

---

## Self-Review

**1. Spec coverage:**
- Bridge / self-entered name → Tasks 2 (backend capture) + 3 (client field + header). ✅
- Stable id stays the key / no re-keying → Global Constraints + no memory-key changes anywhere. ✅
- Name in audit_log (id→name map) → Task 1. ✅
- Name in trace metadata → Task 2 Step 5. ✅
- SSO teed up (dormant name helper + Azure request + prereqs) → Task 2 (`resolve_user_name`/`attorney_name_from_claims` SSO branch) + Task 4 Step 5. ✅
- Memory verification on the VM (short/long/audit, per-user) → Task 4 Step 4. ✅
- Dev/prod manifest coexistence (test-local-then-remote) → Task 3 Step 6. ✅
- Out of scope (RAG seeding, Langfuse-UI deploy, SSO execution) → correctly absent; Langfuse deployment not attempted. ✅

**2. Placeholder scan:** No TBD/TODO. `<add-in-hostname>` / `<client-id>` in Request 3 are deferred external values (Request 1 + the app registration), clearly marked — not gaps. Every code step shows complete code.

**3. Type consistency:** `resolveAttorneyName`/`setAttorneyName`/`userHeaders` names match across `attorneyIdentity.ts`, its test, `api.ts`, `preferences.ts`. `write_audit_log(..., user_name="")` signature matches its call in `memory_writer` and the tests. `state["user_name"]` set in `query.py`, read in `memory_writer`, declared in `LegalAgentState`. `resolve_user_name` produced in `api/auth.py`, consumed in `api/routes/query.py`. Dev manifest Id `A8A5F2CD-…` distinct from prod `D57831EF-…`.
