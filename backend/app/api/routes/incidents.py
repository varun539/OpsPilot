"""Incident endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query

from backend.app.api.dependencies import get_incident_service, require_operator, require_viewer
from backend.app.api.schemas import ErrorResponse, TicketCreateRequest
from backend.app.incidents import IncidentService
from backend.app.incidents.models import Approval, Incident, Investigation, Resolution, Ticket
from backend.app.incidents.schemas import IncidentCreate, IncidentUpdate

router = APIRouter(prefix="/incidents", tags=["incidents"])

_MAX_LIMIT = 100


@router.post(
    "",
    response_model=Incident,
    status_code=201,
    summary="Create an incident",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
def create_incident(
    payload: IncidentCreate,
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_operator),
) -> Incident:
    return service.create_incident(payload)


@router.get(
    "",
    response_model=list[Incident],
    summary="List incidents",
)
def list_incidents(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Incident]:
    return service.list_incidents(limit=limit, offset=offset, status=status, severity=severity)


@router.get(
    "/{incident_id}",
    response_model=Incident,
    summary="Retrieve an incident",
)
def get_incident(
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> Incident:
    return service.get_incident(incident_id)


@router.patch(
    "/{incident_id}",
    response_model=Incident,
    summary="Update permitted incident fields",
)
def update_incident(
    payload: IncidentUpdate,
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_operator),
) -> Incident:
    return service.update_incident(incident_id, payload)


@router.get(
    "/{incident_id}/history",
    response_model=dict[str, Any],
    summary="Retrieve incident history",
)
def get_incident_history(
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> dict[str, Any]:
    history = service.get_incident_history(incident_id)
    return {
        "incident": history["incident"],
        "tickets": history.get("tickets", []),
        "investigations": history.get("investigations", []),
        "approvals": history.get("approvals", []),
        "resolutions": history.get("resolutions", []),
        "events": history.get("events", []),
    }


@router.get(
    "/{incident_id}/tickets",
    response_model=list[Ticket],
    summary="List tickets for an incident",
)
def list_incident_tickets(
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Ticket]:
    return service.list_tickets(incident_id)


@router.post(
    "/{incident_id}/tickets",
    response_model=Ticket,
    status_code=201,
    summary="Create a ticket for an incident",
)
def create_incident_ticket(
    payload: TicketCreateRequest,
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_operator),
) -> Ticket:
    data = payload.model_dump()
    data["incident_id"] = incident_id
    return service.create_ticket(data)


@router.get(
    "/{incident_id}/investigations",
    response_model=list[Investigation],
    summary="List investigations for an incident",
)
def list_incident_investigations(
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Investigation]:
    return service.list_investigations(incident_id)


@router.get(
    "/{incident_id}/approvals",
    response_model=list[Approval],
    summary="Retrieve approval history for an incident",
)
def list_incident_approvals(
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Approval]:
    return service.list_approvals(incident_id=incident_id)


@router.get(
    "/{incident_id}/resolutions",
    response_model=list[Resolution],
    summary="Retrieve resolution history for an incident",
)
def list_incident_resolutions(
    incident_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Resolution]:
    return service.list_resolutions(incident_id)
