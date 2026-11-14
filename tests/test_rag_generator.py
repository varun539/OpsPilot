"""Tests for RAG orchestration."""

from __future__ import annotations

import pytest

from backend.app.llm.base import LLMResult
from backend.app.rag.generator import RAGService
from backend.app.rag.models import InvestigationReport


class FakeRetrieval:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, question, **kwargs):
        self.calls.append((question, kwargs))
        return self.results


class FakeProvider:
    def __init__(self, report=None, error=None):
        self.model = "fake-model"
        self.report = report
        self.error = error
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return LLMResult(output=self.report, model=self.model, usage={"total_tokens": 10})


def _report(chunk_id="c1"):
    return InvestigationReport.model_validate(
        {
            "summary": "Likely pool exhaustion.",
            "incident_category": "database",
            "affected_components": ["db"],
            "evidence_status": "sufficient",
            "root_cause_hypotheses": [
                {
                    "title": "Pool exhaustion",
                    "explanation": "Connections not released.",
                    "supporting_chunk_ids": [chunk_id],
                    "evidence_strength": "medium",
                }
            ],
            "recommended_actions": [
                {
                    "description": "Inspect pool",
                    "rationale": "Evidence supports inspection.",
                    "supporting_chunk_ids": [chunk_id],
                    "risk_level": "low",
                    "requires_human_review": True,
                }
            ],
            "missing_information": [],
            "limitations": [],
        }
    )


def _evidence():
    return [
        {
            "chunk_id": "c1",
            "doc_id": "KB-008",
            "title": "Pool",
            "source_path": "docs/pool.md",
            "chunk_text": "QueuePool exhausted.",
            "metadata": {},
            "final_rank": 1,
            "rrf_score": 0.5,
        }
    ]


def test_retrieval_to_context_and_generation():
    retrieval = FakeRetrieval(_evidence())
    provider = FakeProvider(report=_report())
    service = RAGService(retrieval_engine=retrieval, llm_provider=provider)
    response = service.investigate("pool exhausted", top_k=3)
    assert response.generation_status == "success"
    assert response.valid is True
    assert response.citation_valid is True
    assert response.retrieved_evidence[0].chunk_id == "c1"
    assert response.retrieval_metadata["retrieved_count"] == 1
    assert provider.calls


def test_insufficient_evidence_empty_retrieval():
    retrieval = FakeRetrieval([])
    provider = FakeProvider(report=_report())
    service = RAGService(retrieval_engine=retrieval, llm_provider=provider)
    response = service.investigate("unknown issue")
    assert response.generation_status == "insufficient_evidence"
    assert response.report.evidence_status.value == "insufficient"
    assert provider.calls == []


def test_provider_error():
    retrieval = FakeRetrieval(_evidence())
    provider = FakeProvider(error=RuntimeError("boom"))
    service = RAGService(retrieval_engine=retrieval, llm_provider=provider)
    response = service.investigate("pool exhausted")
    assert response.generation_status == "provider_error"
    assert response.valid is False
    assert "boom" in response.validation_errors[0]


def test_invalid_citations():
    retrieval = FakeRetrieval(_evidence())
    provider = FakeProvider(report=_report(chunk_id="unknown"))
    service = RAGService(retrieval_engine=retrieval, llm_provider=provider)
    response = service.investigate("pool exhausted")
    assert response.generation_status == "citation_validation_failed"
    assert response.citation_valid is False
    assert response.valid is False


def test_repeated_requests():
    retrieval = FakeRetrieval(_evidence())
    provider = FakeProvider(report=_report())
    service = RAGService(retrieval_engine=retrieval, llm_provider=provider)
    r1 = service.investigate("q1")
    r2 = service.investigate("q2")
    assert r1.question == "q1"
    assert r2.question == "q2"


def test_metadata_preservation():
    retrieval = FakeRetrieval(_evidence())
    provider = FakeProvider(report=_report())
    service = RAGService(retrieval_engine=retrieval, llm_provider=provider)
    response = service.investigate("pool exhausted", filters={"doc_id": "KB-008"})
    assert response.retrieval_metadata["filters"] == {"doc_id": "KB-008"}


def test_invalid_question():
    service = RAGService(retrieval_engine=FakeRetrieval([]), llm_provider=FakeProvider())
    with pytest.raises(ValueError):
        service.investigate("   ")
