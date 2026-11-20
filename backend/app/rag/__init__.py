"""Retrieval-augmented investigation pipeline for OpsPilot."""

from __future__ import annotations

from .citation_validator import CitationValidationError, CitationValidator
from .context_builder import ContextBuilder, EvidenceContext, build_context
from .generator import RAGService
from .models import (
    EvidenceReference,
    EvidenceStatus,
    EvidenceStrength,
    InvestigationReport,
    RAGResponse,
    RecommendedAction,
    RetrievedEvidence,
    RiskLevel,
    RootCauseHypothesis,
)
from .prompts import SYSTEM_PROMPT

__all__ = [
    "CitationValidator",
    "CitationValidationError",
    "ContextBuilder",
    "build_context",
    "EvidenceContext",
    "RAGService",
    "EvidenceStatus",
    "EvidenceStrength",
    "InvestigationReport",
    "RAGResponse",
    "RetrievedEvidence",
    "RiskLevel",
    "RootCauseHypothesis",
    "RecommendedAction",
    "EvidenceReference",
    "SYSTEM_PROMPT",
]
