"""Schemas for OpsPilot investigation orchestration."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.agent.models import MAX_EVENTS, MAX_INPUT_CHARS, InvestigationRequest

_MAX_TEXT_FIELD_CHARS = 200_000
_MAX_EVENTS = MAX_EVENTS
_MAX_INPUT_CHARS = MAX_INPUT_CHARS


class InvestigationStartRequest(BaseModel):
    """Client-supplied investigation inputs.

    Mirrors the existing agent request model but makes ``question`` optional so
    callers can start from stored incident context alone.
    """

    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, min_length=1, max_length=10000)
    incident_context: str | None = Field(default=None, max_length=_MAX_TEXT_FIELD_CHARS)
    logs: str | None = Field(default=None, max_length=_MAX_TEXT_FIELD_CHARS)
    stack_trace: str | None = Field(default=None, max_length=_MAX_TEXT_FIELD_CHARS)
    http_status: int | None = None
    response_text: str | None = Field(default=None, max_length=100_000)
    response_headers: dict[str, str] | None = None
    configuration: dict[str, Any] | None = None
    configuration_rules: dict[str, Any] | None = None
    database_error: str | None = Field(default=None, max_length=_MAX_TEXT_FIELD_CHARS)
    events: list[dict[str, Any]] | None = None
    retrieval_filters: dict[str, Any] | None = None
    requested_top_k: int | None = Field(default=None, ge=1, le=50)

    @field_validator("http_status")
    @classmethod
    def _validate_status(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not 100 <= int(value) <= 599:
            raise ValueError("http_status must be between 100 and 599")
        return int(value)

    @field_validator("events")
    @classmethod
    def _validate_events(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        if len(value) > _MAX_EVENTS:
            raise ValueError(f"events must contain at most {_MAX_EVENTS} items")
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("each event must be an object")
        return value

    @model_validator(mode="after")
    def _validate_total_size(self) -> "InvestigationStartRequest":
        total = 0
        for field_name in (
            "question",
            "incident_context",
            "logs",
            "stack_trace",
            "response_text",
            "database_error",
        ):
            value = getattr(self, field_name)
            if value:
                total += len(value)
        if total > _MAX_INPUT_CHARS:
            raise ValueError(f"combined text input must not exceed {_MAX_INPUT_CHARS} characters")
        for field_name in (
            "configuration",
            "configuration_rules",
            "response_headers",
            "events",
            "retrieval_filters",
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            try:
                serialized = json.dumps(value, default=str)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} must be JSON serializable") from exc
            if len(serialized) > _MAX_INPUT_CHARS:
                raise ValueError(f"{field_name} must not exceed {_MAX_INPUT_CHARS} characters")
        return self

    def to_agent_request(self, fallback_question: str) -> InvestigationRequest:
        data = self.model_dump(exclude_none=True)
        if not data.get("question"):
            data["question"] = fallback_question
        return InvestigationRequest.model_validate(data)


class InvestigationStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    investigation_id: str
    workflow_status: str
    approval_status: str | None = None
    message: str
    # Additive: lets clients distinguish a normal review pause from a pause
    # caused by a failed report generation.
    report_valid: bool | None = None
    report_status: str | None = None


class InvestigationApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str | None = Field(default=None, max_length=4000)

    @field_validator("decision")
    @classmethod
    def _validate_decision(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError("decision must be 'approved' or 'rejected'")
        return normalized


class InvestigationApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: str
    incident_id: str | None = None
    workflow_status: str
    approval_status: str | None = None
    message: str
    result: dict[str, Any] | None = None
    report_valid: bool | None = None
    report_status: str | None = None
