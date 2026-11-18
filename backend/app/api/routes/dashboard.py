"""Read-only dashboard and approval-queue endpoints.

These endpoints only aggregate and join records through the existing
``IncidentService``; they add no new lifecycle behavior.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.app.api.dependencies import get_incident_service, require_viewer
from backend.app.api.schemas import ApprovalQueueItem, BreakdownItem, DashboardStats
from backend.app.incidents import IncidentService
from backend.app.incidents.models import ApprovalStatus, Investigation, TicketStatus

router = APIRouter(tags=["dashboard"])

_OPEN_TICKET_STATUSES = (
    TicketStatus.open.value,
    TicketStatus.in_progress.value,
    TicketStatus.blocked.value,
)


def _breakdown(values: dict[str, int]) -> list[BreakdownItem]:
    return [
        BreakdownItem(name=name, count=count)
        for name, count in sorted(values.items(), key=lambda item: item[1], reverse=True)
    ]


@router.get(
    "/dashboard/stats",
    response_model=DashboardStats,
    summary="Retrieve aggregate incident, ticket, and investigation statistics",
)
def get_dashboard_stats(
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> DashboardStats:
    incidents_by_status = service.incident_breakdown("status")
    incidents_by_severity = service.incident_breakdown("severity")
    investigations_by_status = service.investigation_breakdown("status")
    investigations_by_workflow = service.investigation_breakdown("workflow_status")

    return DashboardStats(
        total_incidents=sum(incidents_by_status.values()),
        open_incidents=incidents_by_status.get("open", 0),
        investigating_incidents=incidents_by_status.get("investigating", 0),
        awaiting_approval_incidents=incidents_by_status.get("awaiting_approval", 0),
        resolved_incidents=incidents_by_status.get("resolved", 0),
        total_tickets=service.count_tickets(),
        open_tickets=sum(service.count_tickets(status=status) for status in _OPEN_TICKET_STATUSES),
        total_investigations=sum(investigations_by_status.values()),
        pending_approvals=service.count_approvals(status=ApprovalStatus.pending.value),
        completed_investigations=investigations_by_workflow.get("completed", 0),
        failed_investigations=(
            investigations_by_workflow.get("failed", 0)
            + investigations_by_workflow.get("resume_failed", 0)
        ),
        incidents_by_severity=_breakdown(incidents_by_severity),
        incidents_by_status=_breakdown(incidents_by_status),
        investigations_by_workflow_status=_breakdown(investigations_by_workflow),
    )


@router.get(
    "/approvals",
    response_model=list[ApprovalQueueItem],
    summary="List approvals with incident and investigation context",
)
def list_approvals(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[ApprovalQueueItem]:
    approvals = service.list_approvals(status=status, limit=limit, offset=offset)
    items: list[ApprovalQueueItem] = []
    for approval in approvals:
        incident = service.repository.get_incident(approval.incident_id)
        if incident is None:
            continue
        investigation = None
        if approval.investigation_id:
            investigation = service.repository.get_investigation(approval.investigation_id)
        items.append(
            ApprovalQueueItem(approval=approval, incident=incident, investigation=investigation)
        )
    return items


@router.get(
    "/investigations",
    response_model=list[Investigation],
    summary="List investigations across incidents",
)
def list_investigations(
    status: str | None = Query(default=None),
    workflow_status: str | None = Query(default=None),
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: IncidentService = Depends(get_incident_service),
    principal: Any = Depends(require_viewer),
) -> list[Investigation]:
    return service.list_all_investigations(
        limit=limit,
        offset=offset,
        status=status,
        workflow_status=workflow_status,
        incident_id=incident_id,
    )
