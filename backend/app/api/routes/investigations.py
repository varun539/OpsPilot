"""Investigation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path

from backend.app.api.dependencies import get_incident_service, require_viewer
from backend.app.incidents import IncidentService
from backend.app.incidents.models import Evidence, Investigation

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.get(
    "/{investigation_id}",
    response_model=Investigation,
    summary="Retrieve an investigation",
)
def get_investigation(
    investigation_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> Investigation:
    return service.get_investigation(investigation_id)


@router.get(
    "/{investigation_id}/evidence",
    response_model=list[Evidence],
    summary="Retrieve investigation evidence",
)
def get_investigation_evidence(
    investigation_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Evidence]:
    return service.list_evidence(investigation_id)
