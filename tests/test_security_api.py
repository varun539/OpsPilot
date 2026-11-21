"""Adversarial tests for API security controls (Phase 25)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.auth.config import AuthSettings
from backend.app.main import create_app


def _app(tmp_path, **kwargs):
    settings = kwargs.pop("auth_settings", None) or AuthSettings(
        auth_mode="development", dev_auth_enabled=True, host="127.0.0.1"
    )
    return create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=settings,
        **kwargs,
    )


def test_oversized_request_body_is_rejected(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        huge = "x" * (2 * 1024 * 1024 + 256)
        response = client.post(
            "/api/v1/incidents/does-not-exist/investigations",
            json={"logs": huge},
        )
        assert response.status_code == 413
        body = response.json()
        assert body["error"]["code"] == "payload_too_large"
        assert "request_id" in body["error"]
        # No internal details leaked.
        assert "traceback" not in response.text.lower()


def test_normal_sized_request_passes_body_limit(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/incidents/does-not-exist/investigations",
            json={"logs": "ERROR something"},
        )
        # Incident does not exist -> 404, but body limit must not reject (not 413).
        assert response.status_code != 413


def test_request_id_is_sanitized_and_echoed(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        # Valid id is preserved.
        response = client.get("/health", headers={"X-Request-ID": "abc-123_valid.id:4"})
        assert response.headers.get("X-Request-ID") == "abc-123_valid.id:4"

        # Invalid id (whitespace / newline) is replaced with a fresh uuid.
        bad = "evil\nX-Injected: true"
        response = client.get("/health", headers={"X-Request-ID": bad})
        echoed = response.headers.get("X-Request-ID")
        assert echoed != bad
        # uuid4 format
        assert len(echoed) == 36 and echoed.count("-") == 4


def test_security_headers_present(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "no-referrer"
        # Cache-Control is added for /api paths.
        api_resp = client.get("/api/v1/investigations/does-not-exist")
        assert "no-store" in api_resp.headers.get("Cache-Control", "")


def test_rate_limit_returns_429_with_retry_after(tmp_path):
    app = _app(tmp_path, rate_limit_requests=1, rate_limit_window_seconds=60)
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/incidents/does-not-exist/investigations",
            json={"logs": "ERROR x"},
        )
        # First call consumes the single allowed request (may 404 on incident).
        assert first.status_code in (200, 202, 404, 503)
        second = client.post(
            "/api/v1/incidents/does-not-exist/investigations",
            json={"logs": "ERROR x"},
        )
        assert second.status_code == 429
        body = second.json()
        assert body["error"]["code"] == "rate_limited"
        assert "Retry-After" in second.headers
        assert int(second.headers["Retry-After"]) >= 1


def test_error_responses_do_not_leak_tracebacks(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/investigations/does-not-exist")
        assert response.status_code == 404
        body = response.json()
        assert set(body["error"].keys()) == {"code", "message", "request_id"}
        assert "traceback" not in response.text.lower()
        assert "File " not in response.text


def test_validation_error_is_generic(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/incidents/x/investigations",
            json={"http_status": "not-a-number"},
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        # No echo of the offending value.
        assert "not-a-number" not in response.text


def test_cors_allows_configured_origin(tmp_path):
    settings = AuthSettings(
        auth_mode="development",
        dev_auth_enabled=True,
        host="127.0.0.1",
        cors_origins=["http://localhost:3000"],
    )
    app = _app(tmp_path, auth_settings=settings)
    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"


def test_cors_does_not_reflect_unconfigured_origin(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"


def test_health_does_not_disclose_secrets(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/health")
        text = response.text.lower()
        for token in ("secret", "password", "api_key", "jwt", "bearer"):
            assert token not in text, f"health disclosed {token}"
        assert response.status_code == 200
