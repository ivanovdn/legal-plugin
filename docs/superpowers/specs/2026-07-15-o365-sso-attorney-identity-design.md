# O365 SSO Attorney Identity — Design (Backend Foundation)

**Date:** 2026-07-15
**Status:** Approved (design) — pending spec review
**Slice:** 3 of 3 in the server-side multi-attorney chat-continuity initiative
**Builds on:** slice 1 — canonical document UUID (`docs/superpowers/specs/2026-07-14-canonical-document-uuid-design.md`, shipped `cc83cd5`); slice 2 — per-attorney conversation store (`docs/superpowers/specs/2026-07-15-conversation-store-design.md`, shipped `447e4e7`)

## Goal

Stop trusting a client-asserted, spoofable `X-User-ID` header for attorney
identity. Derive the attorney from a **cryptographically verified O365 token**
instead. Built **config-gated and off by default**, so today's behavior is
byte-for-byte unchanged until an Azure AD app registration is provisioned.

This slice ships the **backend foundation only** (validation + the derivation
seam), fully unit-tested. The Word client's `getAccessToken()` call, the
manifest `WebApplicationInfo` block, and the dialog fallback are **deferred**
to a follow-up that runs once the Azure app exists — they cannot be
smoke-tested without it.

## Background

Everything downstream of the route boundary already keys off
`state["user_id"]`: the conversation-store `attorney_id` partition key
(`memory/conversation_store.py`), the intake `user_id → client_id` map
(`graph/nodes/intake.py`), and Langfuse trace attribution. Today that value is
whatever the client puts in the `X-User-ID` header
(`api/routes/query.py:130`) — the backend performs **no verification**. Slice 2
made this explicit and accepted: `attorney_id` is a partitioning key, not
authentication; spoofable, acceptable for a few trusted internal users.

Slice 3 closes that gap for the eventual scaled deployment: it changes **only
how `user_id` is derived at the route boundary**. Nothing downstream moves.

```
Today:  user_id = Header("X-User-ID")            # trusted as-is
After:  user_id = Depends(resolve_user_id)       # verified when SSO is on
```

## Architecture

A single new FastAPI dependency, `resolve_user_id`, replaces the raw `Header`
parameter on the query route. It implements a two-mode policy:

1. **`sso_enabled = False`** (default, now) → return the `X-User-ID` header
   exactly as today. This is the live path until the Azure app exists. Behavior
   is preserved byte-for-byte, including the `"anonymous"` default.

