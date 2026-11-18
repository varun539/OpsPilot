"""Domain models for OpsPilot incident management."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IncidentStatus(str, Enum):
    open = "open"
    investigating = "investigating"
    awaiting_approval = "awaiting_approval"
    resolved = "resolved"
    closed = "closed"
    reopened = "reopened"


class TicketStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    blocked = "blocked"
    resolved = "resolved"
    closed = "closed"


class InvestigationStatus(str, Enum):
    started = "started"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ApprovalType(str, Enum):
    human_review = "human_review"
    change_approval = "change_approval"


class ResolutionStatus(str, Enum):
    recorded = "recorded"
    superseded = "superseded"


ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
ALLOWED_PRIORITIES = {"critical", "high", "medium", "low", "urgent", "p1", "p2", "p3", "p4"}


INCIDENT_TRANSITIONS: dict[str, set[str]] = {
    IncidentStatus.open.value: {
        IncidentStatus.investigating.value,
        IncidentStatus.closed.value,
    },
    IncidentStatus.investigating.value: {
        IncidentStatus.awaiting_approval.value,
        IncidentStatus.resolved.value,
        IncidentStatus.closed.value,
    },
    IncidentStatus.awaiting_approval.value: {
        IncidentStatus.investigating.value,
        IncidentStatus.resolved.value,
        IncidentStatus.closed.value,
    },
    IncidentStatus.resolved.value: {
        IncidentStatus.reopened.value,
        IncidentStatus.closed.value,
    },
    IncidentStatus.closed.value: {
        IncidentStatus.reopened.value,
    },
    IncidentStatus.reopened.value: {
        IncidentStatus.investigating.value,
        IncidentStatus.closed.value,
    },
}

TICKET_TRANSITIONS: dict[str, set[str]] = {
    TicketStatus.open.value: {
        TicketStatus.in_progress.value,
        TicketStatus.blocked.value,
        TicketStatus.closed.value,
    },
    TicketStatus.in_progress.value: {
        TicketStatus.blocked.value,
        TicketStatus.resolved.value,
        TicketStatus.closed.value,
    },
    TicketStatus.blocked.value: {
        TicketStatus.in_progress.value,
        TicketStatus.closed.value,
    },
    TicketStatus.resolved.value: {
        TicketStatus.closed.value,
        TicketStatus.open.value,
    },
    TicketStatus.closed.value: set(),
}


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    incident_number: str
    title: str
    description: str | None = None
    severity: str
    priority: str
    status: str
    source: str | None = None
    environment: str | None = None
    service_name: str | None = None
    created_at: str
    updated_at: str
    resolved_at: str | None = None


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ticket_number: str
    incident_id: str
    title: str
    description: str | None = None
    priority: str
    status: str
    assignee: str | None = None
    created_at: str
    updated_at: str
    closed_at: str | None = None


class Investigation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    incident_id: str
    investigation_number: str
    status: str
    summary: str | None = None
    suspected_root_cause: str | None = None
    report: dict[str, Any] | None = None
    thread_id: str | None = None
    workflow_status: str | None = None
    last_error: str | None = None
    started_at: str
    completed_at: str | None = None
    created_at: str
    updated_at: str


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    investigation_id: str
    evidence_type: str
    source: str | None = None
    content: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    incident_id: str
    investigation_id: str | None = None
    approval_type: str
    status: str
    requested_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: str


class IncidentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    incident_id: str
    event_type: str
    actor: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    incident_id: str
    investigation_id: str | None = None
    resolution_summary: str
    resolution_details: str | None = None
    resolved_by: str | None = None
    resolution_status: str
    created_at: str
