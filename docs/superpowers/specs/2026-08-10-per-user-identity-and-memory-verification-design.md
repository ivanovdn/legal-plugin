# Per-user identity + memory verification — design

- **Date:** 2026-08-10
- **Status:** Draft — awaiting review
- **Context:** The add-in is deployed and validated on the VM (spec 2 / bucket B). Before handing to legal-team testers, we want (a) each user identified by a **human-readable name** instead of the opaque per-install UUID, and (b) confidence that **every memory system works per-user on the deployment**. This spec covers both. It is a slice of the deferred SSO work ([[2026-07-15-o365-sso-attorney-identity-design]]): it ships the near-term **name bridge** and tees up SSO with an admin request.

## Problem

Today the attorney identity is an opaque per-install UUID (`crypto.randomUUID()` in `clients/word/src/attorneyIdentity.ts`), sent as `X-User-ID` → `resolve_user_id` → `state["user_id"]`. Consequences:

- **No name anywhere.** In the pane, the audit log, and (future) traces, a user is a UUID — you can't tell who's who.
- All per-user memory (preferences, conversations) is keyed by that UUID, which is fine as a *key* but useless for a human.
- Real verified identity (O365 SSO) is the proper fix but is blocked: the user confirmed they **lack rights to create the Azure app registration** (Azure portal returned **401 "You do not have access"**), so it's an admin task with lead time.

Separately, the memory systems (short-term Redis + long-term Postgres + preferences files) are *deployed* on the VM but have not been **functionally verified per-user** against the live deployment.

## Goals

1. **Human-readable identity now, no Azure:** a self-entered **name** that flows to the pane, the audit log, and trace metadata — while the **stable id stays the key** (no re-keying).
2. **SSO teed up:** design the swap (oid = key, token `name` = display) and produce the exact **Azure app-registration request** for an admin, so SSO drops in later with minimal change.
3. **Verify all memory works per-user on the VM:** short-term (Redis) + long-term (reviews / conversations / preferences) + audit, each end-to-end against the deployment.

## Non-goals (explicit follow-ups, not this spec)

- **RAG corpus seeding** (Qdrant `legal_docs`/`case_history`) — a knowledge base, not per-user memory; separate task.
- **Tracing UI deployment** (Langfuse / Phoenix) — Langfuse v3 (Postgres + ClickHouse + MinIO + Redis + 2 Node services, ~3–4 GB) **does not fit** the VM alongside compliance-bot + Phoenix + the legal stack. For the tester phase the **`audit_log` is the observability**; a trace UI (deploy off-box, or re-instrument onto the existing Phoenix) is a separate decision. This spec only ensures identity flows *into* traces/audit.
- **SSO execution** — the Azure app registration, manifest `WebApplicationInfo`, and client `getAccessToken()` wiring. Designed + requested here; executed later (needs the admin + the finalized hostname + VM egress to Microsoft).

## The load-bearing principle

**Key on a stable id; carry the name as a display label.** The name never becomes a key.

- Key today = per-install UUID (`X-User-ID`); key under SSO = Entra `oid`.
- Name is metadata: shown in the pane, written to `audit_log`, attached to trace metadata.
- Rationale: keying by name would collide on duplicates (shared legal work product — a real hazard), orphan memory on rename, and break filesystem paths (`data/attorneys/<id>/`). This mirrors the existing SSO seam's own rule (`api/auth.py`: *"Never key on email/username"*).

## Design

### A. Bridge — self-entered name (build now)

**Client (`clients/word/`):**
- `attorneyIdentity.ts`: add `resolveAttorneyName()` / `setAttorneyName(name)` backed by a new `localStorage` key `legalTriageAttorneyName` (empty by default; parallels the existing `legalTriageAttorneyId`).
- **Preferences tab** (`components/PreferencesTab.tsx`): a "Your name" text field at the top; saves to `localStorage` on blur/change. (Reuses the existing tab — no new surface.)
- `api.ts`: every request adds header **`X-User-Name: resolveAttorneyName()`** alongside the existing `X-User-ID`.

**Backend (`api/`, `graph/`):**
- `resolve_user_id` (`api/auth.py`): **unchanged** — still the stable key.
- New companion dependency `resolve_user_name` (`api/auth.py`): non-SSO → the `X-User-Name` header (default `""`); SSO → the token's `name` claim (implemented with the SSO slice — see B; under today's `sso_enabled=False` the header path is used, no token work).
- `submit_query` (`api/routes/query.py`): consume `resolve_user_name` → `initial_state["user_name"]`.
- `LegalAgentState` (`graph/state.py`): add `user_name: str`.
- **Never** log the raw value anywhere sensitive; a name is low-risk but treat it as user data.

**Where the name shows up:**
- **Audit log:** add a `user_name TEXT NOT NULL DEFAULT ''` column to `audit_log` (DDL in `memory/db.py`; `init_db()` also issues an idempotent `ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_name ...` for forward-compat on existing deployments). `memory_writer` writes `state["user_name"]`. This is the durable **id→name map** an admin queries (`SELECT DISTINCT user_id, user_name FROM audit_log`).
- **Trace metadata:** `query.py`'s `langfuse_context.update_current_trace(...)` keeps `user_id` = the stable id and adds the name as metadata (readable wherever traces land, if/when a trace UI exists).

