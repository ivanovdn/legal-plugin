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
