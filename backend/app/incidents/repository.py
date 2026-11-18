"""Repository for OpsPilot incident-management persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from backend.app.incidents.database import ApplicationDatabase
from backend.app.incidents.exceptions import DuplicateRecordError
from backend.app.incidents.models import (
    Approval,
    Evidence,
    Incident,
    IncidentEvent,
    Investigation,
    Resolution,
    Ticket,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    """Explicitly convert supported non-JSON-native values.

    Arbitrary objects are not silently stringified; unsupported values raise
    ``TypeError`` so callers receive a clear serialization failure.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__!r} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True)


def _json_loads(value: Any) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _new_id() -> str:
    return str(uuid.uuid4())


def _unique_number(prefix: str, exists) -> str:
    while True:
        candidate = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
        if not exists(candidate):
            return candidate


class IncidentRepository:
    """Data-access methods for incident-management records."""

    def __init__(self, database: ApplicationDatabase) -> None:
        self.db = database

    # Incidents

    def create_incident(self, data: dict[str, Any]) -> Incident:
        incident_id = _new_id()
        now = _now()

        def exists(number: str) -> bool:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT 1 FROM incidents WHERE incident_number = ?",
                    (number,),
                ).fetchone()
                return row is not None

        incident_number = _unique_number("INC", exists)
        record = {
            "id": incident_id,
            "incident_number": incident_number,
            "title": data["title"],
            "description": data.get("description"),
            "severity": data["severity"],
            "priority": data["priority"],
            "status": data.get("status", "open"),
            "source": data.get("source"),
            "environment": data.get("environment"),
            "service_name": data.get("service_name"),
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
        }
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO incidents (
                        id, incident_number, title, description, severity, priority,
                        status, source, environment, service_name, created_at,
                        updated_at, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        record["incident_number"],
                        record["title"],
                        record["description"],
                        record["severity"],
                        record["priority"],
                        record["status"],
                        record["source"],
                        record["environment"],
                        record["service_name"],
                        record["created_at"],
                        record["updated_at"],
                        record["resolved_at"],
                    ),
                )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - race guard
            raise DuplicateRecordError(str(exc)) from exc
        return self._incident_from_row(record)

    def get_incident(self, incident_id: str) -> Incident | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                return None
            return self._incident_from_row(row)

    def get_incident_by_number(self, incident_number: str) -> Incident | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE incident_number = ?",
                (incident_number,),
            ).fetchone()
            if row is None:
                return None
            return self._incident_from_row(row)

    def list_incidents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        severity: str | None = None,
    ) -> list[Incident]:
        query = "SELECT * FROM incidents WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if severity is not None:
            query += " AND severity = ?"
            params.append(severity)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.db.transaction() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._incident_from_row(row) for row in rows]

    def count_incidents(self, *, status: str | None = None, severity: str | None = None) -> int:
        query = "SELECT COUNT(*) AS c FROM incidents WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if severity is not None:
            query += " AND severity = ?"
            params.append(severity)
        with self.db.transaction() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["c"])

    def update_incident(self, incident_id: str, changes: dict[str, Any]) -> Incident | None:
        if not changes:
            return self.get_incident(incident_id)
        allowed = {
            "title",
            "description",
            "severity",
            "priority",
            "status",
            "source",
            "environment",
            "service_name",
            "resolved_at",
        }
        fields = {k: v for k, v in changes.items() if k in allowed}
        if not fields:
            return self.get_incident(incident_id)
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [incident_id]
        with self.db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE incidents SET {assignments} WHERE id = ?",
                params,
            )
            if cur.rowcount == 0:
                return None
        return self.get_incident(incident_id)

    def delete_incident(self, incident_id: str) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
            return cur.rowcount > 0

    # Tickets

    def create_ticket(self, data: dict[str, Any]) -> Ticket:
        ticket_id = _new_id()

        def exists(number: str) -> bool:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT 1 FROM tickets WHERE ticket_number = ?",
                    (number,),
                ).fetchone()
                return row is not None

        ticket_number = _unique_number("TKT", exists)
        now = _now()
        record = {
            "id": ticket_id,
            "ticket_number": ticket_number,
            "incident_id": data["incident_id"],
            "title": data["title"],
            "description": data.get("description"),
            "priority": data["priority"],
            "status": data.get("status", "open"),
            "assignee": data.get("assignee"),
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO tickets (
                        id, ticket_number, incident_id, title, description, priority,
                        status, assignee, created_at, updated_at, closed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        record["ticket_number"],
                        record["incident_id"],
                        record["title"],
                        record["description"],
                        record["priority"],
                        record["status"],
                        record["assignee"],
                        record["created_at"],
                        record["updated_at"],
                        record["closed_at"],
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(str(exc)) from exc
        return self._ticket_from_row(record)

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            if row is None:
                return None
            return self._ticket_from_row(row)

    def list_tickets(self, incident_id: str) -> list[Ticket]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE incident_id = ? ORDER BY created_at DESC",
                (incident_id,),
            ).fetchall()
            return [self._ticket_from_row(row) for row in rows]

    def list_all_tickets(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[Ticket]:
        query = "SELECT * FROM tickets WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if priority is not None:
            query += " AND priority = ?"
            params.append(priority)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.db.transaction() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._ticket_from_row(row) for row in rows]

    def count_tickets(self, *, status: str | None = None, priority: str | None = None) -> int:
        query = "SELECT COUNT(*) AS c FROM tickets WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if priority is not None:
            query += " AND priority = ?"
            params.append(priority)
        with self.db.transaction() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["c"])

    def update_ticket(self, ticket_id: str, changes: dict[str, Any]) -> Ticket | None:
        if not changes:
            return self.get_ticket(ticket_id)
        allowed = {"title", "description", "priority", "status", "assignee", "closed_at"}
        fields = {k: v for k, v in changes.items() if k in allowed}
        if not fields:
            return self.get_ticket(ticket_id)
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [ticket_id]
        with self.db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE tickets SET {assignments} WHERE id = ?",
                params,
            )
            if cur.rowcount == 0:
                return None
        return self.get_ticket(ticket_id)

    # Investigations

    def create_investigation(self, data: dict[str, Any]) -> Investigation:
        investigation_id = _new_id()

        def exists(number: str) -> bool:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT 1 FROM investigations WHERE investigation_number = ?",
                    (number,),
                ).fetchone()
                return row is not None

        investigation_number = _unique_number("INV", exists)
        now = _now()
        record = {
            "id": investigation_id,
            "incident_id": data["incident_id"],
            "investigation_number": investigation_number,
            "status": data.get("status", "started"),
            "summary": data.get("summary"),
            "suspected_root_cause": data.get("suspected_root_cause"),
            "report": None,
            "thread_id": data.get("thread_id"),
            "workflow_status": data.get("workflow_status"),
            "last_error": data.get("last_error"),
            "started_at": data.get("started_at", now),
            "completed_at": data.get("completed_at"),
            "created_at": now,
            "updated_at": now,
        }
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO investigations (
                        id, incident_id, investigation_number, status, summary,
                        suspected_root_cause, report, thread_id, workflow_status,
                        last_error, started_at, completed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        record["incident_id"],
                        record["investigation_number"],
                        record["status"],
                        record["summary"],
                        record["suspected_root_cause"],
                        None,
                        record["thread_id"],
                        record["workflow_status"],
                        record["last_error"],
                        record["started_at"],
                        record["completed_at"],
                        record["created_at"],
                        record["updated_at"],
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(str(exc)) from exc
        return self._investigation_from_row(record)

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
            ).fetchone()
            if row is None:
                return None
            return self._investigation_from_row(row)

    def list_investigations(self, incident_id: str) -> list[Investigation]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM investigations WHERE incident_id = ? ORDER BY created_at DESC",
                (incident_id,),
            ).fetchall()
            return [self._investigation_from_row(row) for row in rows]

    def list_all_investigations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        workflow_status: str | None = None,
        incident_id: str | None = None,
    ) -> list[Investigation]:
        query = "SELECT * FROM investigations WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if workflow_status is not None:
            query += " AND workflow_status = ?"
            params.append(workflow_status)
        if incident_id is not None:
            query += " AND incident_id = ?"
            params.append(incident_id)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.db.transaction() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._investigation_from_row(row) for row in rows]

    def count_investigations(
        self,
        *,
        status: str | None = None,
        workflow_status: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) AS c FROM investigations WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if workflow_status is not None:
            query += " AND workflow_status = ?"
            params.append(workflow_status)
        with self.db.transaction() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["c"])

    def group_incidents_by(self, field: str) -> dict[str, int]:
        allowed = {"severity", "status", "priority", "environment", "service_name"}
        if field not in allowed:
            raise ValueError("unsupported incident grouping field")
        with self.db.transaction() as conn:
            rows = conn.execute(
                f"SELECT COALESCE({field}, 'unspecified') AS name, "
                f"COUNT(*) AS c FROM incidents GROUP BY {field} ORDER BY c DESC"
            ).fetchall()
            return {str(row["name"]): int(row["c"]) for row in rows}

    def group_investigations_by(self, field: str) -> dict[str, int]:
        allowed = {"status", "workflow_status"}
        if field not in allowed:
            raise ValueError("unsupported investigation grouping field")
        with self.db.transaction() as conn:
            rows = conn.execute(
                f"SELECT COALESCE({field}, 'unspecified') AS name, "
                f"COUNT(*) AS c FROM investigations GROUP BY {field} ORDER BY c DESC"
            ).fetchall()
            return {str(row["name"]): int(row["c"]) for row in rows}

    def update_investigation(
        self, investigation_id: str, changes: dict[str, Any]
    ) -> Investigation | None:
        if not changes:
            return self.get_investigation(investigation_id)
        allowed = {
            "status",
            "summary",
            "suspected_root_cause",
            "report",
            "thread_id",
            "workflow_status",
            "last_error",
            "started_at",
            "completed_at",
        }
        fields = {k: v for k, v in changes.items() if k in allowed}
        if "report" in fields and fields["report"] is not None:
            fields["report"] = _json_dumps(fields["report"])
        if not fields:
            return self.get_investigation(investigation_id)
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [investigation_id]
        with self.db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE investigations SET {assignments} WHERE id = ?",
                params,
            )
            if cur.rowcount == 0:
                return None
        return self.get_investigation(investigation_id)

    # Evidence

    def create_evidence(self, data: dict[str, Any]) -> Evidence:
        evidence_id = _new_id()
        now = _now()
        record = {
            "id": evidence_id,
            "investigation_id": data["investigation_id"],
            "evidence_type": data["evidence_type"],
            "source": data.get("source"),
            "content": data.get("content"),
            "metadata": data.get("metadata") or {},
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO evidence (
                    id, investigation_id, evidence_type, source, content, metadata,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["investigation_id"],
                    record["evidence_type"],
                    record["source"],
                    (
                        _json_dumps(record["content"])
                        if not isinstance(record["content"], str)
                        else record["content"]
                    ),
                    _json_dumps(record["metadata"]),
                    record["created_at"],
                ),
            )
        return Evidence(
            id=record["id"],
            investigation_id=record["investigation_id"],
            evidence_type=record["evidence_type"],
            source=record["source"],
            content=record["content"],
            metadata=record["metadata"],
            created_at=record["created_at"],
        )

    def list_evidence(self, investigation_id: str) -> list[Evidence]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE investigation_id = ? ORDER BY created_at ASC",
                (investigation_id,),
            ).fetchall()
            return [self._evidence_from_row(row) for row in rows]

    # Approvals

    def create_approval(self, data: dict[str, Any]) -> Approval:
        approval_id = _new_id()
        now = _now()
        record = {
            "id": approval_id,
            "incident_id": data["incident_id"],
            "investigation_id": data.get("investigation_id"),
            "approval_type": data.get("approval_type", "human_review"),
            "status": "pending",
            "requested_at": now,
            "decided_at": None,
            "decided_by": None,
            "decision_reason": None,
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    id, incident_id, investigation_id, approval_type, status,
                    requested_at, decided_at, decided_by, decision_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["incident_id"],
                    record["investigation_id"],
                    record["approval_type"],
                    record["status"],
                    record["requested_at"],
                    record["decided_at"],
                    record["decided_by"],
                    record["decision_reason"],
                    record["created_at"],
                ),
            )
        return self._approval_from_row(record)

    def get_approval(self, approval_id: str) -> Approval | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                return None
            return self._approval_from_row(row)

    def list_approvals(
        self,
        *,
        incident_id: str | None = None,
        investigation_id: str | None = None,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[Approval]:
        query = "SELECT * FROM approvals WHERE 1=1"
        params: list[Any] = []
        if incident_id is not None:
            query += " AND incident_id = ?"
            params.append(incident_id)
        if investigation_id is not None:
            query += " AND investigation_id = ?"
            params.append(investigation_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.db.transaction() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._approval_from_row(row) for row in rows]

    def count_approvals(self, *, status: str | None = None) -> int:
        query = "SELECT COUNT(*) AS c FROM approvals WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        with self.db.transaction() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["c"])

    def update_approval(self, approval_id: str, changes: dict[str, Any]) -> Approval | None:
        allowed = {"status", "decided_at", "decided_by", "decision_reason"}
        fields = {k: v for k, v in changes.items() if k in allowed}
        if not fields:
            return self.get_approval(approval_id)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [approval_id]
        with self.db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE approvals SET {assignments} WHERE id = ?",
                params,
            )
            if cur.rowcount == 0:
                return None
        return self.get_approval(approval_id)

    # Resolutions

    def create_resolution(self, data: dict[str, Any]) -> Resolution:
        resolution_id = _new_id()
        now = _now()
        record = {
            "id": resolution_id,
            "incident_id": data["incident_id"],
            "investigation_id": data.get("investigation_id"),
            "resolution_summary": data["resolution_summary"],
            "resolution_details": data.get("resolution_details"),
            "resolved_by": data.get("resolved_by"),
            "resolution_status": data.get("resolution_status", "recorded"),
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO resolutions (
                    id, incident_id, investigation_id, resolution_summary,
                    resolution_details, resolved_by, resolution_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["incident_id"],
                    record["investigation_id"],
                    record["resolution_summary"],
                    record["resolution_details"],
                    record["resolved_by"],
                    record["resolution_status"],
                    record["created_at"],
                ),
            )
        return self._resolution_from_row(record)

    def list_resolutions(self, incident_id: str) -> list[Resolution]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM resolutions WHERE incident_id = ? ORDER BY created_at DESC",
                (incident_id,),
            ).fetchall()
            return [self._resolution_from_row(row) for row in rows]

    # Incident events

    def create_incident_event(
        self,
        *,
        incident_id: str,
        event_type: str,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IncidentEvent:
        event_id = _new_id()
        now = _now()
        record = {
            "id": event_id,
            "incident_id": incident_id,
            "event_type": event_type,
            "actor": actor,
            "metadata": metadata or {},
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO incident_events (
                    id, incident_id, event_type, actor, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["incident_id"],
                    record["event_type"],
                    record["actor"],
                    _json_dumps(record["metadata"]),
                    record["created_at"],
                ),
            )
        return self._incident_event_from_row(record)

    def list_incident_events(self, incident_id: str) -> list[IncidentEvent]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM incident_events WHERE incident_id = ? ORDER BY created_at ASC",
                (incident_id,),
            ).fetchall()
            return [self._incident_event_from_row(row) for row in rows]

    # Row converters

    def _incident_from_row(self, row: Any) -> Incident:
        data = dict(row) if not isinstance(row, dict) else row
        return Incident(**data)

    def _ticket_from_row(self, row: Any) -> Ticket:
        data = dict(row) if not isinstance(row, dict) else row
        return Ticket(**data)

    def _investigation_from_row(self, row: Any) -> Investigation:
        data = dict(row) if not isinstance(row, dict) else dict(row)
        data["report"] = _json_loads(data.get("report"))
        data.setdefault("thread_id", None)
        data.setdefault("workflow_status", None)
        data.setdefault("last_error", None)
        return Investigation(**data)

    def _evidence_from_row(self, row: Any) -> Evidence:
        data = dict(row) if not isinstance(row, dict) else row
        data["content"] = _json_loads(data.get("content"))
        data["metadata"] = _json_loads(data.get("metadata")) or {}
        return Evidence(**data)

    def _approval_from_row(self, row: Any) -> Approval:
        data = dict(row) if not isinstance(row, dict) else row
        return Approval(**data)

    def _resolution_from_row(self, row: Any) -> Resolution:
        data = dict(row) if not isinstance(row, dict) else row
        return Resolution(**data)

    def _incident_event_from_row(self, row: Any) -> IncidentEvent:
        data = dict(row) if not isinstance(row, dict) else row
        data["metadata"] = _json_loads(data.get("metadata")) or {}
        return IncidentEvent(**data)
