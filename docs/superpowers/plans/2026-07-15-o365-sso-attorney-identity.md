# O365 SSO Attorney Identity (Backend Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive the attorney identity (`state["user_id"]`) from a cryptographically verified O365 token instead of the spoofable `X-User-ID` header, gated behind `sso_enabled` (default False, so today's behavior is unchanged).

**Architecture:** A new `api/auth.py` module validates an `Authorization: Bearer` JWT against the tenant's Microsoft JWKS and extracts the stable `oid` claim. A single FastAPI dependency, `resolve_user_id`, replaces the raw `Header` parameter on `submit_query`: when SSO is off it returns the `X-User-ID` header (today's path); when on it returns the verified `oid`, raising `401` on a missing/invalid token and `503` when JWKS is unreachable. Nothing downstream of `state["user_id"]` changes.

**Tech Stack:** Python 3.12, FastAPI, PyJWT[crypto] (RS256 + `PyJWKClient` JWKS), pytest. Offline tests only (in-test RSA keypair, monkeypatched JWKS client).

## Global Constraints

- **All imports at top of file** — no lazy imports inside functions.
- **`sso_enabled` default is `False`** — the feature ships dormant; `sso_enabled=False` must reproduce today's `X-User-ID` behavior byte-for-byte, including the `"anonymous"` default when the header is absent.
- **Identity anchor is the `oid` claim** — never `email` / `preferred_username` / `sub`.
- **Strict when enabled (Decision 1=A):** `sso_enabled=True` + missing/invalid token → HTTP `401`. No silent fallback to `X-User-ID`.
- **`503`, not `401`, for JWKS-infrastructure failure** (endpoint unreachable) — distinct from a bad token.
- **RS256 only** — `algorithms=["RS256"]` allow-list; reject `alg:none` and symmetric algs.
- **Never log the token** — not in `validate_token`, not in the dependency's error branches.
- **Audience pinning** = `sso_client_id`; **issuer pinning** = tenant issuer.
- **Backend only** — client `getAccessToken()`, manifest `WebApplicationInfo`, and dialog fallback are out of scope (deferred until the Azure app exists).
- **Tests are fully offline** — generate an RSA keypair in-test, sign JWTs, monkeypatch the JWKS client. No network.

---

### Task 1: Auth module core — config fields, token validation, claim extraction

**Files:**
- Modify: `config.py` (add `sso_*` fields after `conversation_max_messages`, line 64)
- Create: `api/auth.py`
- Test: `tests/test_sso.py`

**Interfaces:**
- Consumes: `config.Settings` / `config.get_settings` (existing).
- Produces (relied on by Tasks 2 & 3):
  - `class SSOValidationError(Exception)` — bad/missing/malformed token → maps to 401.
  - `class SSOConfigError(Exception)` — SSO misconfigured or JWKS unreachable → maps to 503.
  - `validate_token(token: str, settings) -> dict` — returns decoded claims; raises `SSOValidationError` / `SSOConfigError`.
  - `attorney_id_from_claims(claims: dict) -> str` — returns `claims["oid"]`; raises `SSOValidationError` if absent.
  - `_issuer(settings) -> str`, `_jwks_url(settings) -> str` — derive v2.0 endpoints from `sso_tenant_id` when the explicit override is empty.

- [ ] **Step 1: Add SSO config fields**

In `config.py`, immediately after line 64 (`conversation_max_messages: int = 20 ...`), add:

```python
    # O365 SSO (slice 3) — dormant until sso_enabled; False = trust X-User-ID (today)
    sso_enabled: bool = False
    sso_tenant_id: str = ""      # Azure AD tenant (directory) id
    sso_client_id: str = ""      # app (client) id — expected token audience
    sso_issuer: str = ""         # expected iss; derived from tenant id when empty
    sso_jwks_url: str = ""       # JWKS endpoint; derived from tenant id when empty
```

- [ ] **Step 2: Write the failing tests for `validate_token` / `attorney_id_from_claims` / derivation**

Create `tests/test_sso.py`:

