"""Tests for RAG report models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.rag.models import (
    EvidenceStatus,
    EvidenceStrength,
    InvestigationReport,
    RAGResponse,
)


def make_report(**overrides):
    base = {
        "summary": "Connection pool exhaustion likely caused timeouts.",
        "incident_category": "database",
        "affected_components": ["api", "database"],
        "evidence_status": "sufficient",
        "root_cause_hypotheses": [
            {
                "title": "Pool exhaustion",
                "explanation": "Connections were not released.",
                "supporting_chunk_ids": ["KB-008::chunk::0001"],
                "evidence_strength": "high",
            }
        ],
        "recommended_actions": [
            {
                "description": "Increase pool size",
                "rationale": "Matches retrieved guidance.",
                "supporting_chunk_ids": ["KB-008::chunk::0001"],
                "risk_level": "medium",
                "requires_human_review": True,
            }
        ],
        "missing_information": [],
        "limitations": [],
    }
    base.update(overrides)
    return base


def test_valid_report():
    report = InvestigationReport.model_validate(make_report())
    assert report.evidence_status == EvidenceStatus.sufficient
    assert report.root_cause_hypotheses[0].evidence_strength == EvidenceStrength.high


def test_invalid_evidence_status():
    with pytest.raises(ValidationError):
        InvestigationReport.model_validate(make_report(evidence_status="maybe"))


def test_missing_required_fields():
    with pytest.raises(ValidationError):
        InvestigationReport.model_validate({"summary": "x"})


def test_requires_human_review_cannot_be_false():
    data = make_report()
    data["recommended_actions"][0]["requires_human_review"] = False
    with pytest.raises(ValidationError):
        InvestigationReport.model_validate(data)


def test_insufficient_cannot_include_hypotheses():
    data = make_report(evidence_status="insufficient")
    with pytest.raises(ValidationError):
        InvestigationReport.model_validate(data)


def test_hypothesis_requires_supporting_ids():
    data = make_report()
    data["root_cause_hypotheses"][0]["supporting_chunk_ids"] = []
    with pytest.raises(ValidationError):
        InvestigationReport.model_validate(data)


def test_rag_response_requires_status():
    with pytest.raises(ValidationError):
        RAGResponse.model_validate(
            {
                "question": "q",
                "generation_status": "success",
                "valid": True,
                "citation_valid": True,
            }
        )
