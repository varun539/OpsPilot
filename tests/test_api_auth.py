import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from backend.app.auth.config import AuthSettings
from backend.app.auth.dependencies import get_current_principal
from backend.app.auth.models import Principal
from backend.app.main import create_app


def _dev_settings(**overrides) -> AuthSettings:
    values = {
        "auth_mode": "development",
        "dev_auth_enabled": True,
        "host": "127.0.0.1",
    }
    values.update(overrides)
    return AuthSettings(**values)


def test_missing_token_returns_401() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(
            db_path=os.path.join(d, "app.sqlite"),
            auth_settings=AuthSettings(
                auth_mode="development", dev_auth_enabled=False, host="127.0.0.1"
            ),
        )
        client = TestClient(app)
        response = client.get("/api/v1/incidents")
        assert response.status_code == 401


def test_dev_mode_allows_local_access() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(
            db_path=os.path.join(d, "app.sqlite"), auth_settings=_dev_settings()
        )
        client = TestClient(app)
        response = client.get("/api/v1/incidents")
        assert response.status_code == 200


def test_dev_mode_disabled_by_default() -> None:
    with tempfile.TemporaryDirectory() as d:
        settings = AuthSettings(auth_mode="development", host="127.0.0.1")
        app = create_app(db_path=os.path.join(d, "app.sqlite"), auth_settings=settings)
        client = TestClient(app)
        response = client.get("/api/v1/incidents")
        assert response.status_code == 401


def test_dev_mode_public_host_raises() -> None:
    with pytest.raises(RuntimeError):
        create_app(
            auth_settings=AuthSettings(
                auth_mode="development", dev_auth_enabled=True, host="0.0.0.0"
            )
        )


def test_production_unconfigured_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(
            db_path=os.path.join(d, "app.sqlite"),
            auth_settings=AuthSettings(auth_mode="production"),
        )
        with pytest.raises(RuntimeError, match="OPSPILOT_AUTH_ISSUER"):
            with TestClient(app):
                pass


def test_production_rejects_development_auth_bypass() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(
            db_path=os.path.join(d, "app.sqlite"),
            auth_settings=AuthSettings(
                auth_mode="production",
                dev_auth_enabled=True,
                host="127.0.0.1",
                issuer="https://issuer.example",
                audience="opspilot-api",
                jwks_url="https://issuer.example/.well-known/jwks.json",
            ),
        )
        with pytest.raises(RuntimeError, match="OPSPILOT_DEV_AUTH_ENABLED"):
            with TestClient(app):
                pass


def test_production_rejects_non_https_jwks() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(
            db_path=os.path.join(d, "app.sqlite"),
            auth_settings=AuthSettings(
                auth_mode="production",
                issuer="https://issuer.example",
                audience="opspilot-api",
                jwks_url="http://issuer.example/jwks.json",
            ),
        )
        with pytest.raises(RuntimeError, match="https"):
            with TestClient(app):
                pass


def test_invalid_auth_mode_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(
            db_path=os.path.join(d, "app.sqlite"),
            auth_settings=AuthSettings(auth_mode="staging"),
        )
        with pytest.raises(RuntimeError, match="OPSPILOT_AUTH_MODE"):
            with TestClient(app):
                pass


def test_viewer_cannot_create_incident() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(
            db_path=os.path.join(d, "app.sqlite"),
            checkpoint_db_path=os.path.join(d, "checkpoints.sqlite"),
            auth_settings=_dev_settings(),
        )
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            subject="viewer", roles=["viewer"]
        )
        with TestClient(app) as client:
            assert client.get("/api/v1/incidents").status_code == 200
            assert (
                client.post("/api/v1/incidents", json={"title": "nope"}).status_code
                == 403
            )
            assert (
                client.post(
                    "/api/v1/incidents/missing/investigations", json={}
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/api/v1/investigations/missing/approval",
                    json={"decision": "approved"},
                ).status_code
                == 403
            )
