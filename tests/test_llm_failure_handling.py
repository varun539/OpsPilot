"""Provider failure semantics for the investigation workflow (Phase 21.1).

All providers here are deterministic mocks: no network access is performed.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.agent.models import InvestigationPlan
from backend.app.incidents import IncidentService
from backend.app.llm.base import LLMResult, LLMUsage
from backend.app.llm.exceptions import (
    LLMIncompleteOutputError,
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
)
from backend.app.orchestration.schemas import InvestigationStartRequest
from backend.app.orchestration.service import InvestigationOrchestrationService
from backend.app.rag.generator import RAGService
from backend.app.rag.models import InvestigationReport

EVIDENCE_CHUNK_ID = "KB-008::chunk::0000::tok::0000"


class FakeRetrieval:
    """Minimal retrieval engine returning one deterministic chunk."""

    def __init__(self, chunk_id: str = EVIDENCE_CHUNK_ID) -> None:
        self.chunk_id = chunk_id
        self.calls: list[dict[str, Any]] = []

    def search(self, question: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"question": question, **kwargs})
        return [
            {
                "chunk_id": self.chunk_id,
                "doc_id": "KB-008",
                "title": "Connection pool exhaustion",
                "source_path": "docs/kb/KB-008.md",
                "chunk_text": "QueuePool exhausted after max_overflow.",
                "metadata": {},
                "final_rank": 1,
                "rrf_score": 0.5,
            }
        ]


class ScriptedProvider:
    """LLM provider double that fails or succeeds per output schema."""

    provider_name = "fake"
    model = "fake-gemini"

    def __init__(
        self,
        *,
        plan: InvestigationPlan | None = None,
        plan_error: Exception | None = None,
        report: Any = None,
        report_error: Exception | None = None,
    ) -> None:
        self.plan = plan
        self.plan_error = plan_error
        self.report = report
        self.report_error = report_error
        self.calls: list[str] = []

    def generate_structured(
        self,
        *,
        system_instructions: str,
        user_input: str,
        output_schema: Any,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        schema_name = output_schema.__name__
        self.calls.append(schema_name)
        if schema_name == "InvestigationPlan":
            if self.plan_error is not None:
                raise self.plan_error
            output = self.plan or InvestigationPlan(selected_tools=[], rationale="r")
        else:
            if self.report_error is not None:
                raise self.report_error
            output = self.report if self.report is not None else _insufficient_report()
        return LLMResult(output=output, model=self.model, usage=LLMUsage(total_tokens=10))


def _insufficient_report() -> InvestigationReport:
    return InvestigationReport(
        summary="No conclusion reached.",
        incident_category="insufficient_evidence",
        evidence_status="insufficient",
    )


def _grounded_report(chunk_id: str = EVIDENCE_CHUNK_ID) -> InvestigationReport:
    return InvestigationReport.model_validate(
        {
            "summary": "Connection pool exhaustion caused the 500s.",
            "incident_category": "database",
            "affected_components": ["api"],
            "evidence_status": "sufficient",
            "root_cause_hypotheses": [
                {
                    "title": "Pool exhaustion",
                    "explanation": "Connections were not released.",
                    "supporting_chunk_ids": [chunk_id],
                    "evidence_strength": "medium",
                }
            ],
            "recommended_actions": [
                {
                    "description": "Inspect pool sizing",
                    "rationale": "Evidence supports inspection.",
                    "supporting_chunk_ids": [chunk_id],
                    "risk_level": "low",
                    "requires_human_review": True,
                }
            ],
        }
    )


@pytest.fixture()
def make(tmp_path):
    created: list[InvestigationOrchestrationService] = []

    def _make(
        provider: Any,
        *,
        retrieval: Any = None,
        rag_service: Any = None,
        index: int = 0,
    ) -> tuple[IncidentService, InvestigationOrchestrationService]:
        service = IncidentService(db_path=str(tmp_path / f"app-{index}.sqlite"))
        orchestration = InvestigationOrchestrationService(
            incident_service=service,
            checkpoint_db_path=str(tmp_path / f"checkpoints-{index}.sqlite"),
            llm_provider=provider,
            retrieval_engine=retrieval,
            rag_service=rag_service,
        )
        created.append(orchestration)
        return service, orchestration

    yield _make
    for orchestration in created:
        orchestration.close()


def _start(
    service: IncidentService,
    orchestration: InvestigationOrchestrationService,
    **payload: Any,
):
    incident = service.create_incident(title="5xx spike")
    started = orchestration.start_investigation(
        incident.id,
        InvestigationStartRequest(logs="ERROR pool exhausted", **payload),
    )
    return incident, started


def _state(orchestration: InvestigationOrchestrationService, investigation_id: str) -> dict:
    snapshot = orchestration.agent.graph.get_state(
        {"configurable": {"thread_id": orchestration.get_investigation(investigation_id).thread_id}}
    )
    return dict(snapshot.values)


# 1. Planner failure is explicit, deterministic and observable.
def test_planner_timeout_records_fallback(make) -> None:
    provider = ScriptedProvider(plan_error=LLMTimeoutError("Gemini request timed out"))
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    state = _state(orchestration, started.investigation_id)
    assert state["planner_used"] is False
    assert state["fallback_used"] is True
    assert state["investigation_plan"]["selected_tools"] == ["log_analyzer"]
    assert "LLMTimeoutError" in state["planner_error"]
    assert any("deterministic fallback" in warning for warning in state["warnings"])
    assert started.workflow_status == "awaiting_approval"


# 2. A report 503 pauses the workflow with an explicitly invalid report.
def test_report_503_pauses_with_invalid_report(make) -> None:
    provider = ScriptedProvider(
        report_error=LLMResponseError("Gemini API request failed (status 503)")
    )
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    assert started.workflow_status == "awaiting_approval"
    assert started.approval_status == "pending"
    assert started.report_valid is False
    assert started.report_status == "provider_error"
    assert "failed" in started.message

    investigation = service.get_investigation(started.investigation_id)
    assert investigation.status == "running"
    assert investigation.report["generation_status"] == "provider_error"
    assert investigation.report["valid"] is False
    assert investigation.report["citation_valid"] is False
    # The failure is visible while the workflow is still paused for review.
    assert investigation.workflow_status == "awaiting_approval"


# 3. RAGService-level provider errors behave the same as node-level ones.
def test_rag_service_provider_error_is_not_valid(make) -> None:
    failing = ScriptedProvider(
        report_error=LLMResponseError("Gemini API request failed (status 503)")
    )
    rag = RAGService(retrieval_engine=FakeRetrieval(), llm_provider=failing)
    service, orchestration = make(
        ScriptedProvider(), retrieval=FakeRetrieval(), rag_service=rag
    )
    _, started = _start(service, orchestration)

    assert failing.calls == ["InvestigationReport"]
    assert started.workflow_status == "awaiting_approval"
    assert started.report_valid is False
    assert started.report_status == "provider_error"


# 4. Approval cannot launder a failed generation into a completed investigation.
def test_approval_after_failed_generation_is_not_completion(make) -> None:
    provider = ScriptedProvider(
        report_error=LLMResponseError("Gemini API request failed (status 503)")
    )
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    incident, started = _start(service, orchestration)

    response = orchestration.approve_investigation(
        started.investigation_id, "approved", reason="reviewed"
    )

    assert response.approval_status == "approved"
    assert response.workflow_status == "failed"
    assert response.report_valid is False
    investigation = service.get_investigation(started.investigation_id)
    assert investigation.workflow_status == "failed"
    assert investigation.status == "failed"
    assert investigation.last_error and "not valid" in investigation.last_error
    assert service.get_incident(incident.id).status == "investigating"
    assert service.list_resolutions(incident.id) == []

    event_types = [event.event_type for event in service.list_incident_events(incident.id)]
    assert "investigation_failed" in event_types
    assert "investigation_completed" not in event_types


# 5. Both planner and report fail: workflow still pauses, approval still fails safely.
def test_planner_and_report_failure_preserves_evidence(make) -> None:
    provider = ScriptedProvider(
        plan_error=LLMTimeoutError("Gemini request timed out"),
        report_error=LLMResponseError("Gemini API request failed (status 503)"),
    )
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    evidence = service.list_evidence(started.investigation_id)
    assert {item.evidence_type for item in evidence} == {"knowledge_base", "diagnostic"}
    assert any(item.source == EVIDENCE_CHUNK_ID for item in evidence)

    response = orchestration.approve_investigation(started.investigation_id, "approved")
    assert response.workflow_status == "failed"
    assert service.get_investigation(started.investigation_id).status == "failed"
    # Diagnostics remain available for follow-up work.
    assert len(service.list_evidence(started.investigation_id)) == len(evidence)


# 6. Malformed structured output is a provider error, not an empty report.
def test_invalid_model_response_is_provider_error(make) -> None:
    provider = ScriptedProvider(report={"summary": ""})
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    assert started.report_valid is False
    assert started.report_status == "provider_error"
    assert orchestration.approve_investigation(
        started.investigation_id, "approved"
    ).workflow_status == "failed"


# 7. Empty provider output is rejected.
def test_empty_output_is_provider_error(make) -> None:
    provider = ScriptedProvider(report="")
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    assert started.report_valid is False
    assert started.report_status == "provider_error"
    assert service.get_investigation(started.investigation_id).status == "running"


# 8. Provider refusal is surfaced as a failure, never as a report.
def test_provider_refusal_is_provider_error(make) -> None:
    provider = ScriptedProvider(report_error=LLMRefusalError("Gemini response was blocked"))
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    assert started.report_valid is False
    assert started.report_status == "provider_error"
    report = service.get_investigation(started.investigation_id).report
    assert report["evidence_status"] == "insufficient"


# 9. A synthesis exception is never marked valid, and secrets are redacted.
def test_synthesis_exception_is_not_valid(make) -> None:
    class BrokenRAG:
        def investigate_with_evidence(self, *args: Any, **kwargs: Any):
            raise RuntimeError("exploded with api_key=super-secret-value")

    service, orchestration = make(ScriptedProvider(), rag_service=BrokenRAG())
    _, started = _start(service, orchestration)

    assert started.report_valid is False
    assert started.report_status == "provider_error"
    report = service.get_investigation(started.investigation_id).report
    assert "super-secret-value" not in str(report)
    assert orchestration.approve_investigation(
        started.investigation_id, "approved"
    ).workflow_status == "failed"


# 10. Invented citations cannot be approved into a completed investigation.
def test_citation_validation_failure_blocks_completion(make) -> None:
    provider = ScriptedProvider(report=_grounded_report(chunk_id="KB-999::invented"))
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    assert started.workflow_status == "awaiting_approval"
    assert started.report_valid is False
    assert started.report_status == "citation_validation_failed"
    response = orchestration.approve_investigation(started.investigation_id, "approved")
    assert response.workflow_status == "failed"


# 11. A restart cannot approve stale output from a checkpoint with a failed report.
def test_recovery_from_checkpoint_with_failed_report(make, tmp_path) -> None:
    provider = ScriptedProvider(
        report_error=LLMResponseError("Gemini API request failed (status 503)")
    )
    service, orchestration = make(provider, retrieval=FakeRetrieval(), index=1)
    _, started = _start(service, orchestration)
    thread_id = service.get_investigation(started.investigation_id).thread_id
    orchestration.close()

    restored_service = IncidentService(db_path=str(tmp_path / "app-1.sqlite"))
    restored = InvestigationOrchestrationService(
        incident_service=restored_service,
        checkpoint_db_path=str(tmp_path / "checkpoints-1.sqlite"),
        llm_provider=provider,
    )
    try:
        investigation = restored_service.get_investigation(started.investigation_id)
        assert investigation.workflow_status == "awaiting_approval"
        assert investigation.thread_id == thread_id
        assert investigation.report["generation_status"] == "provider_error"

        response = restored.approve_investigation(started.investigation_id, "approved")
        assert response.workflow_status == "failed"
        assert response.report_valid is False
        assert restored_service.get_investigation(started.investigation_id).status == "failed"
        assert (
            restored.get_investigation(started.investigation_id).thread_id == thread_id
        )
    finally:
        restored.close()


# 12. Successful generation still reaches human review and can be completed.
def test_successful_generation_reaches_review_and_persists(make) -> None:
    provider = ScriptedProvider(report=_grounded_report())
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    assert started.workflow_status == "awaiting_approval"
    assert started.report_valid is True
    assert started.report_status == "success"
    assert "failed" not in started.message

    pending = service.get_investigation(started.investigation_id)
    assert pending.status == "running"
    assert pending.report["valid"] is True
    assert pending.report["citation_valid"] is True
    assert pending.report["generation_status"] == "success"

    response = orchestration.approve_investigation(
        started.investigation_id, "approved", reason="verified"
    )
    assert response.workflow_status == "completed"
    assert response.report_valid is True

    completed = service.get_investigation(started.investigation_id)
    assert completed.status == "completed"
    assert completed.workflow_status == "completed"
    assert completed.report["summary"] == "Connection pool exhaustion caused the 500s."
    assert completed.last_error is None
    assert "investigation_completed" in [
        event.event_type for event in service.list_incident_events(completed.incident_id)
    ]


# 13. A successful planner call leaves no degradation marker.
def test_successful_planner_clears_planner_error(make) -> None:
    provider = ScriptedProvider(
        plan=InvestigationPlan(selected_tools=["log_analyzer"], rationale="logs"),
        report=_grounded_report(),
    )
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    state = _state(orchestration, started.investigation_id)
    assert state["planner_used"] is True
    assert state["planner_error"] == ""
    assert not any("deterministic fallback" in warning for warning in state["warnings"])


# 14. A synthesis service that returns nothing is treated as a failure, not a report.
def test_synthesis_returning_none_is_provider_error(make) -> None:
    class EmptyRAG:
        def investigate_with_evidence(self, *args: Any, **kwargs: Any):
            return None

    service, orchestration = make(ScriptedProvider(), rag_service=EmptyRAG())
    _, started = _start(service, orchestration)

    assert started.report_valid is False
    assert started.report_status == "provider_error"


# 15. An incomplete-output provider error (truncated JSON) is handled like other failures.
def test_incomplete_output_is_provider_error(make) -> None:
    provider = ScriptedProvider(
        report_error=LLMIncompleteOutputError("Gemini output was truncated")
    )
    service, orchestration = make(provider, retrieval=FakeRetrieval())
    _, started = _start(service, orchestration)

    assert started.report_valid is False
    assert started.report_status == "provider_error"
