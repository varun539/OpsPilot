"""Tests for RAG citation validation."""

from __future__ import annotations

import pytest

from backend.app.rag.citation_validator import CitationValidationError, CitationValidator
from backend.app.rag.models import InvestigationReport


def _report(*chunk_ids):
    return InvestigationReport.model_validate(
        {
            "summary": "summary",
            "incident_category": "database",
            "affected_components": ["db"],
            "evidence_status": "sufficient",
            "root_cause_hypotheses": [
                {
                    "title": "cause",
                    "explanation": "explanation",
                    "supporting_chunk_ids": list(chunk_ids),
                    "evidence_strength": "medium",
                }
            ],
            "recommended_actions": [],
            "missing_information": [],
            "limitations": [],
        }
    )


def test_valid_citation():
    report = _report("c1")
    validator = CitationValidator()
    result = validator.validate(report, [{"chunk_id": "c1"}])
    assert result.valid is True
    assert result.unknown_ids == set()


def test_unknown_citation():
    report = _report("unknown")
    validator = CitationValidator()
    result = validator.validate(report, [{"chunk_id": "c1"}])
    assert result.valid is False
    assert "unknown" in result.unknown_ids


def test_cross_query_citation_rejected():
    report = _report("old-chunk")
    validator = CitationValidator()
    result = validator.validate(report, [{"chunk_id": "new-chunk"}])
    assert result.valid is False


def test_multiple_references():
    report = _report("c1", "c2")
    validator = CitationValidator()
    result = validator.validate(report, [{"chunk_id": "c1"}, {"chunk_id": "c2"}])
    assert result.valid is True
    assert result.referenced_ids == {"c1", "c2"}


def test_missing_reference():
    report = InvestigationReport.model_validate(
        {
            "summary": "summary",
            "incident_category": "database",
            "affected_components": [],
            "evidence_status": "insufficient",
            "root_cause_hypotheses": [],
            "recommended_actions": [],
            "missing_information": [],
            "limitations": [],
        }
    )
    validator = CitationValidator()
    result = validator.validate(report, [{"chunk_id": "c1"}])
    assert result.valid is True
    assert result.referenced_ids == set()


def test_validate_or_raise():
    report = _report("unknown")
    validator = CitationValidator()
    with pytest.raises(CitationValidationError):
        validator.validate_or_raise(report, [{"chunk_id": "c1"}])


def test_supporting_evidence_reference():
    report = InvestigationReport.model_validate(
        {
            "summary": "summary",
            "incident_category": "database",
            "affected_components": [],
            "evidence_status": "sufficient",
            "root_cause_hypotheses": [],
            "supporting_evidence": [{"chunk_id": "c1"}],
            "recommended_actions": [],
            "missing_information": [],
            "limitations": [],
        }
    )
    validator = CitationValidator()
    result = validator.validate(report, [{"chunk_id": "c1"}])
    assert result.valid is True
