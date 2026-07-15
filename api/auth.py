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
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWTError

from config import Settings, get_settings

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
    if not (settings.sso_client_id and (settings.sso_tenant_id or (settings.sso_issuer and settings.sso_jwks_url))):
        raise SSOConfigError(
            "SSO enabled but sso_client_id / sso_tenant_id (or sso_issuer + sso_jwks_url) not configured"
        )

    try:
        signing_key = _jwks_client(_jwks_url(settings)).get_signing_key_from_jwt(token)
    except PyJWKClientConnectionError as e:
        raise SSOConfigError("JWKS endpoint unreachable") from e
    except PyJWTError as e:
        # malformed token / no matching kid — reject the token
        raise SSOValidationError(f"cannot resolve signing key: {e.__class__.__name__}") from e
    except Exception as e:
        # e.g. JWKS returned HTTP 200 with a non-JSON body — infra/config failure
        raise SSOConfigError(f"JWKS key resolution failed: {e.__class__.__name__}") from e

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
        logger.warning("[auth] SSO on but request has no valid Bearer token")
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
