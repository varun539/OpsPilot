"""Schemas for the OpsPilot evaluation system.

Evaluation cases are synthetic and versioned. The production runtime never
imports this module. LLM output is never scored by exact text matching; cases
label diagnostic categories, tool choices, evidence and abstention.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.agent.models import InvestigationRequest

EvaluationMode = Literal["offline", "local", "live"]
SuiteName = Literal["diagnostics", "retrieval", "agent", "reliability", "all"]
ToolName = Literal[
    "log_analyzer",
    "stack_trace_analyzer",
    "http_error_classifier",
    "config_validator",
    "database_diagnostics",
    "incident_correlator",
]

DOC_ID_RE = re.compile(r"^KB-\d{3}$")


class ExpectedWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_interrupt: bool = True
    should_complete: bool = True
    resume_decision: Literal["approved", "rejected"] = "approved"


class ExpectedIncident(BaseModel):
    """Labels used by the agent and grounding evaluators."""

    model_config = ConfigDict(extra="forbid")

    diagnostic_tools: list[str] = Field(default_factory=list)
    diagnostic_finding_codes: list[str] = Field(default_factory=list)
    relevant_doc_ids: list[str] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    root_cause_category: str = ""
    abstain: bool = False
    report_characteristics: list[str] = Field(default_factory=list)
    workflow: ExpectedWorkflow = Field(default_factory=ExpectedWorkflow)

    @field_validator("relevant_doc_ids")
    @classmethod
    def _valid_docs(cls, values: list[str]) -> list[str]:
        for value in values:
            if not DOC_ID_RE.match(value):
                raise ValueError(f"invalid knowledge document id {value!r}")
        if len(set(values)) != len(values):
            raise ValueError("relevant_doc_ids must be unique")
        return values

    @field_validator("expected_chunk_ids")
    @classmethod
    def _valid_chunks(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith("KB-") or "::" not in value:
                raise ValueError(f"invalid expected chunk id {value!r}")
        return values

    @field_validator("diagnostic_tools")
    @classmethod
    def _valid_tools(cls, values: list[str]) -> list[str]:
        allowed = {
            "log_analyzer",
            "stack_trace_analyzer",
            "http_error_classifier",
            "config_validator",
            "database_diagnostics",
            "incident_correlator",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown diagnostic tools: {sorted(unknown)}")
        return values


class SecurityExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adversarial: bool = False
    prompt_injection: bool = False
    secret_values: list[str] = Field(default_factory=list)


class IncidentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    request: InvestigationRequest
    expected: ExpectedIncident
    security: SecurityExpectations = Field(default_factory=SecurityExpectations)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _abstention_is_consistent(self) -> "IncidentCase":
        if self.expected.abstain and self.expected.root_cause_category not in (
            "",
            "insufficient_evidence",
        ):
            raise ValueError(
                "abstaining cases must use root_cause_category "
                "'insufficient_evidence' or omit it"
            )
        return self


class DiagnosticCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tool: ToolName
    inputs: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)
    expected_status: str | None = None
    expected_finding_codes: list[str] = Field(default_factory=list)
    forbidden_finding_codes: list[str] = Field(default_factory=list)
    expect_no_findings: bool = False
    expect_exception: bool = False
    secret_values: list[str] = Field(default_factory=list)
    expect_truncated: bool = False
    tags: list[str] = Field(default_factory=list)


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    relevant_doc_ids: list[str] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    filters: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)


class RetrievalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int | str
    description: str = ""
    queries: list[RetrievalQuery] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "RetrievalDataset":
        ids = [query.id for query in self.queries]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate retrieval query ids")
        return self


class CaseResult(BaseModel):
    """One evaluated case; metrics stay JSON-serializable."""

    case_id: str
    suite: str
    passed: bool
    metrics: dict[str, Any] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    mode: str = "offline"


class SuiteResult(BaseModel):
    suite: str
    mode: str
    dataset_version: str
    cases: list[CaseResult]
    metrics: dict[str, Any]
    limitations: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def failed(self) -> int:
        return len(self.cases) - self.passed
