"""Ticket endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query

from backend.app.api.dependencies import get_incident_service, require_operator, require_viewer
from backend.app.incidents import IncidentService
from backend.app.incidents.models import Ticket
from backend.app.incidents.schemas import TicketUpdate

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get(
    "",
    response_model=list[Ticket],
    summary="List tickets",
)
def list_tickets(
    status: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Ticket]:
    return service.list_all_tickets(limit=limit, offset=offset, status=status, priority=priority)


@router.post(
    "/{ticket_id}/close",
    response_model=Ticket,
    summary="Close a ticket",
)
def close_ticket(
    ticket_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_operator),
) -> Ticket:
    return service.close_ticket(ticket_id)


@router.get(
    "/{ticket_id}",
    response_model=Ticket,
    summary="Retrieve a ticket",
)
def get_ticket(
    ticket_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> Ticket:
    return service.get_ticket(ticket_id)


@router.patch(
    "/{ticket_id}",
    response_model=Ticket,
    summary="Update permitted ticket fields",
)
def update_ticket(
    payload: TicketUpdate,
    ticket_id: str = Path(..., min_length=1),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_operator),
) -> Ticket:
    return service.update_ticket(ticket_id, payload)
