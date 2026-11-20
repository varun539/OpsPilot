"""Service layer for OpsPilot incident management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.app.incidents.database import ApplicationDatabase
from backend.app.incidents.exceptions import (
    ApprovalNotFoundError,
    IncidentNotFoundError,
    InvalidInputError,
    InvalidStateTransitionError,
    InvestigationNotFoundError,
    TicketNotFoundError,
)
from backend.app.incidents.models import (
    ALLOWED_PRIORITIES,
    ALLOWED_SEVERITIES,
    INCIDENT_TRANSITIONS,
    TICKET_TRANSITIONS,
    ApprovalStatus,
    IncidentStatus,
    InvestigationStatus,
    TicketStatus,
)
from backend.app.incidents.repository import IncidentRepository
from backend.app.incidents.schemas import (
    ApprovalCreate,
    EvidenceCreate,
    IncidentCreate,
    IncidentUpdate,
    ResolutionCreate,
    TicketCreate,
    TicketUpdate,
)

ACTIVE_WORKFLOW_STATUSES = {
    "created",
    "running",
    "awaiting_approval",
    "resuming",
}
WORKFLOW_STATUSES = {
    "created",
    "running",
    "awaiting_approval",
    "resuming",
    "completed",
    "failed",
    "resume_failed",
    "rejected",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    raise InvalidInputError("expected a mapping or schema object")


def _serialize_report(report: Any) -> Any:
    if report is None:
        return None
    if hasattr(report, "report") and hasattr(report, "model_dump"):
        # RAGResponse-like object: normalize to its JSON envelope so the same
        # report/provenance merge rules apply.
        return _serialize_report(report.model_dump(mode="json"))
    if hasattr(report, "model_dump"):
        return report.model_dump(mode="json")
    if isinstance(report, dict):
        nested = report.get("report")
        if isinstance(nested, dict):
            # Persist the structured investigation report plus source/provenance
            # metadata. Retrieved evidence chunks are stored separately.
            merged = dict(nested)
            for key in (
                "question",
                "source_references",
                "retrieval_metadata",
                "provider",
                "model",
                "usage",
                "generation_status",
                "valid",
                "citation_valid",
                "validation_errors",
            ):
                if key in report and key not in merged:
                    merged[key] = report[key]
            return merged
        return dict(report)
    if isinstance(report, (list, str, int, float, bool)):
        return report
    raise InvalidInputError("report is not serializable")


class IncidentService:
    """Business operations for incidents, tickets, investigations, and history."""

    def __init__(
        self,
        repository: IncidentRepository | None = None,
        database: ApplicationDatabase | None = None,
        db_path: str | None = None,
    ) -> None:
        if repository is not None:
            self.repository = repository
        else:
            if database is None:
                database = ApplicationDatabase(db_path)
            database.initialize()
            self.repository = IncidentRepository(database)
        self.database = getattr(self.repository, "db", None)

    # Incidents

    def create_incident(self, data: Any = None, **kwargs: Any) -> Any:
        if isinstance(data, str):
            data = {"title": data, **kwargs}
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        if "title" not in payload:
            raise InvalidInputError("title is required")
        payload.setdefault("severity", "medium")
        payload.setdefault("priority", "medium")
        payload.setdefault("status", "open")
        if payload["severity"] not in ALLOWED_SEVERITIES:
            raise InvalidInputError("invalid severity")
        if payload["priority"] not in ALLOWED_PRIORITIES:
            raise InvalidInputError("invalid priority")
        if payload["status"] not in {s.value for s in IncidentStatus}:
            raise InvalidInputError("invalid incident status")
        validated = IncidentCreate(**payload)
        return self.repository.create_incident(validated.model_dump())

    def get_incident(self, incident_id: str) -> Any:
        incident = self.repository.get_incident(incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incident not found: {incident_id}")
        return incident

    def get_incident_by_number(self, incident_number: str) -> Any:
        incident = self.repository.get_incident_by_number(incident_number)
        if incident is None:
            raise IncidentNotFoundError(f"incident not found: {incident_number}")
        return incident

    def list_incidents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        severity: str | None = None,
    ) -> list[Any]:
        if status is not None and status not in {s.value for s in IncidentStatus}:
            raise InvalidInputError("invalid incident status")
        if severity is not None and severity not in ALLOWED_SEVERITIES:
            raise InvalidInputError("invalid severity")
        return self.repository.list_incidents(
            limit=limit, offset=offset, status=status, severity=severity
        )

    def count_incidents(self, *, status: str | None = None, severity: str | None = None) -> int:
        return self.repository.count_incidents(status=status, severity=severity)

    def update_incident(self, incident_id: str, data: Any = None, **kwargs: Any) -> Any:
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        self.get_incident(incident_id)
        if "status" in payload:
            self.transition_incident(incident_id, payload.pop("status"))
        if (
            "severity" in payload
            and payload["severity"] is not None
            and payload["severity"] not in ALLOWED_SEVERITIES
        ):
            raise InvalidInputError("invalid severity")
        if (
            "priority" in payload
            and payload["priority"] is not None
            and payload["priority"] not in ALLOWED_PRIORITIES
        ):
            raise InvalidInputError("invalid priority")
        validated = IncidentUpdate(**payload)
        changes = {k: v for k, v in validated.model_dump().items() if v is not None}
        # Preserve explicit null updates for description? Keep not-null semantics.
        for key in ("description", "source", "environment", "service_name"):
            if key in payload and payload[key] is None:
                changes[key] = None
        updated = self.repository.update_incident(incident_id, changes)
        if updated is None:
            raise IncidentNotFoundError(f"incident not found: {incident_id}")
        return updated

    def transition_incident(
        self, incident_id: str, new_status: str, reason: str | None = None
    ) -> Any:
        if new_status not in {s.value for s in IncidentStatus}:
            raise InvalidInputError("invalid incident status")
        incident = self.get_incident(incident_id)
        current = incident.status
        if new_status == current:
            return incident
        if new_status not in INCIDENT_TRANSITIONS.get(current, set()):
            raise InvalidStateTransitionError(
                f"cannot transition incident from {current} to {new_status}"
            )
        changes: dict[str, Any] = {"status": new_status}
        if new_status == IncidentStatus.resolved.value:
            changes["resolved_at"] = _now()
        if new_status == IncidentStatus.reopened.value:
            changes["resolved_at"] = None
        updated = self.repository.update_incident(incident_id, changes)
        if updated is None:
            raise IncidentNotFoundError(f"incident not found: {incident_id}")
        return updated

    def reopen_incident(self, incident_id: str, reason: str | None = None) -> Any:
        return self.transition_incident(incident_id, IncidentStatus.reopened.value, reason)

    def resolve_incident(
        self,
        incident_id: str,
        resolution_summary: str,
        *,
        resolved_by: str | None = None,
        investigation_id: str | None = None,
        resolution_details: str | None = None,
    ) -> Any:
        incident = self.get_incident(incident_id)
        if incident.status != IncidentStatus.resolved.value:
            self.transition_incident(incident_id, IncidentStatus.resolved.value)
        resolution = self.record_resolution(
            incident_id=incident_id,
            resolution_summary=resolution_summary,
            resolved_by=resolved_by,
            investigation_id=investigation_id,
            resolution_details=resolution_details,
        )
        return resolution

    def get_incident_history(self, incident_id: str) -> dict[str, Any]:
        incident = self.get_incident(incident_id)
        return {
            "incident": incident,
            "tickets": self.list_tickets(incident_id),
            "investigations": self.list_investigations(incident_id),
            "approvals": self.list_approvals(incident_id=incident_id),
            "resolutions": self.list_resolutions(incident_id),
            "events": self.list_incident_events(incident_id),
        }

    # Tickets

    def create_ticket(self, data: Any = None, **kwargs: Any) -> Any:
        if isinstance(data, str):
            data = {"title": data, **kwargs}
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        if "incident_id" not in payload:
            raise InvalidInputError("incident_id is required")
        if "title" not in payload:
            raise InvalidInputError("title is required")
        self.get_incident(payload["incident_id"])
        validated = TicketCreate(**payload)
        return self.repository.create_ticket(validated.model_dump())

    def get_ticket(self, ticket_id: str) -> Any:
        ticket = self.repository.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFoundError(f"ticket not found: {ticket_id}")
        return ticket

    def list_tickets(self, incident_id: str) -> list[Any]:
        self.get_incident(incident_id)
        return self.repository.list_tickets(incident_id)

    def list_all_tickets(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[Any]:
        if status is not None and status not in {s.value for s in TicketStatus}:
            raise InvalidInputError("invalid ticket status")
        if priority is not None and priority not in ALLOWED_PRIORITIES:
            raise InvalidInputError("invalid priority")
        return self.repository.list_all_tickets(
            limit=limit, offset=offset, status=status, priority=priority
        )

    def count_tickets(self, *, status: str | None = None, priority: str | None = None) -> int:
        return self.repository.count_tickets(status=status, priority=priority)

    def update_ticket(self, ticket_id: str, data: Any = None, **kwargs: Any) -> Any:
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        ticket = self.get_ticket(ticket_id)
        if "status" in payload:
            new_status = payload.pop("status")
            if new_status != ticket.status:
                if new_status not in {s.value for s in TicketStatus}:
                    raise InvalidInputError("invalid ticket status")
                if new_status not in TICKET_TRANSITIONS.get(ticket.status, set()):
                    raise InvalidStateTransitionError(
                        f"cannot transition ticket from {ticket.status} to {new_status}"
                    )
            payload["status"] = new_status
            if new_status in {TicketStatus.resolved.value, TicketStatus.closed.value}:
                payload.setdefault("closed_at", _now())
        if (
            "priority" in payload
            and payload["priority"] is not None
            and payload["priority"] not in ALLOWED_PRIORITIES
        ):
            raise InvalidInputError("invalid priority")
        validated = TicketUpdate(**payload)
        changes = {k: v for k, v in validated.model_dump().items() if v is not None}
        for key in ("description", "assignee"):
            if key in payload and payload[key] is None:
                changes[key] = None
        if "status" in payload and payload.get("status") in {
            TicketStatus.resolved.value,
            TicketStatus.closed.value,
        }:
            changes.setdefault("closed_at", _now())
        updated = self.repository.update_ticket(ticket_id, changes)
        if updated is None:
            raise TicketNotFoundError(f"ticket not found: {ticket_id}")
        return updated

    def close_ticket(self, ticket_id: str) -> Any:
        return self.update_ticket(ticket_id, status=TicketStatus.closed.value)

    # Investigations

    def create_investigation(self, data: Any = None, **kwargs: Any) -> Any:
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        if "incident_id" not in payload:
            raise InvalidInputError("incident_id is required")
        self.get_incident(payload["incident_id"])
        payload.setdefault("status", InvestigationStatus.started.value)
        if payload["status"] not in {s.value for s in InvestigationStatus}:
            raise InvalidInputError("invalid investigation status")
        validated = None
        from backend.app.incidents.schemas import InvestigationCreate

        validated = InvestigationCreate(**payload)
        return self.repository.create_investigation(validated.model_dump())

    def get_investigation(self, investigation_id: str) -> Any:
        investigation = self.repository.get_investigation(investigation_id)
        if investigation is None:
            raise InvestigationNotFoundError(f"investigation not found: {investigation_id}")
        return investigation

    def list_investigations(self, incident_id: str) -> list[Any]:
        self.get_incident(incident_id)
        return self.repository.list_investigations(incident_id)

    def list_all_investigations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        workflow_status: str | None = None,
        incident_id: str | None = None,
    ) -> list[Any]:
        if status is not None and status not in {s.value for s in InvestigationStatus}:
            raise InvalidInputError("invalid investigation status")
        if workflow_status is not None and workflow_status not in WORKFLOW_STATUSES:
            raise InvalidInputError("invalid workflow status")
        if incident_id is not None:
            self.get_incident(incident_id)
        return self.repository.list_all_investigations(
            limit=limit,
            offset=offset,
            status=status,
            workflow_status=workflow_status,
            incident_id=incident_id,
        )

    def count_investigations(
        self,
        *,
        status: str | None = None,
        workflow_status: str | None = None,
    ) -> int:
        return self.repository.count_investigations(status=status, workflow_status=workflow_status)

    def incident_breakdown(self, field: str) -> dict[str, int]:
        return self.repository.group_incidents_by(field)

    def investigation_breakdown(self, field: str) -> dict[str, int]:
        return self.repository.group_investigations_by(field)

    def update_investigation_status(self, investigation_id: str, status: str) -> Any:
        if status not in {s.value for s in InvestigationStatus}:
            raise InvalidInputError("invalid investigation status")
        self.get_investigation(investigation_id)
        changes = {"status": status}
        if status in {InvestigationStatus.completed.value, InvestigationStatus.failed.value}:
            changes["completed_at"] = _now()
        updated = self.repository.update_investigation(investigation_id, changes)
        if updated is None:
            raise InvestigationNotFoundError(f"investigation not found: {investigation_id}")
        return updated

    def update_investigation(self, investigation_id: str, data: Any = None, **kwargs: Any) -> Any:
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        self.get_investigation(investigation_id)
        if "status" in payload and payload["status"] is not None:
            if payload["status"] not in {s.value for s in InvestigationStatus}:
                raise InvalidInputError("invalid investigation status")
        if "workflow_status" in payload and payload["workflow_status"] is not None:
            if payload["workflow_status"] not in WORKFLOW_STATUSES:
                raise InvalidInputError("invalid workflow status")
        if "report" in payload and payload["report"] is not None:
            payload["report"] = _serialize_report(payload["report"])
            if "summary" not in payload and isinstance(payload["report"], dict):
                payload.setdefault("summary", payload["report"].get("summary"))
        updated = self.repository.update_investigation(investigation_id, payload)
        if updated is None:
            raise InvestigationNotFoundError(f"investigation not found: {investigation_id}")
        return updated

    def has_active_investigation(self, incident_id: str) -> Any:
        self.get_incident(incident_id)
        for investigation in self.repository.list_investigations(incident_id):
            if investigation.workflow_status in ACTIVE_WORKFLOW_STATUSES:
                return investigation
            if investigation.workflow_status is None and investigation.status in {
                InvestigationStatus.started.value,
                InvestigationStatus.running.value,
            }:
                return investigation
        return None

    def persist_pending_report(
        self,
        investigation_id: str,
        report: Any,
        *,
        summary: str | None = None,
        suspected_root_cause: str | None = None,
        status: str | None = None,
    ) -> Any:
        self.get_investigation(investigation_id)
        serialized = _serialize_report(report)
        if summary is None and isinstance(serialized, dict):
            summary = serialized.get("summary")
        if suspected_root_cause is None and isinstance(serialized, dict):
            hypotheses = serialized.get("root_cause_hypotheses") or []
            if hypotheses and isinstance(hypotheses[0], dict):
                suspected_root_cause = hypotheses[0].get("title")
        changes: dict[str, Any] = {"report": serialized}
        if summary is not None:
            changes["summary"] = summary
        if suspected_root_cause is not None:
            changes["suspected_root_cause"] = suspected_root_cause
        if status is not None:
            changes["status"] = status
        updated = self.repository.update_investigation(investigation_id, changes)
        if updated is None:
            raise InvestigationNotFoundError(f"investigation not found: {investigation_id}")
        return updated

    def persist_investigation_report(
        self,
        investigation_id: str,
        report: Any,
        *,
        summary: str | None = None,
        suspected_root_cause: str | None = None,
    ) -> Any:
        self.get_investigation(investigation_id)
        serialized = _serialize_report(report)
        if summary is None and isinstance(serialized, dict):
            summary = serialized.get("summary")
        if suspected_root_cause is None and isinstance(serialized, dict):
            hypotheses = serialized.get("root_cause_hypotheses") or []
            if hypotheses and isinstance(hypotheses[0], dict):
                suspected_root_cause = hypotheses[0].get("title")
        changes = {
            "report": serialized,
            "status": InvestigationStatus.completed.value,
            "completed_at": _now(),
        }
        if summary is not None:
            changes["summary"] = summary
        if suspected_root_cause is not None:
            changes["suspected_root_cause"] = suspected_root_cause
        updated = self.repository.update_investigation(investigation_id, changes)
        if updated is None:
            raise InvestigationNotFoundError(f"investigation not found: {investigation_id}")
        return updated

    persist_report = persist_investigation_report

    def store_evidence(
        self,
        investigation_id: str,
        evidence_type: str,
        *,
        source: str | None = None,
        content: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        self.get_investigation(investigation_id)

        validated = EvidenceCreate(
            investigation_id=investigation_id,
            evidence_type=evidence_type,
            source=source,
            content=content,
            metadata=metadata or {},
        )
        return self.repository.create_evidence(validated.model_dump())

    def list_evidence(self, investigation_id: str) -> list[Any]:
        self.get_investigation(investigation_id)
        return self.repository.list_evidence(investigation_id)

    # Approvals

    def create_approval(self, data: Any = None, **kwargs: Any) -> Any:
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        if "incident_id" not in payload:
            raise InvalidInputError("incident_id is required")
        self.get_incident(payload["incident_id"])
        if payload.get("investigation_id") is not None:
            self.get_investigation(payload["investigation_id"])

        validated = ApprovalCreate(**payload)
        return self.repository.create_approval(validated.model_dump())

    def get_approval(self, approval_id: str) -> Any:
        approval = self.repository.get_approval(approval_id)
        if approval is None:
            raise ApprovalNotFoundError(f"approval not found: {approval_id}")
        return approval

    def list_approvals(
        self,
        *,
        incident_id: str | None = None,
        investigation_id: str | None = None,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[Any]:
        if incident_id is not None:
            self.get_incident(incident_id)
        if investigation_id is not None:
            self.get_investigation(investigation_id)
        if status is not None and status not in {s.value for s in ApprovalStatus}:
            raise InvalidInputError("invalid approval status")
        return self.repository.list_approvals(
            incident_id=incident_id,
            investigation_id=investigation_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def count_approvals(self, *, status: str | None = None) -> int:
        return self.repository.count_approvals(status=status)

    def record_approval(
        self,
        approval_id: str,
        decision: str,
        *,
        decided_by: str | None = None,
        reason: str | None = None,
    ) -> Any:
        approval = self.get_approval(approval_id)
        if approval.status != ApprovalStatus.pending.value:
            raise InvalidStateTransitionError("approval decision has already been recorded")
        if decision not in {"approved", "rejected"}:
            raise InvalidInputError("invalid approval decision")
        status = (
            ApprovalStatus.approved.value
            if decision == "approved"
            else ApprovalStatus.rejected.value
        )
        decided_at = _now()
        with self.database.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE approvals
                SET status = ?, decided_at = ?, decided_by = ?, decision_reason = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status,
                    decided_at,
                    decided_by,
                    reason,
                    approval_id,
                    ApprovalStatus.pending.value,
                ),
            )
            if cur.rowcount == 0:
                raise InvalidStateTransitionError("approval decision has already been recorded")
        updated = self.get_approval(approval_id)
        return updated

    record_approval_decision = record_approval

    def approve(self, approval_id: str, *, decided_by=None, reason=None) -> Any:
        return self.record_approval(approval_id, "approved", decided_by=decided_by, reason=reason)

    def reject(self, approval_id: str, *, decided_by=None, reason=None) -> Any:
        return self.record_approval(approval_id, "rejected", decided_by=decided_by, reason=reason)

    # Resolutions

    def record_resolution(self, data: Any = None, **kwargs: Any) -> Any:
        payload = _as_dict(data) if data is not None else {}
        payload.update(kwargs)
        if "incident_id" not in payload:
            raise InvalidInputError("incident_id is required")
        if "resolution_summary" not in payload:
            raise InvalidInputError("resolution_summary is required")
        self.get_incident(payload["incident_id"])
        if payload.get("investigation_id") is not None:
            self.get_investigation(payload["investigation_id"])

        validated = ResolutionCreate(**payload)
        return self.repository.create_resolution(validated.model_dump())

    def list_resolutions(self, incident_id: str) -> list[Any]:
        self.get_incident(incident_id)
        return self.repository.list_resolutions(incident_id)

    def list_incident_events(self, incident_id: str) -> list[Any]:
        self.get_incident(incident_id)
        return self.repository.list_incident_events(incident_id)

    def record_incident_event(
        self,
        incident_id: str,
        event_type: str,
        *,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        self.get_incident(incident_id)
        return self.repository.create_incident_event(
            incident_id=incident_id,
            event_type=event_type,
            actor=actor,
            metadata=metadata,
        )

    # Compatibility aliases

    get_incidents = list_incidents
    get_tickets = list_tickets
    get_investigations = list_investigations
    get_evidence = list_evidence
    get_resolutions = list_resolutions
    get_approvals = list_approvals
    create_incident_record = create_incident
    create_ticket_record = create_ticket
    create_investigation_record = create_investigation
