"""Shared models for OpsPilot investigation tools.

These models are intentionally deterministic, serializable, and free of any
LLM, network, or filesystem side effects.  They are designed so that a
future LangGraph layer can wrap the individual tool functions without
changing their contracts.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(str, Enum):
    """Shared severity vocabulary for diagnostic findings."""

    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    informational = "informational"


class DiagnosticFinding(BaseModel):
    """One observed diagnostic finding produced by a tool."""

    model_config = ConfigDict(extra="allow")

    finding_id: str = ""
    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: Severity = Severity.informational
    evidence: list[str] = Field(default_factory=list)
    source: str = ""
    remediation_suggestions: list[str] = Field(default_factory=list)
    observed_facts: list[str] = Field(default_factory=list)
    detected_patterns: list[str] = Field(default_factory=list)
    potential_explanations: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "evidence",
        "remediation_suggestions",
        "observed_facts",
        "detected_patterns",
        "potential_explanations",
        "recommended_next_steps",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        return [str(value)]

    def model_post_init(self, __context: Any) -> None:  # pragma: no cover - simple
        if not self.finding_id:
            self.finding_id = self.code


class DiagnosticResult(BaseModel):
    """Standard result envelope returned by every investigation tool."""

    model_config = ConfigDict(extra="allow")

    tool_name: str = Field(min_length=1)
    status: str = Field(min_length=1)
    summary: str = ""
    findings: list[DiagnosticFinding] = Field(default_factory=list)
    analyzed_items: int = 0
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    observed_facts: list[str] = Field(default_factory=list)
    detected_patterns: list[str] = Field(default_factory=list)
    potential_explanations: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)


class CorrelationGroup(BaseModel):
    """A group of events/findings proposed to be related."""

    model_config = ConfigDict(extra="allow")

    group_id: str = Field(min_length=1)
    event_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    shared_evidence: list[str] = Field(default_factory=list)
    timeline: list[Any] = Field(default_factory=list)
    possible_dependency_relationships: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    correlation_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CorrelationResult(BaseModel):
    """Result envelope returned by the incident correlator."""

    model_config = ConfigDict(extra="allow")

    tool_name: str = "incident_correlator"
    status: str = Field(min_length=1)
    summary: str = ""
    correlation_groups: list[CorrelationGroup] = Field(default_factory=list)
    groups: list[CorrelationGroup] = Field(default_factory=list)
    findings: list[DiagnosticFinding] = Field(default_factory=list)
    analyzed_items: int = 0
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    observed_facts: list[str] = Field(default_factory=list)
    detected_patterns: list[str] = Field(default_factory=list)
    potential_explanations: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        # Keep the convenience alias in sync with the canonical field.
        if self.groups and not self.correlation_groups:
            self.correlation_groups = list(self.groups)
        if self.correlation_groups and not self.groups:
            self.groups = list(self.correlation_groups)


class InvestigationToolError(Exception):
    """Base error for investigation tool failures."""


class InputValidationError(InvestigationToolError, ValueError):
    """Raised when supplied input does not satisfy validation rules."""


class InputSizeError(InvestigationToolError, ValueError):
    """Raised when input exceeds configured limits and truncation is disallowed."""
