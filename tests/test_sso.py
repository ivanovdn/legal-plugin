"""O365 SSO token validation — fully offline (in-test RSA keypair, mocked JWKS)."""
import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.exceptions import DecodeError, PyJWKClientConnectionError

import api.auth as auth
from api.auth import (
    SSOConfigError,
    SSOValidationError,
    attorney_id_from_claims,
    resolve_user_id,
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


def test_hs256_token_rejected():
    # RS256 allow-list must reject an HS256-signed token even though the
    # (fake) JWKS lookup succeeds and reaches jwt.decode(algorithms=["RS256"]).
    now = int(time.time())
    payload = dict(oid="attorney-oid-123", aud=CLIENT_ID, iss=ISSUER,
                   exp=now + 3600, iat=now)
    token = jwt.encode(payload, "shared-secret-at-least-32-bytes-long!!", algorithm="HS256")
    with pytest.raises(SSOValidationError):
        validate_token(token, _settings())


def test_alg_none_token_rejected():
    # An unsigned alg:none token must also be rejected by the RS256 allow-list.
    now = int(time.time())
    payload = dict(oid="attorney-oid-123", aud=CLIENT_ID, iss=ISSUER,
                   exp=now + 3600, iat=now)
    try:
        token = jwt.encode(payload, key=None, algorithm="none")
    except (TypeError, NotImplementedError, jwt.exceptions.InvalidKeyError):
        # PyJWT may refuse to encode with key=None — build the token by hand.
        header = jwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
        body = jwt.utils.base64url_encode(json.dumps(payload).encode()).decode()
        token = f"{header}.{body}."
    with pytest.raises(SSOValidationError):
        validate_token(token, _settings())