### B. SSO — teed up (design + request; execute later)

- **Flip:** `sso_enabled=True`, `sso_tenant_id=3df46721-ba07-4b23-968c-cb40dee5230e` (Trinetix Inc / trinetix.com, confirmed), `sso_client_id=<from the app registration>`. The validated token then supplies **oid (key)** + **`name` claim (display)**; `X-User-Name` is ignored. `resolve_user_name`'s SSO branch reads the `name` claim from the already-validated token (single validation — extend `resolve_user_id`'s path rather than double-decoding).
- **Manifest:** add `WebApplicationInfo` (client id + `api://<hostname>/<client-id>` resource) to `manifest.template.xml`.
- **Client:** `Office.auth.getAccessToken()` → send as `Authorization: Bearer`; MSAL dialog fallback for consent/edge cases.
- **Deliverable now:** an **Azure app-registration request** appended to `docs/deploy-it-request.md` (Request 3), for an admin — because the user lacks rights (401). It specifies: register an app in the Trinetix tenant, expose the API scope, set the redirect/Application ID URI to the add-in hostname, return client id, and grant admin consent.
- **Prereqs (flagged):** the finalized **hostname** (deploy Request 1) and **VM outbound egress to `login.microsoftonline.com`** for JWKS validation — ⚠️ the VM's egress is restricted (it failed to resolve `langfuse.com`), so this must be opened or SSO validation returns 503 by design.

Because the bridge and SSO share the same downstream wiring (`user_name` → state → audit/trace; stable id → memory keys), the SSO swap is small and requires **no memory re-keying**.

### C. Memory — verify per-user on the VM (no keying change)

All memory keeping is **unchanged**; this is a verification pass proving each system works per-user against the deployment. Keys stay: preferences + conversations by `attorney_id` (the stable id); reviews by `document_id`.

## Data flow (bridge)

```
Word pane → headers: X-User-ID: <uuid>  +  X-User-Name: "Dmytro Ivanov"
   │
api/routes/query.py → resolve_user_id → state["user_id"] (KEY, unchanged)
                    → resolve_user_name → state["user_name"] (display)
   │
   ├─ memory_writer → audit_log(user_id, user_name, …)          ← durable id→name map
   ├─ langfuse trace → user_id=<uuid>, metadata.user_name="…"    ← readable if a UI exists
   └─ preferences/conversations keyed by user_id (UNCHANGED)
```

## Error handling

- Identity never breaks a turn: missing `X-User-Name` → `""` (same fail-safe posture as the existing `X-User-ID` → `anonymous`).
- The `audit_log` column is additive with a default, so old rows and non-name callers (Chainlit) are unaffected.
- Best-effort audit write is unchanged (a failed audit write already never fails the turn).

## Testing / acceptance (all run against the VM deployment)

**Unit / integration (repo tests):**
- `resolve_user_name` returns the header value; `""` when absent; (SSO branch covered when SSO ships).
- `memory_writer` writes `user_name` into `audit_log`.
- Frontend: `resolveAttorneyName`/`setAttorneyName` round-trip; `api.ts` sends `X-User-Name`.

**VM verification pass (the "establish the same on remote" checklist):**
1. **Short-term (Redis):** a multi-turn chat in one session retains context; resume-after-interrupt works.
2. **Long-term reviews:** a review persists (proven) **and** is recalled in a later chat turn on the same doc.
3. **Long-term conversations:** chat on a doc, reopen the pane / new session → prior turns are recalled (`conversation_store`).
4. **Preferences:** set a preference via the Preferences tab → persists to `data/attorneys/<id>/USER.md` on the VM → injected into the next turn.
5. **Identity + audit:** `audit_log` rows carry `user_id` **and** `user_name`; two testers with different names → separate preferences/conversations, correct names in audit.
6. **All keyed per stable id** (no cross-user bleed).

## Files touched (indicative — detailed in the plan)

- `clients/word/src/attorneyIdentity.ts` (name accessors), `components/PreferencesTab.tsx` (name field), `api.ts` (header).
- `api/auth.py` (`resolve_user_name`), `api/routes/query.py` (capture name + trace metadata), `graph/state.py` (`user_name`), `graph/nodes/memory_writer.py` (write name), `memory/db.py` + `memory/audit.py` (`user_name` column).
- `docs/deploy-it-request.md` (Request 3 — Azure app registration).
- Tests: `tests/test_auth*.py`, `tests/test_memory_writer.py`, `tests/test_audit.py`, frontend parse/identity tests.

## Risks & trade-offs

- **Self-entered name is unverified** (a user can type anything / duplicate). Acceptable for the tester phase; SSO replaces it with a verified name. The *key* is still unique regardless, so memory integrity holds.
- **SSO remains blocked** on the Azure app registration (admin) + hostname + VM egress — hence the bridge. All three are called out; none block the bridge.
- **No trace UI** for testers — mitigated by the enriched `audit_log`.

## Out of scope recap

RAG corpus seeding · tracing-UI deployment (Langfuse off-box / Phoenix re-instrumentation) · SSO execution (Azure app + manifest `WebApplicationInfo` + client `getAccessToken()`). Each is a separate follow-up; this spec keeps identity/memory self-contained and leaves clean seams for all three.