```python
"""O365 SSO token validation — fully offline (in-test RSA keypair, mocked JWKS)."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import DecodeError, PyJWKClientConnectionError

import api.auth as auth
from api.auth import (
    SSOConfigError,
    SSOValidationError,
    attorney_id_from_claims,
    validate_token,
)

CLIENT_ID = "api://legal-triage-app-id"
TENANT_ID = "test-tenant-guid"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"

# One keypair for the whole module — RSA keygen is slow; reuse it.
_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIV_PEM = _PRIV.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUB_PEM = _PRIV.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _settings(**over):
    base = dict(
        sso_enabled=True, sso_tenant_id=TENANT_ID, sso_client_id=CLIENT_ID,
        sso_issuer="", sso_jwks_url="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _token(**over):
    now = int(time.time())
    payload = dict(oid="attorney-oid-123", aud=CLIENT_ID, iss=ISSUER,
                   exp=now + 3600, iat=now)
    payload.update(over)
    return jwt.encode(payload, _PRIV_PEM, algorithm="RS256")


class _FakeKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    """Returns our test public key; or raises to simulate JWKS failures."""
    def __init__(self, raise_exc=None):
        self._raise = raise_exc

    def get_signing_key_from_jwt(self, token):
        if self._raise is not None:
            raise self._raise
        return _FakeKey(_PUB_PEM)


@pytest.fixture(autouse=True)
def _patch_jwks(monkeypatch):
    # Default: healthy JWKS returning our public key. Individual tests override.
    monkeypatch.setattr(auth, "_jwks_client", lambda url: _FakeJWKClient())


def test_valid_token_returns_claims():
    claims = validate_token(_token(), _settings())
    assert claims["oid"] == "attorney-oid-123"
    assert claims["aud"] == CLIENT_ID


def test_bad_signature_rejected(monkeypatch):
    # JWKS returns a public key that does NOT match the signing key.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pub = other.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    class _MismatchClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeKey(other_pub)

    monkeypatch.setattr(auth, "_jwks_client", lambda url: _MismatchClient())
    with pytest.raises(SSOValidationError):
        validate_token(_token(), _settings())


def test_wrong_audience_rejected():
    with pytest.raises(SSOValidationError):
        validate_token(_token(aud="api://some-other-app"), _settings())


def test_wrong_issuer_rejected():
    with pytest.raises(SSOValidationError):
        validate_token(_token(iss="https://evil.example/v2.0"), _settings())


def test_expired_token_rejected():
    now = int(time.time())
    with pytest.raises(SSOValidationError):
        validate_token(_token(exp=now - 10, iat=now - 3600), _settings())


def test_malformed_token_rejected(monkeypatch):
    monkeypatch.setattr(auth, "_jwks_client",
                        lambda url: _FakeJWKClient(raise_exc=DecodeError("bad header")))
    with pytest.raises(SSOValidationError):
        validate_token("not-a-jwt", _settings())


def test_jwks_unreachable_is_config_error(monkeypatch):
    monkeypatch.setattr(
        auth, "_jwks_client",
        lambda url: _FakeJWKClient(raise_exc=PyJWKClientConnectionError("dns fail", url)))
    with pytest.raises(SSOConfigError):
        validate_token(_token(), _settings())


def test_missing_client_id_is_config_error():
    with pytest.raises(SSOConfigError):
        validate_token(_token(), _settings(sso_client_id=""))


def test_attorney_id_from_claims_returns_oid():
    assert attorney_id_from_claims({"oid": "abc", "email": "x@y.z"}) == "abc"


def test_attorney_id_from_claims_missing_oid_raises():
    with pytest.raises(SSOValidationError):
        attorney_id_from_claims({"email": "x@y.z"})


def test_issuer_and_jwks_derived_from_tenant():
    s = _settings()
    assert auth._issuer(s) == ISSUER
    assert auth._jwks_url(s) == (
        f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys")


def test_explicit_issuer_and_jwks_override_tenant():
    s = _settings(sso_issuer="https://custom/iss", sso_jwks_url="https://custom/keys")
    assert auth._issuer(s) == "https://custom/iss"
    assert auth._jwks_url(s) == "https://custom/keys"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sso.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.auth'` (module not created yet).

- [ ] **Step 4: Implement `api/auth.py` (core)**

Create `api/auth.py`:

```python
# api/auth.py
"""O365 SSO token validation and attorney-identity resolution.

Dormant until settings.sso_enabled. When enabled, an incoming Bearer JWT is
verified against the tenant's Microsoft JWKS (RS256, audience + issuer + expiry)
and the stable `oid` claim becomes the attorney id. See
docs/superpowers/specs/2026-07-15-o365-sso-attorney-identity-design.md.
"""
import functools
import logging

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWTError

logger = logging.getLogger(__name__)


class SSOValidationError(Exception):
    """A Bearer token could not be validated (bad/expired/malformed). -> HTTP 401."""


class SSOConfigError(Exception):
    """SSO is enabled but misconfigured, or the JWKS endpoint is unreachable. -> HTTP 503."""


def _issuer(settings) -> str:
    return settings.sso_issuer or (
        f"https://login.microsoftonline.com/{settings.sso_tenant_id}/v2.0"
    )


def _jwks_url(settings) -> str:
    return settings.sso_jwks_url or (
        f"https://login.microsoftonline.com/{settings.sso_tenant_id}"
        "/discovery/v2.0/keys"
    )


@functools.lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    """One cached client per JWKS URL — PyJWKClient caches signing keys internally."""
    return PyJWKClient(jwks_url)


def validate_token(token: str, settings) -> dict:
    """Verify signature + audience + issuer + expiry; return decoded claims.

    Raises SSOValidationError on any token defect, SSOConfigError on missing
    config or an unreachable JWKS endpoint.
    """
    if not settings.sso_client_id or not (settings.sso_tenant_id or settings.sso_issuer):
        raise SSOConfigError("SSO enabled but sso_client_id / sso_tenant_id not configured")

    try:
        signing_key = _jwks_client(_jwks_url(settings)).get_signing_key_from_jwt(token)
    except PyJWKClientConnectionError as e:
        raise SSOConfigError("JWKS endpoint unreachable") from e
    except (PyJWKClientError, PyJWTError) as e:
        # malformed token / no matching kid — reject the token
        raise SSOValidationError(f"cannot resolve signing key: {e.__class__.__name__}") from e

    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.sso_client_id,
            issuer=_issuer(settings),
            options={"require": ["exp", "iss", "aud"]},
        )
    except PyJWTError as e:
        raise SSOValidationError(f"token rejected: {e.__class__.__name__}") from e


def attorney_id_from_claims(claims: dict) -> str:
    """The stable per-user, per-tenant object id. Never key on email/username."""
    oid = claims.get("oid")
    if not oid:
        raise SSOValidationError("token has no 'oid' claim")
    return oid
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sso.py -v`
Expected: PASS (12 tests).

- [ ] **Step 6: Commit**

```bash
git add config.py api/auth.py tests/test_sso.py
git commit -m "feat: O365 SSO token validation core (api/auth.py) + config fields

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `resolve_user_id` dependency — the two-mode policy

**Files:**
- Modify: `api/auth.py` (add `resolve_user_id` + imports)
- Test: `tests/test_sso.py` (append)

**Interfaces:**
- Consumes: `validate_token`, `attorney_id_from_claims`, `SSOValidationError`, `SSOConfigError` (Task 1); `config.Settings` / `config.get_settings`.
- Produces (relied on by Task 3):
  - `resolve_user_id(authorization: str | None = Header(None), x_user_id: str = Header("anonymous", alias="X-User-ID"), settings: Settings = Depends(get_settings)) -> str`
  - Returns `x_user_id` when `sso_enabled` is False; the verified `oid` when True. Raises `HTTPException(401)` on missing/invalid token, `HTTPException(503)` on JWKS failure.

- [ ] **Step 1: Write the failing tests for `resolve_user_id`**

First, extend the **top-of-file imports** in `tests/test_sso.py` (keep all imports at the top — project rule): add `from fastapi import HTTPException`, and add `resolve_user_id` to the existing `from api.auth import (...)` tuple. Then append these tests (no inline imports):

```python
def test_resolve_sso_off_returns_header():
    uid = resolve_user_id(authorization=None, x_user_id="localstorage-uuid",
                          settings=_settings(sso_enabled=False))
    assert uid == "localstorage-uuid"


def test_resolve_sso_off_missing_header_is_anonymous():
    # Byte-for-byte guard on today's Header("anonymous") default.
    uid = resolve_user_id(authorization=None, x_user_id="anonymous",
                          settings=_settings(sso_enabled=False))
    assert uid == "anonymous"


def test_resolve_sso_on_valid_token_returns_oid():
    uid = resolve_user_id(authorization=f"Bearer {_token()}", x_user_id="ignored",
                          settings=_settings())
    assert uid == "attorney-oid-123"


