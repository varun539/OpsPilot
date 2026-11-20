"""Tests for read-only dashboard, approvals queue, and global list endpoints."""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from backend.app.auth.config import AuthSettings
from backend.app.auth.dependencies import get_current_principal
from backend.app.auth.models import Principal
from backend.app.main import create_app


def _app():
    directory = tempfile.mkdtemp()
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=True, host="127.0.0.1"
    )
    return create_app(
        db_path=os.path.join(directory, "app.sqlite"),
        checkpoint_db_path=os.path.join(directory, "checkpoints.sqlite"),
        auth_settings=settings,
    )


def test_dashboard_stats_reflect_stored_records() -> None:
    app = _app()
    with TestClient(app) as client:
        client.post(
            "/api/v1/incidents",
            json={"title": "a", "severity": "critical", "status": "investigating"},
        )
        client.post("/api/v1/incidents", json={"title": "b", "severity": "low"})
        incident = client.get("/api/v1/incidents").json()[0]
        client.post(
            f"/api/v1/incidents/{incident['id']}/tickets", json={"title": "t"}
        )

        response = client.get("/api/v1/dashboard/stats")
        assert response.status_code == 200
        body = response.json()
        assert body["total_incidents"] == 2
        assert body["open_incidents"] == 1
        assert body["investigating_incidents"] == 1
        assert body["total_tickets"] == 1
        assert body["open_tickets"] == 1
        severities = {item["name"]: item["count"] for item in body["incidents_by_severity"]}
        assert severities == {"critical": 1, "low": 1}


def test_global_ticket_listing_and_filters() -> None:
    app = _app()
    with TestClient(app) as client:
        incident = client.post("/api/v1/incidents", json={"title": "a"}).json()
        client.post(
            f"/api/v1/incidents/{incident['id']}/tickets",
            json={"title": "high ticket", "priority": "high"},
        )
        client.post(
            f"/api/v1/incidents/{incident['id']}/tickets",
            json={"title": "low ticket", "priority": "low"},
        )
        response = client.get("/api/v1/tickets")
        assert response.status_code == 200
        assert len(response.json()) == 2
        filtered = client.get("/api/v1/tickets", params={"priority": "high"})
        assert len(filtered.json()) == 1
        assert filtered.json()[0]["title"] == "high ticket"


def test_global_investigation_listing() -> None:
    app = _app()
    with TestClient(app) as client:
        incident = client.post("/api/v1/incidents", json={"title": "a"}).json()
        app.state.incident_service.create_investigation(
            incident_id=incident["id"], status="started", workflow_status="running"
        )
        response = client.get("/api/v1/investigations")
        assert response.status_code == 200
        assert len(response.json()) == 1
        filtered = client.get(
            "/api/v1/investigations", params={"workflow_status": "completed"}
        )
        assert filtered.json() == []


def test_pending_approval_queue_includes_context() -> None:
    app = _app()
    with TestClient(app) as client:
        incident = client.post(
            "/api/v1/incidents", json={"title": "db latency", "severity": "high"}
        ).json()
        investigation = app.state.incident_service.create_investigation(
            incident_id=incident["id"],
            status="running",
            workflow_status="awaiting_approval",
        )
        app.state.incident_service.create_approval(
            incident_id=incident["id"], investigation_id=investigation.id
        )

        response = client.get("/api/v1/approvals", params={"status": "pending"})
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["incident"]["incident_number"] == incident["incident_number"]
        assert body[0]["investigation"]["id"] == investigation.id
        assert body[0]["approval"]["status"] == "pending"


def test_viewer_can_read_dashboard_but_not_mutate() -> None:
    app = _app()
    with TestClient(app) as client:
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            subject="viewer", roles=["viewer"]
        )
        assert client.get("/api/v1/dashboard/stats").status_code == 200
        assert client.get("/api/v1/approvals").status_code == 200
        assert client.get("/api/v1/tickets").status_code == 200
        assert client.get("/api/v1/investigations").status_code == 200
        assert (
            client.post("/api/v1/incidents", json={"title": "nope"}).status_code
            == 403
        )


def test_dashboard_requires_authentication() -> None:
    directory = tempfile.mkdtemp()
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=False, host="127.0.0.1"
    )
    app = create_app(
        db_path=os.path.join(directory, "app.sqlite"), auth_settings=settings
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/dashboard/stats").status_code == 401
