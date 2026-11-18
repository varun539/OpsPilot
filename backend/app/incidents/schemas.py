"""Validated request and response schemas for incident management."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.incidents.models import (
    ALLOWED_PRIORITIES,
    ALLOWED_SEVERITIES,
    IncidentStatus,
    TicketStatus,
)


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    severity: str = "medium"
    priority: str = "medium"
    source: str | None = None
    environment: str | None = None
    service_name: str | None = None
    status: str = IncidentStatus.open.value

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, value: str) -> str:
        if value not in ALLOWED_SEVERITIES:
            raise ValueError("invalid severity")
        return value

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str) -> str:
        if value not in ALLOWED_PRIORITIES:
            raise ValueError("invalid priority")
        return value

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in {s.value for s in IncidentStatus}:
            raise ValueError("invalid incident status")
        return value


class IncidentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    severity: str | None = None
    priority: str | None = None
    source: str | None = None
    environment: str | None = None
    service_name: str | None = None

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_SEVERITIES:
            raise ValueError("invalid severity")
        return value

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_PRIORITIES:
            raise ValueError("invalid priority")
        return value


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    priority: str = "medium"
    assignee: str | None = None
    status: str = TicketStatus.open.value

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str) -> str:
        if value not in ALLOWED_PRIORITIES:
            raise ValueError("invalid priority")
        return value

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in {s.value for s in TicketStatus}:
            raise ValueError("invalid ticket status")
        return value


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    assignee: str | None = None
    closed_at: str | None = None

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_PRIORITIES:
            raise ValueError("invalid priority")
        return value

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in {s.value for s in TicketStatus}:
            raise ValueError("invalid ticket status")
        return value


class InvestigationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    status: str = "started"
    summary: str | None = None
    suspected_root_cause: str | None = None
    thread_id: str | None = None
    workflow_status: str | None = None
    last_error: str | None = None


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    investigation_id: str | None = None
    approval_type: str = "human_review"


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    decided_by: str | None = None
    reason: str | None = None

    @field_validator("decision")
    @classmethod
    def _validate_decision(cls, value: str) -> str:
        if value not in {"approved", "rejected"}:
            raise ValueError("invalid approval decision")
        return value


class ResolutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    investigation_id: str | None = None
    resolution_summary: str = Field(min_length=1)
    resolution_details: str | None = None
    resolved_by: str | None = None
    resolution_status: str = "recorded"


class EvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    source: str | None = None
    content: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
