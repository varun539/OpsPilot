"""Unit tests for bearer-token verification and role extraction."""

from __future__ import annotations

import time

import jwt
import pytest

from backend.app.auth.config import AuthSettings
from backend.app.auth.exceptions import AuthConfigurationError, InvalidTokenError
from backend.app.auth.jwt import JWTVerifier, normalize_roles

SECRET = "test-only-signing-secret-0123456789abcd"
ISSUER = "https://issuer.example.com/"
AUDIENCE = "opspilot-api"


class _SigningKey:
    key = SECRET


class _StaticJWKClient:
    """Return a fixed local key instead of fetching a remote JWKS document."""

    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey()


def _settings(**overrides) -> AuthSettings:
    values = {
        "auth_mode": "production",
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_url": "https://issuer.example.com/.well-known/jwks.json",
        # HMAC is used only so tests can sign tokens locally; production
        # defaults allow the asymmetric family only.
        "algorithms": ["HS256"],
    }
    values.update(overrides)
    return AuthSettings(**values)


def _verifier(**overrides) -> JWTVerifier:
    verifier = JWTVerifier(_settings(**overrides))
    verifier._client = _StaticJWKClient()  # type: ignore[assignment]
    return verifier


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-1",
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


def test_valid_token_yields_principal_with_roles_and_scopes() -> None:
    token = _token(_claims(roles=["operator", "viewer"], scope="read write"))
    principal = _verifier().verify(token)

    assert principal.subject == "user-1"
    assert principal.roles == ["operator", "viewer"]
    assert principal.scopes == ["read", "write"]


def test_roles_are_read_from_the_configured_nested_claim() -> None:
    token = _token(_claims(realm_access={"roles": ["admin"]}))
    principal = _verifier(roles_claim="realm_access.roles").verify(token)

    assert principal.roles == ["admin"]


def test_unrecognized_roles_fall_back_to_viewer() -> None:
    token = _token(_claims(roles=["superuser"]))
    assert _verifier().verify(token).roles == ["viewer"]


def test_missing_roles_claim_falls_back_to_viewer() -> None:
    claims = _claims()
    claims.pop("roles")
    assert _verifier().verify(_token(claims)).roles == ["viewer"]


def test_normalize_roles_handles_strings_and_deduplicates() -> None:
    assert normalize_roles(" ADMIN, operator ", "roles") == ["admin", "operator"]
    assert normalize_roles(["admin", "ADMIN"], "roles") == ["admin"]
    assert normalize_roles({"groups": ["operator"]}, "roles") == ["operator"]


def test_expired_token_is_rejected() -> None:
    now = int(time.time())
    token = _token(_claims(iat=now - 600, exp=now - 60))

    with pytest.raises(InvalidTokenError):
        _verifier().verify(token)


def test_wrong_audience_is_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(_claims(aud="another-api")))


def test_wrong_issuer_is_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(_claims(iss="https://attacker.example/")))


def test_missing_required_claim_is_rejected() -> None:
    claims = _claims()
    claims.pop("exp")

    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(claims))


def test_token_signed_with_a_different_key_is_rejected() -> None:
    token = _token(_claims(), secret="not-the-configured-key-0123456789")

    with pytest.raises(InvalidTokenError):
        _verifier().verify(token)


def test_unsigned_token_is_rejected() -> None:
    token = jwt.encode(_claims(), key="", algorithm="none")

    with pytest.raises(InvalidTokenError):
        _verifier().verify(token)


def test_unconfigured_verifier_fails_closed() -> None:
    verifier = JWTVerifier(AuthSettings(auth_mode="production"))

    with pytest.raises(AuthConfigurationError):
        verifier.verify("anything")
