"""Application database management for OpsPilot incident records."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS incidents (
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

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    ticket_number TEXT NOT NULL UNIQUE,
    incident_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    assignee TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    investigation_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    summary TEXT,
    suspected_root_cause TEXT,
    report TEXT,
    thread_id TEXT,
    workflow_status TEXT,
    last_error TEXT,
    planner_used INTEGER,
    planner_status TEXT,
    planner_error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    source TEXT,
    content TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    investigation_id TEXT,
    approval_type TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    decision_reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE RESTRICT,
    FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS resolutions (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    investigation_id TEXT,
    resolution_summary TEXT NOT NULL,
    resolution_details TEXT,
    resolved_by TEXT,
    resolution_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE RESTRICT,
    FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_tickets_incident_id ON tickets(incident_id);
CREATE INDEX IF NOT EXISTS idx_investigations_incident_id ON investigations(incident_id);
CREATE INDEX IF NOT EXISTS idx_evidence_investigation_id ON evidence(investigation_id);
CREATE INDEX IF NOT EXISTS idx_approvals_incident_id ON approvals(incident_id);
CREATE INDEX IF NOT EXISTS idx_resolutions_incident_id ON resolutions(incident_id);

CREATE TABLE IF NOT EXISTS incident_events (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_incident_events_incident_id ON incident_events(incident_id);
"""


def restrict_file_permissions(path: Path) -> None:
    """Best-effort 0600 for a persistent database file.

    SQLite creates files with the process umask, which commonly leaves incident
    records and evidence readable by other local users. Tightening the file is
    safe and meaningful on POSIX; Windows ACLs are left to the deployment.
    """
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def app_db_path() -> str:
    raw = os.getenv("OPSPILOT_APP_DB", "").strip()
    if raw:
        return raw
    return "data/opspilot_app.sqlite"


class ApplicationDatabase:
    """SQLite application database with explicit lifecycle and transactions."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path if db_path is not None else app_db_path()

    def _is_memory(self) -> bool:
        return str(self.db_path).strip() == ":memory:"

    def connect(self) -> sqlite3.Connection:
        if self._is_memory():
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            path = Path(self.db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            if not self._is_memory():
                restrict_file_permissions(Path(self.db_path))
            conn.executescript(SCHEMA)
            # Additive migrations for Phase 11.
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(investigations)").fetchall()
            }
            migrations = {
                "thread_id": "TEXT",
                "workflow_status": "TEXT",
                "last_error": "TEXT",
                # Phase 23 planner observability. Nullable by design: rows
                # written before this migration stay readable and simply
                # report an unknown planner status.
                "planner_used": "INTEGER",
                "planner_status": "TEXT",
                "planner_error": "TEXT",
            }
            for column, ddl in migrations.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE investigations ADD COLUMN {column} {ddl}")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_investigations_thread_id "
                "ON investigations(thread_id) WHERE thread_id IS NOT NULL"
            )
            # Durable guard against two concurrent *orchestrated* active
            # investigations for one incident. Legacy/manual investigations have
            # no thread_id and remain unaffected.
            conn.execute("DROP INDEX IF EXISTS idx_investigations_active_incident")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_investigations_active_thread "
                "ON investigations(incident_id) "
                "WHERE thread_id IS NOT NULL AND workflow_status IN "
                "('created', 'running', 'awaiting_approval', 'resuming')"
            )
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def close(self) -> None:
        # Connections are opened per operation; nothing persistent to close.
        return None