2. **`sso_enabled = True`** → require and validate an `Authorization: Bearer
   <jwt>`:
   - Verify the signature (RS256) against Microsoft's JWKS for the tenant.
   - Verify `iss` (tenant issuer), `aud` (our app's client id), and `exp`.
   - On success → `user_id = claims["oid"]` (the stable per-user, per-tenant
     object-id GUID — the correct identity anchor; `email` /
     `preferred_username` are mutable display fields and are **never** keyed on).
   - On missing / expired / invalid token → **`401 Unauthorized`** (Decision 1,
     option A). Enabling SSO means the spoofable header is no longer trusted at
     all. Legitimate `getAccessToken()` failures (old Office, consent pending,
     transient) are handled by the deferred client-side dialog fallback, **not**
     by silently trusting the header.
   - On JWKS-fetch / key-resolution infrastructure failure (not a bad token) →
     **`503 Service Unavailable`**, logged loudly. Distinct from `401`: the
     caller's token may be fine; we cannot verify it right now.

```
Word add-in                       FastAPI route boundary                    downstream (unchanged)
-----------                       ----------------------                    ----------------------
[deferred] getAccessToken()  --Authorization: Bearer-->  resolve_user_id  --> state["user_id"]
resolveAttorneyId() (today)  --X-User-ID header------->  (fallback path)   --> conversation_store,
                                                                                intake, Langfuse

resolve_user_id:
  sso_enabled == False  ->  X-User-ID header (today's behavior, incl. "anonymous")
  sso_enabled == True   ->  validate Bearer JWT -> oid   | 401 (bad/missing) | 503 (JWKS down)
```

## Components

### 1. `api/auth.py` (new — focused module)

Plain module, top-level imports only (project rule). Depends on `PyJWT[crypto]`.

```python
class SSOValidationError(Exception):
    """Raised when a Bearer token cannot be validated. Maps to HTTP 401."""

class SSOConfigError(Exception):
    """Raised when SSO is enabled but misconfigured, or JWKS is unreachable.
    Maps to HTTP 503."""
```

API:

- `validate_token(token: str, settings) -> dict`
  Verify signature + `iss` + `aud` + `exp` against the tenant JWKS; return the
  decoded claims. Raise `SSOValidationError` on any token defect (bad
  signature, wrong audience, wrong issuer, expired, malformed). Raise
  `SSOConfigError` when the JWKS endpoint is unreachable or SSO config is
  incomplete. **Never logs the token.**

- `attorney_id_from_claims(claims: dict) -> str`
  Return `claims["oid"]`. Raise `SSOValidationError` if `oid` is absent (a
  well-formed O365 user token always carries it; its absence means the token is
  not a user token we can key on).

- `resolve_user_id(request: Request, settings = Depends(get_settings)) -> str`
  The FastAPI dependency. Implements the two-mode policy above. Reads the
  `Authorization` / `X-User-ID` headers off the `Request`. Raises
  `HTTPException(401)` / `HTTPException(503)` as specified. In the `sso_enabled
  = False` branch it returns `request.headers.get("X-User-ID", "anonymous")` —
  matching the current `Header("anonymous", alias="X-User-ID")` default.

**JWKS caching:** use PyJWT's `PyJWKClient`, constructed once per JWKS URL and
cached at module scope (`functools.lru_cache` on the URL), so signing keys are
not refetched per request. `PyJWKClient` handles its own key-set caching and
kid lookup.

### 2. `api/routes/query.py` — swap the seam

Replace the header parameter with the dependency:

```python
from api.auth import resolve_user_id
# ...
def submit_query(
    body: QueryRequest,
    user_id: str = Depends(resolve_user_id),
):
    # ... x_user_id references become user_id
```

The two current uses (`langfuse_context.update_current_trace(user_id=...)` and
`initial_state["user_id"] = ...`) switch from `x_user_id` to `user_id`. No
other route consumes the identity header today (`resume` does not read it), so
this is the entire seam.

### 3. `config.py` — SSO settings (all inert by default)

Added after the conversation-store fields:

```python
sso_enabled: bool = False            # master switch; False = trust X-User-ID (today)
sso_tenant_id: str = ""              # Azure AD tenant (directory) id
sso_client_id: str = ""              # app (client) id — the expected token audience
sso_issuer: str = ""                 # expected iss; if empty, derived from tenant id
sso_jwks_url: str = ""               # JWKS endpoint; if empty, derived from tenant id
```

Derivation helpers (when the explicit override is empty), using the v2.0
endpoints:

- issuer  → `https://login.microsoftonline.com/{tenant_id}/v2.0`
- JWKS    → `https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys`

With `sso_enabled = False` and empty ids, none of these are touched.

### 4. `requirements.txt`

Add `PyJWT[crypto]` (brings in `cryptography` for RS256 verification). No other
new runtime dependency — `PyJWKClient` ships with PyJWT and uses `urllib` for
the JWKS fetch.

## Data flow

**Request (SSO off — today):** client sends `X-User-ID` → `resolve_user_id`
returns it verbatim → `state["user_id"]` → downstream unchanged.

**Request (SSO on):** client sends `Authorization: Bearer <jwt>` →
`resolve_user_id` validates → `user_id = oid` → `state["user_id"]` → downstream
unchanged (conversation store now partitions by the verified oid).

**Attacker with no token (SSO on):** `resolve_user_id` raises `401` before the
graph runs. The spoofable-header path is unreachable while SSO is enabled.

## Error handling / degraded posture

- **Bad/missing token (SSO on):** `HTTPException(401)`. Turn does not run.
- **JWKS unreachable (SSO on):** `HTTPException(503)`, logged. Distinct from a
  bad token; the operator sees an auth-infrastructure problem, not a client
  problem.
- **SSO off:** header path, identical to today (incl. `"anonymous"` default).
- **This is orthogonal to the existing memory-degraded / Redis-degrade logic**
  in `query.py` — that handles a checkpointer outage mid-invoke and is
  untouched. Auth happens before graph invocation.

## Security notes

- **`oid` is the identity anchor.** Stable per-user, per-tenant GUID. Display
  fields (`email`, `preferred_username`, `name`) are mutable and never keyed on.
- **Audience pinning.** `aud` must equal `sso_client_id`. Prevents a token
  minted for another app from being replayed against ours.
- **Issuer pinning.** `iss` must match the tenant issuer. Single-tenant now.
- **Never log tokens** — not in `validate_token`, not in exception handlers.
- **Signature required.** RS256 only; reject `alg: none` and symmetric algs
  (PyJWT's `algorithms=["RS256"]` allow-list enforces this).

## Known transition note (documented, not built)

When an attorney flips from the slice-2 localStorage UUID to their O365 `oid`,
their `attorney_id` partition key changes, so **pre-SSO conversation history
does not carry forward** for that attorney. This is a one-time transition on
enablement, acceptable, and documented as a follow-up (no migration is built —
YAGNI while SSO is dormant). Reviews are unaffected (keyed by `document_id`
only).

## What stays untouched

- **Downstream identity consumers** — conversation store, intake `client_id`
  map, Langfuse — all still read `state["user_id"]`. Zero changes.
- **`X-User-ID` seam** — remains the live path (and the SSO-off path) until the
  client work lands.
- **Redis degrade / `memory_degraded`** — orthogonal, unchanged.
- **Chainlit** — stays `anonymous`; the SSO deployment is Word-only. When SSO
  is enabled in a Word-only deployment, Chainlit is not part of it.

## Testing strategy

**Backend (pytest) — `tests/test_sso.py`:** fully offline. Generate an RSA
keypair in-test, sign JWTs with the private key, monkeypatch the JWKS
client/key resolution to return the matching public key. No network.

- `validate_token`: happy path (valid signature, `aud`, `iss`, unexpired) →
  returns claims incl. `oid`.
- Rejects: bad signature (signed with a different key), wrong `aud`, wrong
  `iss`, expired (`exp` in the past), malformed token → `SSOValidationError`.
- JWKS unreachable (key resolution raises) → `SSOConfigError`.
- `attorney_id_from_claims`: returns `oid`; missing `oid` → `SSOValidationError`.

**Dependency — `resolve_user_id`:**

- `sso_enabled = False`: returns the `X-User-ID` header; **missing header →
  `"anonymous"`** (byte-for-byte regression guard on today's live path).
- `sso_enabled = True` + valid Bearer → the `oid`.
- `sso_enabled = True` + missing `Authorization` → `401`.
- `sso_enabled = True` + invalid token → `401`.
- `sso_enabled = True` + JWKS down → `503`.

**Route integration — `tests/` (FastAPI `TestClient`):**

- With SSO off (default), an existing query test still passes with `X-User-ID`
  → confirms the seam swap is transparent.
- With SSO on, a request with no `Authorization` gets `401` (graph never
  invoked — assert the graph mock is not called).

**No frontend changes in this slice** (client work deferred), so no new tsx
tests. Existing frontend suite must remain green (it still sends `X-User-ID`,
which is exactly the SSO-off path).

## Out of scope (deferred follow-ups)

- **Client SSO wiring** — `getAccessToken()` in the Word add-in, manifest
  `WebApplicationInfo` (app id + `api://…` resource), attaching the
  `Authorization: Bearer` header, and the **dialog-based fallback login**
  (MSAL) for the legitimate `getAccessToken()` failure cases. Runs when the
  Azure app is registered; this is what makes SSO *live*.
- **Azure AD app registration** — tenant/app provisioning + admin consent
  (external infra, not code).
- **Conversation-history migration** at SSO cutover (localStorage id → oid).
- **Multi-tenant `tid → client_id` mapping** — everyone is `"internal"` now;
  the `tid` claim is available if/when real multi-tenant lands.
- **On-Behalf-Of / Microsoft Graph** — we need identity, not Graph calls.
- **Real Chainlit auth** — stays `anonymous`.

## Decisions resolved

- **Decision 1 — strict when enabled (option A).** `sso_enabled = True` +
  missing/invalid token → `401`. Rejected: soft "validate-if-present" fallback
  to `X-User-ID` (option B) — an attacker just omits the token, defeating SSO;
  it only buys a softer rollout we don't need while dormant.
- **Decision 2 — backend-first scope.** Ship the tested backend foundation now;
  defer client `getAccessToken()` + manifest, which can't be smoke-tested
  without a live Azure app. Rejected: full dormant slice (ships unverified-live
  client code) and spec-only (user asked to make progress).
- **Identity anchor = `oid` claim**, not email/username (mutable), not `sub`
  (per-app-pairwise, less portable than the tenant-stable `oid`).
- **Seam = a FastAPI `Depends` dependency**, not ASGI middleware — the identity
  is consumed at exactly one route param; a dependency is more targeted and
  directly unit-testable.
- **Additive + reversible** — `sso_enabled = False` fully restores today's
  behavior; the change is a strict superset gated behind one flag.
