"""FastAPI authentication and authorization dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from backend.app.auth.config import AuthSettings
from backend.app.auth.exceptions import AuthConfigurationError, InvalidTokenError
from backend.app.auth.jwt import JWTVerifier, _role_rank
from backend.app.auth.models import Principal, TenantContext
from backend.app.auth.tenant import is_valid_tenant_id


def get_auth_settings(request: Request) -> AuthSettings:
    return request.app.state.auth_settings


def _is_dev_mode(settings: AuthSettings) -> bool:
    return (
        settings.auth_mode == "development"
        and settings.dev_auth_enabled
        and settings.is_loopback_host()
    )


def _dev_tenant_id(settings: AuthSettings) -> str | None:
    """Server-configured development tenant, or None when unset/invalid.

    No tenant is ever invented for development: an absent value leaves the dev
    principal without tenant identity so tenant-scoped access fails closed.
    """
    tenant_id = settings.dev_tenant_id
    if isinstance(tenant_id, str) and is_valid_tenant_id(tenant_id):
        return tenant_id
    return None


def get_current_principal(
    request: Request,
    settings: AuthSettings = Depends(get_auth_settings),
) -> Principal:
    if _is_dev_mode(settings):
        return Principal(
            subject="dev-user",
            roles=["admin"],
            scopes=[],
            tenant_id=_dev_tenant_id(settings),
        )

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


def get_tenant_context(
    principal: Principal = Depends(get_current_principal),
) -> TenantContext:
    """Return the verified tenant context or fail closed with HTTP 403.

    Tenant identity is taken only from the authenticated principal, which in
    turn comes from verified JWT claims or server-side development
    configuration. Request headers, query parameters, bodies and paths are
    never consulted, so a client cannot select its own tenant.
    """
    tenant_id = principal.tenant_id
    if not isinstance(tenant_id, str) or not is_valid_tenant_id(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant context required",
        )
    return TenantContext(tenant_id=tenant_id, actor_subject=principal.subject)


# Phase 26.1 exposes the tenant dependency for later route enforcement but does
# not apply it to any existing API route.
require_tenant = get_tenant_context


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
