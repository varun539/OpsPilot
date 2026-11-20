"""Human approval integration tests for investigation orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.app.agent.models import InvestigationPlan
from backend.app.incidents import IncidentService
from backend.app.incidents.exceptions import InvalidStateTransitionError
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
            output = InvestigationPlan(selected_tools=[], rationale="r")
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


@pytest.fixture()
def setup(tmp_path):
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = InvestigationOrchestrationService(
        incident_service=service,
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        llm_provider=FakeProvider(),
    )
    incident = service.create_incident(title="failure")
    started = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR failure")
    )
    yield service, orchestration, incident, started.investigation_id
    orchestration.close()


def test_approved_resumes_same_thread_and_does_not_resolve(setup) -> None:
    service, orchestration, incident, investigation_id = setup
    thread_id = service.get_investigation(investigation_id).thread_id
    response = orchestration.approve_investigation(
        investigation_id, "approved", reason="looks good", principal=Principal("alice")
    )
    investigation = service.get_investigation(investigation_id)
    assert response.workflow_status == "completed"
    assert response.approval_status == "approved"
    assert investigation.thread_id == thread_id
    assert investigation.workflow_status == "completed"
    assert investigation.status == "completed"
    assert service.get_incident(incident.id).status == "investigating"
    assert service.list_resolutions(incident.id) == []
    assert len(service.list_investigations(incident.id)) == 1
    approval = orchestration.get_approval(investigation_id)
    assert approval.status == "approved"
    assert approval.decided_by == "alice"
    assert approval.decision_reason == "looks good"


def test_rejected_resumes_and_preserves_decision(setup) -> None:
    service, orchestration, incident, investigation_id = setup
    response = orchestration.approve_investigation(
        investigation_id, "rejected", reason="not enough evidence", principal=Principal("bob")
    )
    assert response.workflow_status == "completed"
    assert response.approval_status == "rejected"
    approval = orchestration.get_approval(investigation_id)
    assert approval.status == "rejected"
    assert approval.decided_by == "bob"
    assert service.get_incident(incident.id).status == "investigating"
    assert service.list_resolutions(incident.id) == []


def test_duplicate_approval_rejected(setup) -> None:
    _, orchestration, _, investigation_id = setup
    orchestration.approve_investigation(
        investigation_id, "approved", principal=Principal("alice")
    )
    with pytest.raises(InvalidStateTransitionError):
        orchestration.approve_investigation(
            investigation_id, "approved", principal=Principal("alice")
        )


def test_conflicting_decision_rejected(setup) -> None:
    _, orchestration, _, investigation_id = setup
    orchestration.approve_investigation(
        investigation_id, "approved", principal=Principal("alice")
    )
    with pytest.raises(InvalidStateTransitionError):
        orchestration.approve_investigation(
            investigation_id, "rejected", principal=Principal("bob")
        )


def test_approval_requires_waiting_state(tmp_path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = InvestigationOrchestrationService(
        incident_service=service,
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        llm_provider=FakeProvider(),
    )
    incident = service.create_incident(title="x")
    investigation = service.create_investigation(incident_id=incident.id)
    with pytest.raises(InvalidStateTransitionError):
        orchestration.approve_investigation(
            investigation.id, "approved", principal=Principal("alice")
        )
    orchestration.close()


def test_history_records_lifecycle_events(setup) -> None:
    service, orchestration, incident, investigation_id = setup
    orchestration.approve_investigation(
        investigation_id, "approved", reason="ok", principal=Principal("alice")
    )
    event_types = [event.event_type for event in service.list_incident_events(incident.id)]
    assert event_types == [
        "investigation_started",
        "human_review_requested",
        "approval_approved",
        "workflow_resumed",
        "investigation_completed",
    ]
    approval_event = [
        event
        for event in service.list_incident_events(incident.id)
        if event.event_type == "approval_approved"
    ][0]
    assert approval_event.actor == "alice"
    assert approval_event.metadata["decision"] == "approved"