def test_resolve_sso_on_missing_authorization_is_401():
    with pytest.raises(HTTPException) as ei:
        resolve_user_id(authorization=None, x_user_id="spoofed", settings=_settings())
    assert ei.value.status_code == 401


def test_resolve_sso_on_non_bearer_scheme_is_401():
    with pytest.raises(HTTPException) as ei:
        resolve_user_id(authorization="Basic abc123", x_user_id="x", settings=_settings())
    assert ei.value.status_code == 401


def test_resolve_sso_on_invalid_token_is_401():
    with pytest.raises(HTTPException) as ei:
        resolve_user_id(authorization=f"Bearer {_token(aud='wrong')}",
                        x_user_id="x", settings=_settings())
    assert ei.value.status_code == 401


def test_resolve_sso_on_token_without_oid_is_401():
    with pytest.raises(HTTPException) as ei:
        resolve_user_id(authorization=f"Bearer {_token(oid=None)}",
                        x_user_id="x", settings=_settings())
    assert ei.value.status_code == 401


def test_resolve_sso_on_jwks_down_is_503(monkeypatch):
    monkeypatch.setattr(
        auth, "_jwks_client",
        lambda url: _FakeJWKClient(raise_exc=PyJWKClientConnectionError("dns fail", url)))
    with pytest.raises(HTTPException) as ei:
        resolve_user_id(authorization=f"Bearer {_token()}", x_user_id="x",
                        settings=_settings())
    assert ei.value.status_code == 503
```

Note: `_token(oid=None)` signs a payload with `oid=None`; PyJWT drops `None`-valued
claims on encode, so the decoded token has no `oid` → `attorney_id_from_claims` raises
`SSOValidationError` → 401. (Verified behavior; keep the test as written.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sso.py -k resolve -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_user_id' from 'api.auth'`.

- [ ] **Step 3: Implement `resolve_user_id`**

In `api/auth.py`, add to the imports at the top:

```python
from fastapi import Depends, Header, HTTPException

from config import Settings, get_settings
```

Then append the dependency (after `attorney_id_from_claims`):

```python
def resolve_user_id(
    authorization: str | None = Header(None),
    x_user_id: str = Header("anonymous", alias="X-User-ID"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Resolve the attorney id for this request.

    SSO off (default): return the X-User-ID header, exactly as before.
    SSO on: require a valid Bearer token and return its `oid`; 401 on a
    missing/invalid token, 503 when the JWKS endpoint is unreachable.
    """
    if not settings.sso_enabled:
        return x_user_id

    if not authorization or not authorization.lower().startswith("bearer "):
        logger.warning("[auth] SSO on but request has no Bearer token")
        raise HTTPException(status_code=401, detail="authentication required")

    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = validate_token(token, settings)
        return attorney_id_from_claims(claims)
    except SSOValidationError as e:
        logger.warning("[auth] token rejected: %s", e)
        raise HTTPException(status_code=401, detail="invalid token") from e
    except SSOConfigError as e:
        logger.error("[auth] cannot validate token (infra/config): %s", e)
        raise HTTPException(status_code=503, detail="authentication temporarily unavailable") from e
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sso.py -v`
Expected: PASS (all Task 1 + Task 2 tests, 20 total).

- [ ] **Step 5: Commit**

```bash
git add api/auth.py tests/test_sso.py
git commit -m "feat: resolve_user_id dependency — verified oid or X-User-ID fallback

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire the seam into the query route + pin dependency

**Files:**
- Modify: `api/routes/query.py` (line 7 import; line 128-144 `submit_query` signature + body)
- Modify: `requirements.txt` (pin `PyJWT[crypto]`)
- Test: `tests/test_query_auth.py` (new — route integration via `TestClient`)

**Interfaces:**
- Consumes: `resolve_user_id` from `api.auth` (Task 2).
- Produces: `submit_query` now takes `user_id: str = Depends(resolve_user_id)`; `initial_state["user_id"]` and the Langfuse `user_id` come from it. No other route changes (`resume_query` does not read identity).

- [ ] **Step 1: Write the failing route-integration tests**

Create `tests/test_query_auth.py`:

```python
"""The query route derives user_id via resolve_user_id (SSO seam)."""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import api.routes.query as q
from config import get_settings


def _fake_graph(captured):
    """A graph whose invoke records the initial_state it received."""
    class _G:
        def invoke(self, state, config=None):
            captured["state"] = state
            return {"task_type": "research", "report": {"response": "ok"}}
    return _G()


