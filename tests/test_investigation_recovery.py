"""Failure handling and restart recovery tests for investigation orchestration."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from backend.app.agent.models import InvestigationPlan
from backend.app.incidents import IncidentService
from backend.app.incidents.exceptions import InvalidStateTransitionError
from backend.app.orchestration.exceptions import (
    InvestigationResumeError,
    InvestigationStartError,
)
from backend.app.orchestration.schemas import InvestigationStartRequest
from backend.app.orchestration.service import InvestigationOrchestrationService
from backend.app.rag.models import InvestigationReport


class FakeProvider:
    model = "fake"

    def generate_structured(
        self,
        *,
        system_instructions,
        user_input,
        output_schema,
        timeout=None,
        max_output_tokens=None,
    ):
        if output_schema.__name__ == "InvestigationPlan":
            output = InvestigationPlan(
                selected_tools=["log_analyzer"], rationale="logs"
            )
        else:
            output = InvestigationReport(
                summary="s",
                incident_category="x",
                evidence_status="insufficient",
            )
        result = MagicMock()
        result.output = output
        result.usage = None
        result.model = "fake"
        return result


class Principal:
    def __init__(self, subject: str) -> None:
        self.subject = subject


def _orchestration(tmp_path, incident_service=None):
    return InvestigationOrchestrationService(
        incident_service=incident_service,
        db_path=None if incident_service else str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        llm_provider=FakeProvider(),
    )


def test_restart_recovery_resumes_original_thread(tmp_path) -> None:
    app_db = str(tmp_path / "app.sqlite")
    checkpoint_db = str(tmp_path / "checkpoints.sqlite")

    service = IncidentService(db_path=app_db)
    incident = service.create_incident(title="restart")
    orchestration = InvestigationOrchestrationService(
        incident_service=service,
        checkpoint_db_path=checkpoint_db,
        llm_provider=FakeProvider(),
    )
    started = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR restart")
    )
    investigation_id = started.investigation_id
    thread_id = service.get_investigation(investigation_id).thread_id
    assert thread_id
    orchestration.close()
    service.database.close()

    # Reconstruct the application database service and the LangGraph checkpointer.
    service2 = IncidentService(db_path=app_db)
    orchestration2 = InvestigationOrchestrationService(
        incident_service=service2,
        checkpoint_db_path=checkpoint_db,
        llm_provider=FakeProvider(),
    )
    assert service2.get_investigation(investigation_id).workflow_status == "awaiting_approval"
    assert service2.get_investigation(investigation_id).thread_id == thread_id

    response = orchestration2.approve_investigation(
        investigation_id, "approved", reason="resume", principal=Principal("alice")
    )
    assert response.workflow_status == "completed"
    investigation = service2.get_investigation(investigation_id)
    assert investigation.workflow_status == "completed"
    assert investigation.status == "completed"
    assert investigation.thread_id == thread_id
    assert len(service2.list_investigations(incident.id)) == 1
    assert service2.get_incident(incident.id).status == "investigating"
    orchestration2.close()


def test_resume_failure_preserves_approval_and_allows_retry(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = _orchestration(tmp_path, service)
    incident = service.create_incident(title="resume failure")
    started = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR x")
    )
    investigation_id = started.investigation_id
    original_resume = orchestration.agent.resume

    def failing_resume(**kwargs):
        raise RuntimeError("graph unavailable")

    orchestration.agent.resume = failing_resume
    with pytest.raises(InvestigationResumeError):
        orchestration.approve_investigation(
            investigation_id, "approved", principal=Principal("alice")
        )

    investigation = service.get_investigation(investigation_id)
    approval = orchestration.get_approval(investigation_id)
    assert investigation.workflow_status == "resume_failed"
    assert investigation.last_error
    assert approval.status == "approved"
    assert approval.decided_by == "alice"

    orchestration.agent.resume = original_resume
    retried = orchestration.approve_investigation(
        investigation_id, "approved", principal=Principal("alice")
    )
    assert retried.workflow_status == "completed"
    assert service.get_investigation(investigation_id).workflow_status == "completed"
    assert len(service.list_approvals(investigation_id=investigation_id)) == 1
    orchestration.close()


def test_conflicting_retry_after_resume_failure_rejected(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = _orchestration(tmp_path, service)
    incident = service.create_incident(title="conflict")
    started = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR x")
    )

    def failing_resume(**kwargs):
        raise RuntimeError("graph unavailable")

    orchestration.agent.resume = failing_resume
    with pytest.raises(InvestigationResumeError):
        orchestration.approve_investigation(
            started.investigation_id, "approved", principal=Principal("alice")
        )
    with pytest.raises(InvalidStateTransitionError):
        orchestration.approve_investigation(
            started.investigation_id, "rejected", principal=Principal("bob")
        )
    orchestration.close()


def test_final_report_persistence_failure_is_recoverable(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = _orchestration(tmp_path, service)
    incident = service.create_incident(title="persistence failure")
    started = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR x")
    )
    investigation_id = started.investigation_id
    original_persist = service.persist_pending_report
    calls = {"count": 0}

    def flaky_persist(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] >= 1:
            raise RuntimeError("database locked")
        return original_persist(*args, **kwargs)

    service.persist_pending_report = flaky_persist
    with pytest.raises(InvestigationResumeError):
        orchestration.approve_investigation(
            investigation_id, "approved", principal=Principal("alice")
        )
    investigation = service.get_investigation(investigation_id)
    assert investigation.workflow_status == "resume_failed"
    assert orchestration.get_approval(investigation_id).status == "approved"

    service.persist_pending_report = original_persist
    retried = orchestration.approve_investigation(
        investigation_id, "approved", principal=Principal("alice")
    )
    assert retried.workflow_status == "completed"
    assert service.get_investigation(investigation_id).report is not None
    orchestration.close()


def test_evidence_persistence_failure_marks_failed(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = _orchestration(tmp_path, service)
    incident = service.create_incident(title="evidence failure")
    original = service.store_evidence

    def failing_store(*args, **kwargs):
        raise RuntimeError("disk full")

    service.store_evidence = failing_store
    with pytest.raises(InvestigationStartError):
        orchestration.start_investigation(
            incident.id, InvestigationStartRequest(logs="ERROR x")
        )
    service.store_evidence = original
    investigations = service.list_investigations(incident.id)
    assert investigations[0].workflow_status == "failed"
    assert investigations[0].status == "failed"
    assert investigations[0].last_error
    assert service.list_approvals(incident_id=incident.id) == []
    orchestration.close()


def test_graph_failure_marks_failed_without_leaking_secrets(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = _orchestration(tmp_path, service)
    incident = service.create_incident(title="graph failure")

    def failing_investigate(*args, **kwargs):
        raise RuntimeError("failure with api_key=super-secret-value")

    orchestration.agent.investigate = failing_investigate
    with pytest.raises(InvestigationStartError):
        orchestration.start_investigation(
            incident.id, InvestigationStartRequest(logs="ERROR x")
        )
    investigation = service.list_investigations(incident.id)[0]
    assert investigation.workflow_status == "failed"
    assert investigation.status == "failed"
    assert "super-secret-value" not in (investigation.last_error or "")
    assert "RuntimeError" in (investigation.last_error or "")
    orchestration.close()


def test_evidence_not_duplicated_during_resume(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = _orchestration(tmp_path, service)
    incident = service.create_incident(title="dedupe")
    started = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR x")
    )
    before = len(service.list_evidence(started.investigation_id))
    orchestration.approve_investigation(
        started.investigation_id, "approved", principal=Principal("alice")
    )
    after = len(service.list_evidence(started.investigation_id))
    assert after == before
    orchestration.close()


def test_concurrent_approvals_only_one_wins(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = _orchestration(tmp_path, service)
    incident = service.create_incident(title="concurrent")
    started = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR x")
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def decide() -> None:
        barrier.wait()
        try:
            orchestration.approve_investigation(
                started.investigation_id, "approved", principal=Principal("alice")
            )
            result = "ok"
        except (InvalidStateTransitionError, InvestigationResumeError):
            result = "error"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=decide) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert outcomes.count("ok") == 1
    assert outcomes.count("error") == 1
    assert len(service.list_approvals(investigation_id=started.investigation_id)) == 1
    orchestration.close()
