"""API contract tests for OpsPilot."""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_returns_200() -> None:
    response = client.get("/health")

    assert response.status_code == 200


def test_health_payload_is_exact() -> None:
    response = client.get("/health")

    assert response.json() == {"status": "ok", "service": "opspilot-api"}
