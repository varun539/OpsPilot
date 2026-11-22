"""Authentication configuration for OpsPilot REST API."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from backend.app.auth.tenant import is_valid_tenant_id

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_valid_claim_path(value: object) -> bool:
    """Claim names may be dotted paths, but never empty or padded segments."""
    if not isinstance(value, str) or not value:
        return False
    if value != value.strip():
        return False
    return all(segment and segment == segment.strip() for segment in value.split("."))


@dataclass
class AuthSettings:
    host: str = field(default_factory=lambda: os.getenv("OPSPILOT_HOST", "127.0.0.1"))
    auth_mode: str = field(default_factory=lambda: os.getenv("OPSPILOT_AUTH_MODE", "production"))
    dev_auth_enabled: bool = field(
        default_factory=lambda: _bool(os.getenv("OPSPILOT_DEV_AUTH_ENABLED"), False)
    )
    issuer: str | None = field(default_factory=lambda: os.getenv("OPSPILOT_AUTH_ISSUER") or None)
    audience: str | None = field(
        default_factory=lambda: os.getenv("OPSPILOT_AUTH_AUDIENCE") or None
    )
    jwks_url: str | None = field(
        default_factory=lambda: os.getenv("OPSPILOT_AUTH_JWKS_URL") or None
    )
    roles_claim: str = field(
        default_factory=lambda: os.getenv("OPSPILOT_AUTH_ROLES_CLAIM", "roles")
    )
    # Dotted claim paths are supported by the shared claim extraction helper.
    tenant_claim: str = field(
        default_factory=lambda: os.getenv("OPSPILOT_AUTH_TENANT_CLAIM", "tenant_id")
    )
    # Development-only tenant identity. No default is invented: absent means the
    # dev principal has no tenant and tenant-scoped access fails closed.
    dev_tenant_id: str | None = field(
        default_factory=lambda: os.getenv("OPSPILOT_DEV_TENANT_ID") or None
    )
    algorithms: list[str] = field(
        default_factory=lambda: (
            _list(os.getenv("OPSPILOT_AUTH_ALGORITHMS"))
            or [
                "RS256",
                "RS384",
                "RS512",
                "ES256",
                "ES384",
                "ES512",
                "PS256",
                "PS384",
                "PS512",
            ]
        )
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _list(os.getenv("OPSPILOT_CORS_ORIGINS"))
    )

    def is_loopback_host(self) -> bool:
        host = (self.host or "").strip().lower()
        if host in _LOOPBACK_HOSTS:
            return True
        if host.startswith("127."):
            return True
        return False

    def production_configured(self) -> bool:
        if not (self.issuer and self.audience and self.jwks_url):
            return False
        if self.auth_mode == "production":
            parsed = urlparse(self.jwks_url)
            hostname = (parsed.hostname or "").lower()
            if (
                parsed.scheme != "https"
                and hostname not in _LOOPBACK_HOSTS
                and not hostname.startswith("127.")
            ):
                return False
        return True

    def configuration_errors(self) -> list[str]:
        """Return startup-blocking configuration problems.

        Only environment variable names are mentioned; values are never
        included in error messages.
        """
        errors: list[str] = []
        if self.auth_mode not in {"development", "production"}:
            errors.append("OPSPILOT_AUTH_MODE must be 'development' or 'production'")
            return errors

        if self.auth_mode == "production":
            if self.dev_auth_enabled:
                errors.append("OPSPILOT_DEV_AUTH_ENABLED must not be set in production mode")
            if not (self.issuer and self.audience and self.jwks_url):
                errors.append(
                    "production requires OPSPILOT_AUTH_ISSUER, OPSPILOT_AUTH_AUDIENCE, "
                    "and OPSPILOT_AUTH_JWKS_URL"
                )
            elif not self.production_configured():
                errors.append(
                    "OPSPILOT_AUTH_JWKS_URL must use https outside loopback in production"
                )
        elif self.dev_auth_enabled and not self.is_loopback_host():
            errors.append("development authentication requires a loopback OPSPILOT_HOST")

        # A malformed development tenant id can never satisfy tenant-scoped
        # access, so surface the misconfiguration at startup instead of at the
        # first tenant-scoped request. Only the variable name is reported.
        if self.dev_tenant_id is not None and not is_valid_tenant_id(self.dev_tenant_id):
            errors.append("OPSPILOT_DEV_TENANT_ID must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

        # A malformed claim name silently yields no tenant, which would quietly
        # turn tenant-scoped access into a permanent 403. Reject it at startup.
        if not _is_valid_claim_path(self.tenant_claim):
            errors.append(
                "OPSPILOT_AUTH_TENANT_CLAIM must be a non-empty claim name or dotted path "
                "without empty segments"
            )
        return errors


def validate_runtime_configuration(settings: AuthSettings) -> None:
    """Fail closed at application startup instead of serving unauthenticated traffic."""
    errors = settings.configuration_errors()
    if errors:
        raise RuntimeError("invalid OpsPilot authentication configuration: " + "; ".join(errors))
