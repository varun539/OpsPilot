"""FastAPI authentication and authorization dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from backend.app.auth.config import AuthSettings
from backend.app.auth.exceptions import AuthConfigurationError, InvalidTokenError
from backend.app.auth.jwt import JWTVerifier, _role_rank
from backend.app.auth.models import Principal


def get_auth_settings(request: Request) -> AuthSettings:
    return request.app.state.auth_settings


def _is_dev_mode(settings: AuthSettings) -> bool:
    return (
        settings.auth_mode == "development"
        and settings.dev_auth_enabled
        and settings.is_loopback_host()
    )


def get_current_principal(
    request: Request,
    settings: AuthSettings = Depends(get_auth_settings),
) -> Principal:
    if _is_dev_mode(settings):
        return Principal(subject="dev-user", roles=["admin"], scopes=[])

    # Production (or non-enabled development) mode.
    if settings.auth_mode == "development":
        # Development mode without explicit bypass must still use JWT if configured.
        if not settings.production_configured():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication not configured",
            )
    elif not settings.production_configured():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication not configured",
        )

    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )

    # Reuse one verifier per app so the JWKS document and signing keys stay
    # cached. Building a verifier per request would fetch JWKS on every call and
    # defeat the rotation cache entirely.
    verifier = getattr(request.app.state, "jwt_verifier", None)
    if verifier is None or verifier.settings is not settings:
        verifier = JWTVerifier(settings)
        request.app.state.jwt_verifier = verifier
    try:
        return verifier.verify(token)
    except (InvalidTokenError, AuthConfigurationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        )


def require_min_role(minimum: str):
    minimum_rank = _role_rank(minimum)

    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if _role_rank(max(principal.roles, key=_role_rank, default="viewer")) < minimum_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient permissions",
            )
        return principal

    return dependency


require_viewer = require_min_role("viewer")
require_operator = require_min_role("operator")
require_admin = require_min_role("admin")
