"""Adversarial authentication tests (Phase 25).

Covers missing/malformed tokens, invalid signatures, expiry, not-before, issuer
and audience mismatch, JWKS failures, key rotation, algorithm restriction,
response/log non-disclosure, verifier reuse, and development-auth isolation.

Signing uses a synthetic local key: no JWKS endpoint is contacted.
"""

from __future__ import annotations

import logging
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.app.auth.config import AuthSettings
from backend.app.auth.dependencies import get_current_principal
from backend.app.auth.exceptions import InvalidTokenError
from backend.app.auth.jwt import JWTVerifier
from backend.app.auth.models import Principal
from backend.app.main import create_app
from tests.security_harness import (
    SYNTHETIC_SECRETS,
    SecurityMetrics,
    check_no_disclosure,
    check_no_leak,
)

SECRET = "phase25-synthetic-signing-secret-0123456789"
ISSUER = "https://issuer.example.com/"
AUDIENCE = "opspilot-api"


class _SigningKey:
    key = SECRET


class _StaticJWKClient:
    """Stands in for a JWKS endpoint; never performs network access."""

    def __init__(self, *, fail_calls: int = 0) -> None:
        self._fail_calls = fail_calls

    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        if self._fail_calls:
            self._fail_calls -= 1
            raise RuntimeError("synthetic JWKS outage")
        return _SigningKey()


def _settings(**overrides) -> AuthSettings:
    values = {
        "auth_mode": "production",
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_url": "https://issuer.example.com/.well-known/jwks.json",
        # HMAC only so tests can sign locally; production defaults allow the
        # asymmetric family exclusively.
        "algorithms": ["HS256"],
    }
    values.update(overrides)
    return AuthSettings(**values)


def _verifier(*, clients: list[_StaticJWKClient] | None = None, **overrides) -> JWTVerifier:
    verifier = JWTVerifier(_settings(**overrides))
    queue = list(clients or [_StaticJWKClient()])
    last = queue[-1]

    def factory() -> _StaticJWKClient:
        return queue.pop(0) if queue else last

    verifier._client_instance = factory  # type: ignore[method-assign]
    return verifier


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "synthetic-user",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "roles": ["operator"],
    }
    claims.update(overrides)
    return claims


def _token(claims: dict, *, secret: str = SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=algorithm)


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


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-jwt",
        "a.b",
        "....",
        "Bearer",
        "eyJhbGciOiJIUzI1NiJ9.not-base64.signature",
    ],
)
def test_malformed_tokens_are_rejected(token: str) -> None:
    metrics = SecurityMetrics()
    with pytest.raises(InvalidTokenError):
        _verifier().verify(token)
        metrics.violation("invalid_token_acceptances", f"accepted malformed token {token!r}")
    metrics.rejected()
    metrics.assert_clean()


def test_expired_and_not_yet_valid_tokens_are_rejected() -> None:
    metrics = SecurityMetrics()
    now = int(time.time())
    for claims in (
        _claims(iat=now - 600, exp=now - 60),
        _claims(iat=now + 600, nbf=now + 600, exp=now + 900),
    ):
        with pytest.raises(InvalidTokenError):
            _verifier().verify(_token(claims))
            metrics.violation("invalid_token_acceptances", "accepted out-of-window token")
        metrics.rejected()
    metrics.assert_clean()


def test_wrong_issuer_audience_and_signature_are_rejected() -> None:
    metrics = SecurityMetrics()
    cases = [
        _token(_claims(iss="https://attacker.example/")),
        _token(_claims(aud="another-api")),
        _token(_claims(), secret="a-different-synthetic-signing-key"),
    ]
    for token in cases:
        with pytest.raises(InvalidTokenError):
            _verifier().verify(token)
            metrics.violation("invalid_token_acceptances", "accepted untrusted token")
        metrics.rejected()
    metrics.assert_clean()


