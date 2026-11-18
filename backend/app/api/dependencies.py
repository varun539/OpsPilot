"""API dependencies for services and authentication."""

from __future__ import annotations

from fastapi import Request

from backend.app.auth.dependencies import (
    get_current_principal,
    require_admin,
    require_operator,
    require_viewer,
)
from backend.app.incidents import IncidentService

__all__ = [
    "get_incident_service",
    "get_current_principal",
    "require_viewer",
    "require_operator",
    "require_admin",
]


def get_incident_service(request: Request) -> IncidentService:
    return request.app.state.incident_service
