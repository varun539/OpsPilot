"""API-level tests for AI runtime modes and readiness."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.ai import AIRuntimeConfigurationError
from backend.app.auth.config import AuthSettings
from backend.app.main import create_app


def _app(tmp_path: Path):
    return create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=AuthSettings(
            auth_mode="development", dev_auth_enabled=True, host="127.0.0.1"
        ),
    )


def test_disabled_mode_refuses_investigations_without_fake_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPSPILOT_AI_MODE", "disabled")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = _app(tmp_path)

    with TestClient(app) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "service": "opspilot-api",
        }
        readiness = client.get("/health/ai")
        assert readiness.status_code == 200
        body = readiness.json()
        assert body["mode"] == "disabled"
        assert body["ready"] is False
        assert body["reason"] == "ai_runtime_disabled"
        assert body["checks"]["provider"] is False
        assert "OPENAI_API_KEY" not in readiness.text

        incident = client.post("/api/v1/incidents", json={"title": "boom"}).json()
        response = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations",
            json={"logs": "ERROR boom"},
        )
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "ai_unavailable"
        assert "disabled" in error["message"]

        listed = client.get(
            f"/api/v1/incidents/{incident['id']}/investigations"
        ).json()
        assert listed == []


def test_live_mode_without_api_key_fails_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPSPILOT_AI_MODE", "live")
    monkeypatch.setenv("OPSPILOT_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("OPSPILOT_LLM_MODEL", "gemini-free-test")
    for name in ("OPSPILOT_LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    app = _app(tmp_path)

    with pytest.raises(AIRuntimeConfigurationError, match="GEMINI_API_KEY"):
        with TestClient(app):
            pass


def test_paid_openai_provider_is_rejected_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPSPILOT_AI_MODE", "live")
    monkeypatch.setenv("OPSPILOT_LLM_PROVIDER", "openai")
    app = _app(tmp_path)

    with pytest.raises(AIRuntimeConfigurationError, match="must be one of"):
        with TestClient(app):
            pass


def test_unknown_mode_fails_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPSPILOT_AI_MODE", "sometimes")
    app = _app(tmp_path)

    with pytest.raises(AIRuntimeConfigurationError, match="OPSPILOT_AI_MODE"):
        with TestClient(app):
            pass