def test_signing_algorithms_are_restricted_to_configuration() -> None:
    """A token must never select its own verification family."""
    metrics = SecurityMetrics()

    # Algorithm-confusion attempt: HMAC token presented to a verifier that only
    # trusts RS256. The JWKS stub returns a usable key, so only the explicit
    # algorithm restriction can reject this.
    hmac_token = _token(_claims(), algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        _verifier(algorithms=["RS256"]).verify(hmac_token)
        metrics.violation("invalid_token_acceptances", "algorithm confusion accepted")
    metrics.rejected()

    unsigned = jwt.encode(_claims(), key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        _verifier().verify(unsigned)
        metrics.violation("invalid_token_acceptances", "unsigned token accepted")
    metrics.rejected()

    metrics.assert_clean()


def test_missing_required_claims_are_rejected() -> None:
    metrics = SecurityMetrics()
    for claim in ("exp", "sub", "iss", "aud"):
        payload = _claims()
        payload.pop(claim)
        with pytest.raises(InvalidTokenError):
            _verifier().verify(_token(payload))
            metrics.violation("invalid_token_acceptances", f"accepted token without {claim}")
        metrics.rejected()
    metrics.assert_clean()


def test_jwks_lookup_failure_is_contained_and_generic() -> None:
    metrics = SecurityMetrics()
    metrics_reference = _verifier(clients=[_StaticJWKClient(fail_calls=99)])
    token = _token(_claims())
    with pytest.raises(InvalidTokenError) as excinfo:
        metrics_reference.verify(token)
        metrics.violation("invalid_token_acceptances", "accepted token after JWKS failure")
    metrics.contained()
    message = str(excinfo.value)
    check_no_leak(metrics, message, where="JWKS failure exception")
    check_no_disclosure(metrics, message, where="JWKS failure exception")
    assert token not in message
    metrics.assert_clean()


def test_key_rotation_retries_with_a_fresh_jwks_client() -> None:
    """A rotated signing key is recovered by exactly one retry."""
    metrics = SecurityMetrics()
    verifier = _verifier(clients=[_StaticJWKClient(fail_calls=1), _StaticJWKClient()])
    principal = verifier.verify(_token(_claims()))
    metrics.accepted()
    assert principal.subject == "synthetic-user"
    metrics.assert_clean()


def test_failed_authentication_does_not_leak_token_or_claims(caplog) -> None:
    metrics = SecurityMetrics()
    caplog.set_level(logging.DEBUG, logger="opspilot.auth")
    token = _token(_claims(sub="sensitive-subject-value", roles=["admin"]))
    bad = token[:-4] + "AAAA"

    with pytest.raises(InvalidTokenError) as excinfo:
        _verifier().verify(bad)
    metrics.contained()

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert bad not in logs and token not in logs
    assert "sensitive-subject-value" not in logs
    assert bad not in str(excinfo.value)
    check_no_leak(metrics, logs, where="authentication logs")
    metrics.assert_clean()


def test_api_returns_generic_401_for_missing_and_malformed_tokens(tmp_path) -> None:
    metrics = SecurityMetrics()
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=False, host="127.0.0.1"
    )
    app = _app(tmp_path, auth_settings=settings)
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "Bearer"}, {"Authorization": "Bearer x.y.z"}):
            response = client.get("/api/v1/incidents", headers=headers)
            assert response.status_code == 401
            body = response.text
            assert "bearer" not in body.lower()
            assert "x.y.z" not in body
            check_no_disclosure(metrics, body, where="401 response")
            check_no_leak(metrics, body, where="401 response")
            metrics.rejected()
    metrics.assert_clean()


def test_verifier_is_cached_per_app_instance(tmp_path, monkeypatch) -> None:
    """The JWKS client must not be rebuilt on every request."""
    import backend.app.auth.dependencies as dependencies

    created: list[object] = []

    class CountingVerifier:
        def __init__(self, settings):
            self.settings = settings
            created.append(self)

        def verify(self, token: str) -> Principal:
            return Principal(subject="cached-user", roles=["viewer"])

    monkeypatch.setattr(dependencies, "JWTVerifier", CountingVerifier)

    settings = _settings()
    app = create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=settings,
    )
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get(
                "/api/v1/incidents", headers={"Authorization": "Bearer synthetic"}
            ).status_code == 200
    assert len(created) == 1, "verifier (and therefore JWKS state) was rebuilt per request"


def test_development_bypass_cannot_be_enabled_in_production(tmp_path) -> None:
    metrics = SecurityMetrics()
    settings = _settings(dev_auth_enabled=True)
    errors = settings.configuration_errors()
    assert any("OPSPILOT_DEV_AUTH_ENABLED" in error for error in errors)
    app = create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=settings,
    )
    # Validation runs at startup, so the application fails closed before it
    # serves a single request.
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
    metrics.rejected()
    metrics.assert_clean()


def test_development_bypass_requires_loopback_host(tmp_path) -> None:
    metrics = SecurityMetrics()
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=True, host="0.0.0.0"
    )
    assert settings.configuration_errors()
    with pytest.raises(RuntimeError):
        create_app(
            db_path=str(tmp_path / "app.sqlite"),
            auth_settings=settings,
        )
    metrics.rejected()

    # A private (non-loopback) address must also be refused.
    assert AuthSettings(
        auth_mode="development", dev_auth_enabled=True, host="10.0.0.5"
    ).configuration_errors()
    metrics.rejected()
    metrics.assert_clean()


def test_production_requires_https_jwks_and_never_falls_back_to_dev(tmp_path) -> None:
    metrics = SecurityMetrics()
    for settings in (
        AuthSettings(auth_mode="production"),
        AuthSettings(
            auth_mode="production",
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url="http://issuer.example.com/jwks.json",
        ),
        AuthSettings(auth_mode="staging"),
    ):
        with pytest.raises(RuntimeError):
            with TestClient(_app(tmp_path, auth_settings=settings)):
                pass
        metrics.rejected()
    metrics.assert_clean()


def test_client_headers_cannot_select_a_privileged_principal(tmp_path) -> None:
    """Roles come from the verified principal, never from request headers."""
    metrics = SecurityMetrics()
    app = _app(tmp_path)
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject="viewer-only", roles=["viewer"]
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/incidents",
            json={"title": "synthetic"},
            headers={"X-Role": "admin", "X-Principal": "admin", "X-Subject": "admin"},
        )
        assert response.status_code == 403
        assert SYNTHETIC_SECRETS[0] not in response.text
        metrics.rejected()
    metrics.assert_clean()
