"""REST API tests for investigation orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.app.agent.models import InvestigationPlan
from backend.app.auth.config import AuthSettings
from backend.app.auth.dependencies import get_current_principal
from backend.app.auth.models import Principal
from backend.app.main import create_app
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


def _app(tmp_path):
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=True, host="127.0.0.1"
    )
    return create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=settings,
        llm_provider=FakeProvider(),
    )


def _incident(client: TestClient) -> dict:
    return client.post("/api/v1/incidents", json={"title": "boom"}).json()


def test_operator_can_start_investigation(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        response = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations",
            json={"logs": "ERROR boom"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["workflow_status"] == "awaiting_approval"
        assert body["approval_status"] == "pending"
        assert body["incident_id"] == incident["id"]

        fetched = client.get(f"/api/v1/investigations/{body['investigation_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["thread_id"]
        assert fetched.json()["workflow_status"] == "awaiting_approval"

        approval = client.get(
            f"/api/v1/investigations/{body['investigation_id']}/approval"
        )
        assert approval.status_code == 200
        assert approval.json()["status"] == "pending"


def test_admin_can_start_investigation(tmp_path) -> None:
    app = _app(tmp_path)
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject="admin", roles=["admin"]
    )
    with TestClient(app) as client:
        incident = _incident(client)
        response = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        )
        assert response.status_code == 202


def test_viewer_cannot_start_investigation(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            subject="viewer", roles=["viewer"]
        )
        response = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        )
        assert response.status_code == 403


def test_viewer_can_read_investigation(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        created = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            subject="viewer", roles=["viewer"]
        )
        assert (
            client.get(f"/api/v1/investigations/{created['investigation_id']}").status_code
            == 200
        )


def test_missing_incident_returns_404(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/incidents/missing/investigations", json={})
        assert response.status_code == 404


def test_invalid_input_returns_422(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        response = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations",
            json={"http_status": 999},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_oversized_input_returns_422(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        response = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations",
            json={"logs": "x" * 200001},
        )
        assert response.status_code == 422


def test_duplicate_active_investigation_returns_409(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        first = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        )
        assert first.status_code == 202
        second = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        )
        assert second.status_code == 409


def test_operator_can_approve(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        created = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        response = client.post(
            f"/api/v1/investigations/{created['investigation_id']}/approval",
            json={"decision": "approved", "reason": "ok"},
        )
        assert response.status_code == 200
        assert response.json()["workflow_status"] == "completed"
        assert response.json()["approval_status"] == "approved"
        assert client.get(f"/api/v1/incidents/{incident['id']}").json()["status"] == "investigating"


def test_viewer_cannot_approve(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        created = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            subject="viewer", roles=["viewer"]
        )
        response = client.post(
            f"/api/v1/investigations/{created['investigation_id']}/approval",
            json={"decision": "approved"},
        )
        assert response.status_code == 403


def test_invalid_decision_returns_422(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        created = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        response = client.post(
            f"/api/v1/investigations/{created['investigation_id']}/approval",
            json={"decision": "maybe"},
        )
        assert response.status_code == 422


def test_duplicate_approval_returns_409(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        created = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        url = f"/api/v1/investigations/{created['investigation_id']}/approval"
        assert client.post(url, json={"decision": "approved"}).status_code == 200
        duplicate = client.post(url, json={"decision": "approved"})
        assert duplicate.status_code == 409


def test_history_includes_events(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = _incident(client)
        client.post(f"/api/v1/incidents/{incident['id']}/investigations", json={})
        history = client.get(f"/api/v1/incidents/{incident['id']}/history")
        assert history.status_code == 200
        event_types = [event["event_type"] for event in history.json()["events"]]
        assert "investigation_started" in event_types
        assert "human_review_requested" in event_types
