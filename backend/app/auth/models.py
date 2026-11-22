"""Normalized authentication principal and tenant context models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Principal:
    subject: str
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    # Verified tenant identity, or None while tenancy is not yet asserted by the
    # token issuer. Never populated from client-controlled input.
    tenant_id: str | None = None


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Verified tenant identity for the authenticated actor.

    Immutable by design: downstream services receive a value they cannot
    mutate into a different tenant. The acting subject is carried alongside the
    tenant so callers never have to re-derive the authenticated identity.
    """

    tenant_id: str
    actor_subject: str
