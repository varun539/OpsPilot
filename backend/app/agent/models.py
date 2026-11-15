"""Validated models for the OpsPilot agent."""


from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InvestigationRequest(BaseModel):
    """Validated request for an OpsPilot investigation."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=10000)
    incident_context: str | None = None
    logs: str | None = None
    stack_trace: str | None = None
    http_status: int | None = None
    response_text: str | None = None
    response_headers: dict[str, str] | None = None
    configuration: dict[str, Any] | None = None
    configuration_rules: dict[str, Any] | None = None
    database_error: str | None = None
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
        if not isinstance(value, list):
            raise ValueError("events must be a list")
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("each event must be an object")
        return value

    @model_validator(mode="after")
    def _validate_inputs(self) -> "InvestigationRequest":
        if self.requested_top_k is not None and self.requested_top_k < 1:
            raise ValueError("requested_top_k must be positive")
        return self


class InvestigationPlan(BaseModel):
    """Structured plan produced by the investigation planner."""

    model_config = ConfigDict(extra="forbid")

    selected_tools: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=4000)
    available_evidence: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    proposed_steps: list[str] = Field(default_factory=list)
    fallback: bool = False


ALLOWED_TOOLS = [
    "log_analyzer",
    "stack_trace_analyzer",
    "http_error_classifier",
    "config_validator",
    "database_diagnostics",
    "incident_correlator",
]

MAX_TOOLS = 6
MAX_PLANNER_STEPS = 12
MAX_INPUT_CHARS = 200000
MAX_EVENTS = 200