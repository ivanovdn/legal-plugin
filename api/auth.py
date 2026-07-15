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
