import os
import tempfile

from fastapi.testclient import TestClient

from backend.app.auth.config import AuthSettings
from backend.app.main import create_app


def _client():
    d = tempfile.mkdtemp()
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=True, host="127.0.0.1"
    )
    app = create_app(db_path=os.path.join(d, "app.sqlite"), auth_settings=settings)
    return TestClient(app)


def test_create_and_get_incident() -> None:
    client = _client()
    response = client.post("/api/v1/incidents", json={"title": "boom", "severity": "high"})
    assert response.status_code == 201
    incident_id = response.json()["id"]
    fetched = client.get(f"/api/v1/incidents/{incident_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "boom"


def test_list_and_filter() -> None:
    client = _client()
    client.post("/api/v1/incidents", json={"title": "a", "severity": "high"})
    client.post("/api/v1/incidents", json={"title": "b", "severity": "low"})
    response = client.get("/api/v1/incidents", params={"severity": "high"})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert all(item["severity"] == "high" for item in response.json())


def test_pagination() -> None:
    client = _client()
    for i in range(5):
        client.post("/api/v1/incidents", json={"title": f"i{i}"})
    response = client.get("/api/v1/incidents", params={"limit": 2, "offset": 0})
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_update_incident() -> None:
    client = _client()
    created = client.post("/api/v1/incidents", json={"title": "x"}).json()
    response = client.patch(
        f"/api/v1/incidents/{created['id']}", json={"title": "y", "severity": "low"}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "y"
    assert response.json()["severity"] == "low"


def test_missing_incident_returns_404() -> None:
    client = _client()
    response = client.get("/api/v1/incidents/missing")
    assert response.status_code == 404


def test_invalid_payload_returns_422() -> None:
    client = _client()
    response = client.post("/api/v1/incidents", json={"title": ""})
    assert response.status_code == 422


def test_incident_history() -> None:
    client = _client()
    created = client.post("/api/v1/incidents", json={"title": "x"}).json()
    response = client.get(f"/api/v1/incidents/{created['id']}/history")
    assert response.status_code == 200
    assert response.json()["incident"]["id"] == created["id"]


def test_ticket_flow() -> None:
    client = _client()
    incident = client.post("/api/v1/incidents", json={"title": "x"}).json()
    ticket = client.post(
        f"/api/v1/incidents/{incident['id']}/tickets", json={"title": "t"}
    )
    assert ticket.status_code == 201
    ticket_id = ticket.json()["id"]
    assert client.get(f"/api/v1/tickets/{ticket_id}").status_code == 200
    assert (
        client.post(f"/api/v1/tickets/{ticket_id}/close").status_code == 200
    )


def test_investigation_endpoints() -> None:
    client = _client()
    incident = client.post("/api/v1/incidents", json={"title": "x"}).json()
    # create investigation via service? Use history listing empty
    response = client.get(f"/api/v1/incidents/{incident['id']}/investigations")
    assert response.status_code == 200
    assert response.json() == []
