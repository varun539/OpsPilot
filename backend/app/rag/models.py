"""Validated models for the OpsPilot RAG investigation pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceStatus(str, Enum):
    sufficient = "sufficient"
    insufficient = "insufficient"


class EvidenceStrength(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RetrievedEvidence(BaseModel):
    """One evidence chunk supplied to the RAG model."""

    model_config = ConfigDict(extra="allow")

    chunk_id: str = Field(min_length=1)
    doc_id: str = ""
    title: str = ""
    source_path: str = ""
    chunk_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    semantic_rank: int | None = None
    bm25_rank: int | None = None
    rrf_score: float | None = None
    final_rank: int | None = None


class EvidenceReference(BaseModel):
    """A structured reference to an evidence chunk."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    doc_id: str = ""
    title: str = ""
    reason: str = ""


class RootCauseHypothesis(BaseModel):
    """A possible root cause, explicitly tied to evidence."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    supporting_chunk_ids: list[str] = Field(min_length=1)
    evidence_strength: EvidenceStrength


class RecommendedAction(BaseModel):
    """A recommended next step. Production actions are never auto-executed."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    supporting_chunk_ids: list[str] = Field(default_factory=list)
    risk_level: RiskLevel
    requires_human_review: bool = True

    @field_validator("requires_human_review")
    @classmethod
    def _review_required(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("recommended actions must require human review")
        return value


class InvestigationReport(BaseModel):
    """Structured investigation output produced by the LLM."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    incident_category: str = Field(min_length=1)
    affected_components: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus
    root_cause_hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    supporting_evidence: list[EvidenceReference | str] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_evidence_rules(self) -> "InvestigationReport":
        if self.evidence_status == EvidenceStatus.insufficient and self.root_cause_hypotheses:
            raise ValueError("insufficient evidence cannot include root-cause hypotheses")
        for hypothesis in self.root_cause_hypotheses:
            if not hypothesis.supporting_chunk_ids:
                raise ValueError("root-cause hypothesis requires supporting chunk ids")
        return self


class RAGResponse(BaseModel):
    """Final validated response returned by the RAG service."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    report: InvestigationReport
    retrieved_evidence: list[RetrievedEvidence] = Field(default_factory=list)
    source_references: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    usage: dict[str, Any] | None = None
    generation_status: str
    valid: bool
    citation_valid: bool
    validation_errors: list[str] = Field(default_factory=list)
