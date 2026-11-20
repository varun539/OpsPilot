from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.incidents import IncidentService
from backend.app.incidents.database import ApplicationDatabase
from backend.app.incidents.exceptions import (
    IncidentNotFoundError,
    InvalidInputError,
    InvalidStateTransitionError,
)


def make_service(tmp_path: Path) -> IncidentService:
    db = tmp_path / "app.sqlite"
    return IncidentService(db_path=db)


def test_database_initializes(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite"
    db = ApplicationDatabase(db_path)
    db.initialize()
    assert db_path.exists()
    conn = db.connect()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "incidents" in tables
    assert "tickets" in tables


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(Exception):
        service.create_ticket(title="t", incident_id="missing")


def test_incident_create_and_get(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="boom", severity="high")
    assert incident.title == "boom"
    fetched = service.get_incident(incident.id)
    assert fetched.id == incident.id
    assert fetched.incident_number.startswith("INC-")


def test_unique_incident_numbers(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    first = service.create_incident(title="a")
    second = service.create_incident(title="b")
    assert first.incident_number != second.incident_number


def test_invalid_severity(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(InvalidInputError):
        service.create_incident(title="x", severity="bogus")


def test_list_filter_pagination(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    for i in range(5):
        service.create_incident(title=f"i{i}", severity="high" if i % 2 else "low")
    highs = service.list_incidents(severity="high")
    assert len(highs) == 2
    page = service.list_incidents(limit=2, offset=0)
    assert len(page) == 2


def test_status_transition_valid(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    updated = service.transition_incident(incident.id, "investigating")
    assert updated.status == "investigating"


def test_status_transition_invalid(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    with pytest.raises(InvalidStateTransitionError):
        service.transition_incident(incident.id, "resolved")


def test_resolve_and_reopen(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    service.transition_incident(incident.id, "investigating")
    resolution = service.resolve_incident(
        incident.id, "fixed", resolved_by="alice"
    )
    assert resolution.resolution_summary == "fixed"
    fetched = service.get_incident(incident.id)
    assert fetched.status == "resolved"
    reopened = service.reopen_incident(incident.id)
    assert reopened.status == "reopened"
    assert service.list_resolutions(incident.id)


def test_reopen_preserves_resolutions(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    service.transition_incident(incident.id, "investigating")
    service.resolve_incident(incident.id, "first fix")
    service.reopen_incident(incident.id)
    assert len(service.list_resolutions(incident.id)) == 1


def test_ticket_lifecycle(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    ticket = service.create_ticket(
        incident_id=incident.id, title="ticket", priority="high"
    )
    assert ticket.incident_id == incident.id
    updated = service.update_ticket(ticket.id, status="in_progress")
    assert updated.status == "in_progress"
    closed = service.close_ticket(ticket.id)
    assert closed.status == "closed"


def test_ticket_invalid_incident(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(IncidentNotFoundError):
        service.create_ticket(incident_id="nope", title="t")


def test_multiple_investigations(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    first = service.create_investigation(incident_id=incident.id)
    second = service.create_investigation(incident_id=incident.id)
    assert first.id != second.id
    assert len(service.list_investigations(incident.id)) == 2


def test_persist_report(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    investigation = service.create_investigation(incident_id=incident.id)
    report = {"summary": "done", "root_cause_hypotheses": [{"title": "db"}]}
    updated = service.persist_investigation_report(investigation.id, report)
    assert updated.status == "completed"
    fetched = service.get_investigation(investigation.id)
    assert fetched.report["summary"] == "done"
    assert fetched.suspected_root_cause == "db"


def test_store_and_get_evidence(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    investigation = service.create_investigation(incident_id=incident.id)
    ev = service.store_evidence(
        investigation.id, "log", source="logs", content={"pattern": "boom"}
    )
    assert ev.evidence_type == "log"
    items = service.list_evidence(investigation.id)
    assert len(items) == 1
    assert items[0].content["pattern"] == "boom"


def test_approval_flow(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    investigation = service.create_investigation(incident_id=incident.id)
    approval = service.create_approval(
        incident_id=incident.id, investigation_id=investigation.id
    )
    assert approval.status == "pending"
    approved = service.record_approval(
        approval.id, "approved", decided_by="alice", reason="ok"
    )
    assert approved.status == "approved"
    assert approved.decided_by == "alice"
    with pytest.raises(InvalidStateTransitionError):
        service.record_approval(approval.id, "rejected")


def test_resolution_history(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    service.transition_incident(incident.id, "investigating")
    service.record_resolution(incident_id=incident.id, resolution_summary="r1")
    service.record_resolution(incident_id=incident.id, resolution_summary="r2")
    items = service.list_resolutions(incident.id)
    assert len(items) == 2


def test_incident_history(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    history = service.get_incident_history(incident.id)
    assert history["incident"].id == incident.id
    assert isinstance(history["tickets"], list)


def test_reinitialization_preserves_records(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite"
    service = IncidentService(db_path=db_path)
    incident = service.create_incident(title="x")
    service2 = IncidentService(db_path=db_path)
    assert service2.get_incident(incident.id).title == "x"


def test_database_separate_from_checkpoint(tmp_path: Path) -> None:
    app_db = tmp_path / "app.sqlite"
    checkpoint_db = tmp_path / "checkpoint.sqlite"
    assert app_db != checkpoint_db
    service = IncidentService(db_path=app_db)
    service.create_incident(title="x")
    assert checkpoint_db.exists() is False


def test_invalid_transition_ticket(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    ticket = service.create_ticket(incident_id=incident.id, title="t")
    service.close_ticket(ticket.id)
    with pytest.raises(InvalidStateTransitionError):
        service.update_ticket(ticket.id, status="in_progress")


def test_update_incident_fields(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    incident = service.create_incident(title="x")
    updated = service.update_incident(incident.id, title="y", severity="low")
    assert updated.title == "y"
    assert updated.severity == "low"


def test_get_missing_incident(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(IncidentNotFoundError):
        service.get_incident("missing")
