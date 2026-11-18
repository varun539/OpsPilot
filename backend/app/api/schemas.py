"""API response and error schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.app.incidents.models import Approval, Incident, Investigation


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: dict[str, Any]


class TicketCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    priority: str = "medium"
    assignee: str | None = None
    status: str = "open"
    incident_id: str | None = None


class BreakdownItem(BaseModel):
    name: str
    count: int


class DashboardStats(BaseModel):
    """Read-only aggregate statistics calculated from stored records."""

    total_incidents: int
    open_incidents: int
    investigating_incidents: int
    awaiting_approval_incidents: int
    resolved_incidents: int
    total_tickets: int
    open_tickets: int
    total_investigations: int
    pending_approvals: int
    completed_investigations: int
    failed_investigations: int
    incidents_by_severity: list[BreakdownItem]
    incidents_by_status: list[BreakdownItem]
    investigations_by_workflow_status: list[BreakdownItem]


class ApprovalQueueItem(BaseModel):
    """An approval together with the incident and investigation it references."""

    approval: Approval
    incident: Incident
    investigation: Investigation | None = None
