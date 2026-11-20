"""Service-level tests for end-to-end investigation orchestration."""

from __future__ import annotations

import sqlite3
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
            output = InvestigationPlan(
                selected_tools=["log_analyzer"],
                rationale="logs are available",
            )
        else:
            output = InvestigationReport(
                summary="database pool exhausted",
                incident_category="database",
                evidence_status="insufficient",
                root_cause_hypotheses=[],
                supporting_evidence=[],
                recommended_actions=[],
                missing_information=[],
                limitations=[],
            )
        result = MagicMock()
        result.output = output
        result.usage = None
        result.model = "fake"
        return result


class FakeRetrieval:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query, top_k=5, filters=None):
        self.calls += 1
        return [
            {
                "chunk_id": "chunk-1",
                "doc_id": "doc-1",
                "title": "Connection pool guide",
                "source_path": "kb/pool.md",
                "chunk_text": "Increase the pool size.",
            }
        ]


class Principal:
    subject = "alice"


@pytest.fixture()
def setup(tmp_path):
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    retrieval = FakeRetrieval()
    orchestration = InvestigationOrchestrationService(
        incident_service=service,
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        llm_provider=FakeProvider(),
        retrieval_engine=retrieval,
    )
    incident = service.create_incident(title="database timeouts", severity="high")
    yield service, orchestration, incident, retrieval
    orchestration.close()


def test_start_persists_thread_and_interrupts(setup) -> None:
    service, orchestration, incident, _ = setup
    response = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR pool exhausted")
    )
    assert response.workflow_status == "awaiting_approval"
    assert response.approval_status == "pending"
    investigation = service.get_investigation(response.investigation_id)
    assert investigation.thread_id
    assert investigation.thread_id.startswith("thread-")
    assert investigation.workflow_status == "awaiting_approval"
    assert investigation.report is not None
    assert investigation.report["summary"] == "database pool exhausted"
    assert service.get_incident(incident.id).status == "awaiting_approval"
    approval = orchestration.get_approval(investigation.id)
    assert approval.status == "pending"


def test_start_persists_report_and_evidence(setup) -> None:
    service, orchestration, incident, retrieval = setup
    response = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR pool exhausted")
    )
    assert retrieval.calls == 1
    evidence = service.list_evidence(response.investigation_id)
    types = {item.evidence_type for item in evidence}
    assert "diagnostic" in types
    assert "knowledge_base" in types
    knowledge = [item for item in evidence if item.evidence_type == "knowledge_base"]
    assert knowledge[0].content["chunk_id"] == "chunk-1"
    assert knowledge[0].metadata["doc_id"] == "doc-1"
    assert knowledge[0].metadata["source_path"] == "kb/pool.md"


def test_incident_context_is_passed_to_agent(setup) -> None:
    service, orchestration, incident, _ = setup
    captured = {}
    original = orchestration.agent.investigate

    def spy(request, *, thread_id=None, investigation_id=None):
        captured["question"] = request.question
        captured["context"] = request.incident_context
        captured["thread_id"] = thread_id
        captured["investigation_id"] = investigation_id
        return original(
            request, thread_id=thread_id, investigation_id=investigation_id
        )

    orchestration.agent.investigate = spy
    response = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(question="Why timeouts?")
    )
    investigation = service.get_investigation(response.investigation_id)
    assert captured["question"] == "Why timeouts?"
    assert incident.incident_number in captured["context"]
    assert captured["thread_id"] == investigation.thread_id
    assert captured["investigation_id"] == investigation.id


def test_duplicate_active_investigation_rejected(setup) -> None:
    _, orchestration, incident, _ = setup
    orchestration.start_investigation(incident.id, InvestigationStartRequest(logs="ERROR x"))
    with pytest.raises(InvalidStateTransitionError):
        orchestration.start_investigation(
            incident.id, InvestigationStartRequest(logs="ERROR y")
        )


def test_missing_incident_rejected(setup) -> None:
    _, orchestration, _, _ = setup
    with pytest.raises(Exception):
        orchestration.start_investigation(
            "missing", InvestigationStartRequest(logs="ERROR x")
        )


def test_server_generated_thread_ids_are_unique(setup) -> None:
    service, orchestration, incident, _ = setup
    first = orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR x")
    )
    first_thread = service.get_investigation(first.investigation_id).thread_id

    second_incident = service.create_incident(title="second")
    second = orchestration.start_investigation(
        second_incident.id, InvestigationStartRequest(logs="ERROR y")
    )
    second_thread = service.get_investigation(second.investigation_id).thread_id
    assert first_thread != second_thread


def test_insufficient_input_still_interrupts(setup) -> None:
    service, orchestration, incident, _ = setup
    response = orchestration.start_investigation(incident.id, InvestigationStartRequest())
    investigation = service.get_investigation(response.investigation_id)
    assert investigation.workflow_status == "awaiting_approval"
    assert investigation.report is not None


def test_migration_preserves_existing_phase8_records(tmp_path) -> None:
    db_path = str(tmp_path / "legacy.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE incidents (
            id TEXT PRIMARY KEY,
            incident_number TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            severity TEXT NOT NULL,
            priority TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT,
            environment TEXT,
            service_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE investigations (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            investigation_number TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            summary TEXT,
            suspected_root_cause TEXT,
            report TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO incidents VALUES (
            'inc-1', 'INC-LEGACY', 'legacy', NULL, 'high', 'high', 'investigating',
            NULL, NULL, NULL, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', NULL
        );
        INSERT INTO investigations VALUES (
            'inv-1', 'inc-1', 'INV-LEGACY', 'completed', 'old summary', 'old cause',
            '{\"summary\": \"old\"}', '2026-01-01T00:00:00+00:00',
            '2026-01-01T01:00:00+00:00', '2026-01-01T00:00:00+00:00',
            '2026-01-01T01:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()

    service = IncidentService(db_path=db_path)
    investigation = service.get_investigation("inv-1")
    assert investigation.summary == "old summary"
    assert investigation.report == {"summary": "old"}
    assert investigation.thread_id is None
    assert investigation.workflow_status is None
    assert service.get_incident("inc-1").incident_number == "INC-LEGACY"

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(investigations)")}
    conn.close()
    assert {"thread_id", "workflow_status", "last_error"} <= columns


def test_application_and_checkpoint_databases_are_separate(tmp_path, setup) -> None:
    _, orchestration, incident, _ = setup
    orchestration.start_investigation(
        incident.id, InvestigationStartRequest(logs="ERROR x")
    )
    app_tables = _tables(tmp_path / "app.sqlite")
    checkpoint_tables = _tables(tmp_path / "checkpoints.sqlite")
    assert "incidents" in app_tables
    assert "investigations" in app_tables
    assert "checkpoints" not in app_tables
    assert "incidents" not in checkpoint_tables


def _tables(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