def test_sso_off_uses_x_user_id_header(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", False, raising=False)
    captured = {}
    with patch("api.routes.query._get_graph", return_value=_fake_graph(captured)), \
         patch("api.routes.query.refresh_ttl", lambda s: None):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           headers={"X-User-ID": "atty-localstorage-uuid"},
                           json={"request": "who signs?", "task_type": "research"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["state"]["user_id"] == "atty-localstorage-uuid"


def test_sso_off_missing_header_is_anonymous(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", False, raising=False)
    captured = {}
    with patch("api.routes.query._get_graph", return_value=_fake_graph(captured)), \
         patch("api.routes.query.refresh_ttl", lambda s: None):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           json={"request": "who signs?", "task_type": "research"})
    assert resp.status_code == 200
    assert captured["state"]["user_id"] == "anonymous"


def test_sso_on_without_token_is_401_and_graph_not_invoked(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", True, raising=False)
    graph_mock = MagicMock()
    with patch("api.routes.query._get_graph", return_value=graph_mock):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           headers={"X-User-ID": "spoofed"},
                           json={"request": "who signs?", "task_type": "research"})
    assert resp.status_code == 401
    graph_mock.invoke.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_query_auth.py -v`
Expected: FAIL — `test_sso_on_without_token_is_401...` fails (route still trusts the header, returns 200 and invokes the graph) because the seam isn't wired yet.

- [ ] **Step 3: Wire `resolve_user_id` into `submit_query`**

In `api/routes/query.py`, line 7, change:

```python
from fastapi import APIRouter, Header
```
to:
```python
from fastapi import APIRouter, Depends
```

(Confirm no other `Header(...)` usage remains in the file — `resume_query` and `query_status` take no header. If any `Header` use remains, keep `Header` in the import.)

Add the auth import near the other `from api...` imports (after line 12, `from api.models import ...`):

```python
from api.auth import resolve_user_id
```

Change the `submit_query` signature (lines 128-131) from:

```python
def submit_query(
    body: QueryRequest,
    x_user_id: str = Header("anonymous", alias="X-User-ID"),
):
```
to:
```python
def submit_query(
    body: QueryRequest,
    user_id: str = Depends(resolve_user_id),
):
```

Update the two body references — line 137 (`user_id=x_user_id,` inside `update_current_trace`) and line 144 (`"user_id": x_user_id,` inside `initial_state`) — replacing `x_user_id` with `user_id`:

```python
    langfuse_context.update_current_trace(
        name=f"query:{body.task_type or 'auto'}",
        user_id=user_id,
        session_id=session_id,
        input=body.request,
    )
```
```python
        "user_id": user_id,
```

- [ ] **Step 4: Run the route tests to verify they pass**

Run: `uv run pytest tests/test_query_auth.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Pin the dependency in `requirements.txt`**

Add a new section before `# Testing` (mirroring the file's `# Header` + `name>=x,<y` style):

```
# Auth (O365 SSO — slice 3; dormant until sso_enabled)
PyJWT[crypto]>=2.9,<3.0
```

(PyJWT 2.12.1 is already resolved in the venv as a transitive dep; `>=2.9` guarantees `PyJWKClientConnectionError`. No `uv pip install` needed for tests to pass, but pin it because we now depend on it directly.)

- [ ] **Step 6: Run the full suite — nothing regressed**

Run: `uv run pytest tests/ -q`
Expected: all pass, including the pre-existing `tests/test_query_degrade.py` / `tests/test_query_memory.py` (SSO off is the default, so the route is transparent).

- [ ] **Step 7: Commit**

```bash
git add api/routes/query.py requirements.txt tests/test_query_auth.py
git commit -m "feat: derive query user_id via resolve_user_id SSO seam; pin PyJWT

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Post-implementation (controller, not a task)

- Final whole-branch review (opus) pointed at any accumulated minors.
- Update `docs/wiki.md` (shipped row: slice 3 backend foundation; mark the SSO follow-up in-progress/partial) and the CLAUDE.md backend notes (the `sso_enabled` seam; `oid` anchor; deferred client work). Keep CLAUDE.md ≤150 lines.
- **No human sideload smoke this slice** — there is no client change to exercise, and SSO stays off. The deferred client-wiring follow-up is where the live Word/Azure smoke happens.
