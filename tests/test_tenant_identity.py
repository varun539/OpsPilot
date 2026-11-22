"""Tenant identity tests (Phase 26.1).

Tenant identity must come only from a successfully verified JWT claim or from
explicit server-side development configuration. Every identity and token here
is synthetic: no identity provider, JWKS endpoint, LLM or network is contacted.
"""

from __future__ import annotations

import dataclasses
import time

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.app.auth.config import AuthSettings
from backend.app.auth.dependencies import get_current_principal, require_tenant
from backend.app.auth.exceptions import InvalidTokenError
from backend.app.auth.jwt import JWTVerifier
from backend.app.auth.models import Principal, TenantContext
from backend.app.auth.tenant import is_valid_tenant_id, tenant_id_from_claim
from backend.app.main import create_app

SECRET = "phase26-synthetic-signing-secret-0123456789"
ISSUER = "https://issuer.example.com/"
AUDIENCE = "opspilot-api"

UUID_TENANT = "3f2a9c1e-5b7d-4e8f-9a01-2b3c4d5e6f70"
SLUG_TENANT = "acme-inc_01"
MAX_LENGTH_TENANT = "a" * 64


class _SigningKey:
    key = SECRET


class _StaticJWKClient:
    """Stand-in for a JWKS document; performs no network access."""

    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey()


def _settings(**overrides) -> AuthSettings:
    values = {
        "auth_mode": "production",
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_url": "https://issuer.example.com/.well-known/jwks.json",
        # HMAC only so tests can sign locally; production defaults stay
        # restricted to the asymmetric family.
        "algorithms": ["HS256"],
    }
    values.update(overrides)
    return AuthSettings(**values)


def _verifier(*, settings: AuthSettings | None = None, **overrides) -> JWTVerifier:
    verifier = JWTVerifier(settings or _settings(**overrides))
    verifier._client = _StaticJWKClient()  # type: ignore[assignment]
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


@pytest.fixture
def tenant_app() -> FastAPI:
    """Isolated app exposing one route guarded by the tenant dependency.

    Phase 26.1 deliberately does not guard any OpsPilot API route with it.
    """
    app = FastAPI()

    @app.get("/tenant")
    def read_tenant(context: TenantContext = Depends(require_tenant)) -> dict[str, str]:
        return {"tenant_id": context.tenant_id, "actor_subject": context.actor_subject}

    return app


# --- A. JWT tenant claim extraction ----------------------------------------


def test_tenant_claim_is_extracted_from_a_verified_token() -> None:
    principal = _verifier().verify(_token(_claims(tenant_id=UUID_TENANT)))

    assert principal.tenant_id == UUID_TENANT
    assert principal.subject == "synthetic-user"
    assert principal.roles == ["operator"]


def test_existing_subject_roles_and_scopes_are_unchanged() -> None:
    token = _token(_claims(tenant_id=SLUG_TENANT, roles=["admin"], scope="read write"))
    principal = _verifier().verify(token)

    assert (principal.subject, principal.roles, principal.scopes) == (
        "synthetic-user",
        ["admin"],
        ["read", "write"],
    )
    assert principal.tenant_id == SLUG_TENANT


# --- B. Configurable claim name --------------------------------------------


def test_tenant_claim_name_is_configurable() -> None:
    token = _token(_claims(org_id=SLUG_TENANT, tenant_id="ignored-tenant"))

    assert _verifier(tenant_claim="org_id").verify(token).tenant_id == SLUG_TENANT


def test_tenant_claim_name_and_dev_tenant_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPSPILOT_AUTH_TENANT_CLAIM", "org_id")
    monkeypatch.setenv("OPSPILOT_DEV_TENANT_ID", "dev-tenant-0001")
    settings = AuthSettings()

    assert settings.tenant_claim == "org_id"
    assert settings.dev_tenant_id == "dev-tenant-0001"


def test_tenant_settings_defaults_are_conservative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSPILOT_AUTH_TENANT_CLAIM", raising=False)
    monkeypatch.delenv("OPSPILOT_DEV_TENANT_ID", raising=False)
    settings = AuthSettings()

    assert settings.tenant_claim == "tenant_id"
    assert settings.dev_tenant_id is None


# --- C. Dotted claim path --------------------------------------------------


def test_dotted_tenant_claim_path_is_supported() -> None:
    token = _token(_claims(org={"tenant_id": SLUG_TENANT}))

    assert _verifier(tenant_claim="org.tenant_id").verify(token).tenant_id == SLUG_TENANT


def test_dotted_claim_path_missing_leaf_yields_no_tenant() -> None:
    token = _token(_claims(org={"other": SLUG_TENANT}))

    assert _verifier(tenant_claim="org.tenant_id").verify(token).tenant_id is None


# --- D. Missing tenant claim ----------------------------------------------


def test_missing_tenant_claim_yields_no_tenant_and_no_default() -> None:
    principal = _verifier().verify(_token(_claims()))

    assert principal.tenant_id is None


def test_null_tenant_claim_is_treated_as_absent() -> None:
    assert _verifier().verify(_token(_claims(tenant_id=None))).tenant_id is None


# --- E. Empty tenant claim -------------------------------------------------


@pytest.mark.parametrize("value", ["", " ", "   ", "\t", "\n", "\x00"])
def test_empty_or_whitespace_tenant_claim_is_rejected(value: str) -> None:
    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(_claims(tenant_id=value)))


# --- F. Non-string tenant claim -------------------------------------------


@pytest.mark.parametrize("value", [123, 1.5, True, ["acme"], {"id": "acme"}, ["acme", "globex"]])
def test_non_string_tenant_claim_is_rejected(value: object) -> None:
    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(_claims(tenant_id=value)))


# --- G. Invalid characters and excessive length ---------------------------


@pytest.mark.parametrize(
    "value",
    [
        "-leading-hyphen",
        "_leading-underscore",
        ".",
        "..",
        "acme/globex",
        "acme\\globex",
        "../acme",
        "acme tenant",
        " acme",
        "acme ",
        "acme\nt enant",
        "acme\x00tenant",
        "acme.globex",
        "tenant;drop",
        "tenant%2F..",
        "*",
        "a" * 65,
        "a" * 4096,
    ],
)
def test_malformed_tenant_identifiers_are_rejected(value: str) -> None:
    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(_claims(tenant_id=value)))


def test_tenant_validator_accepts_only_conservative_identifiers() -> None:
    assert is_valid_tenant_id("a")
    assert is_valid_tenant_id(MAX_LENGTH_TENANT)
    assert not is_valid_tenant_id(MAX_LENGTH_TENANT + "a")
    assert not is_valid_tenant_id("")
    assert not is_valid_tenant_id(None)
    assert not is_valid_tenant_id(7)
    assert tenant_id_from_claim(None) is None


# --- H. Valid UUID-like and slug identifiers ------------------------------


@pytest.mark.parametrize("value", [UUID_TENANT, SLUG_TENANT, "acme", "ACME01", MAX_LENGTH_TENANT])
def test_valid_tenant_identifiers_are_accepted(value: str) -> None:
    assert _verifier().verify(_token(_claims(tenant_id=value))).tenant_id == value


# --- I. Development tenant configuration ----------------------------------


def _dev_app(tmp_path, **overrides) -> FastAPI:
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=True, host="127.0.0.1", **overrides
    )
    app = create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=settings,
    )

    @app.get("/test/tenant")
    def read_tenant(context: TenantContext = Depends(require_tenant)) -> dict[str, str]:
        return {"tenant_id": context.tenant_id, "actor_subject": context.actor_subject}

    return app


def test_dev_principal_takes_its_tenant_from_configuration(tmp_path) -> None:
    app = _dev_app(tmp_path, dev_tenant_id=SLUG_TENANT)

    with TestClient(app) as client:
        response = client.get("/test/tenant")

    assert response.status_code == 200
    assert response.json() == {"tenant_id": SLUG_TENANT, "actor_subject": "dev-user"}


def test_dev_without_a_tenant_fails_closed_but_keeps_routes_working(tmp_path) -> None:
    app = _dev_app(tmp_path)

    with TestClient(app) as client:
        assert client.get("/test/tenant").status_code == 403
        # Transitional behavior: no API route is tenant-scoped in Phase 26.1.
        assert client.get("/api/v1/incidents").status_code == 200


def test_no_tenant_is_invented_for_development() -> None:
    settings = AuthSettings(auth_mode="development", dev_auth_enabled=True, host="127.0.0.1")

    assert settings.dev_tenant_id is None


@pytest.mark.parametrize("value", ["bad/tenant", "../acme", "acme tenant", "a" * 65, ""])
def test_invalid_dev_tenant_configuration_fails_at_startup(tmp_path, value: str) -> None:
    settings = AuthSettings(
        auth_mode="development",
        dev_auth_enabled=True,
        host="127.0.0.1",
        dev_tenant_id=value,
    )

    assert any("OPSPILOT_DEV_TENANT_ID" in error for error in settings.configuration_errors())
    with pytest.raises(RuntimeError):
        with TestClient(_dev_app(tmp_path, dev_tenant_id=value)):
            pass


# --- J. require_tenant success --------------------------------------------


def test_require_tenant_returns_a_validated_context(tenant_app: FastAPI) -> None:
    tenant_app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject="synthetic-user", roles=["operator"], tenant_id=UUID_TENANT
    )

    with TestClient(tenant_app) as client:
        response = client.get("/tenant")

    assert response.status_code == 200
    assert response.json() == {"tenant_id": UUID_TENANT, "actor_subject": "synthetic-user"}


def test_tenant_context_is_immutable() -> None:
    context = TenantContext(tenant_id=SLUG_TENANT, actor_subject="synthetic-user")

    with pytest.raises(dataclasses.FrozenInstanceError):
        context.tenant_id = "globex"  # type: ignore[misc]


# --- K. require_tenant failure --------------------------------------------


@pytest.mark.parametrize(
    "tenant_id",
    [None, "", " ", "bad/tenant", "../acme", "acme tenant", "a" * 65, "-leading", 123, ["acme"]],
)
def test_require_tenant_fails_closed(tenant_app: FastAPI, tenant_id: object) -> None:
    tenant_app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject="synthetic-user",
        roles=["admin"],
        tenant_id=tenant_id,  # type: ignore[arg-type]
    )

    with TestClient(tenant_app) as client:
        response = client.get("/tenant")

    assert response.status_code == 403
    assert response.json() == {"detail": "tenant context required"}
    assert "synthetic-user" not in response.text


def test_client_input_cannot_supply_a_tenant(tenant_app: FastAPI) -> None:
    tenant_app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject="synthetic-user", roles=["admin"]
    )

    with TestClient(tenant_app) as client:
        for kwargs in (
            {"headers": {"X-Tenant-ID": SLUG_TENANT}},
            {"headers": {"X-Tenant": SLUG_TENANT, "X-Tenant-Id": UUID_TENANT}},
            {"params": {"tenant_id": SLUG_TENANT}},
            {"params": {"tenant": SLUG_TENANT}},
        ):
            assert client.get("/tenant", **kwargs).status_code == 403


def test_tenant_claim_cannot_override_a_missing_verified_principal_tenant() -> None:
    """A verified token without a tenant claim never gains one downstream."""
    principal = _verifier().verify(_token(_claims()))

    assert principal.tenant_id is None


# --- L. Existing verification guarantees remain intact --------------------


def test_existing_verification_guarantees_hold_with_a_tenant_claim() -> None:
    now = int(time.time())
    cases = [
        _token(_claims(tenant_id=SLUG_TENANT), secret="a-different-synthetic-key"),
        _token(_claims(tenant_id=SLUG_TENANT, iss="https://attacker.example/")),
        _token(_claims(tenant_id=SLUG_TENANT, aud="another-api")),
        _token(_claims(tenant_id=SLUG_TENANT, iat=now - 600, exp=now - 60)),
        _token(_claims(tenant_id=SLUG_TENANT, nbf=now + 600, exp=now + 900)),
    ]
    for token in cases:
        with pytest.raises(InvalidTokenError):
            _verifier().verify(token)

    unsigned = jwt.encode(_claims(tenant_id=SLUG_TENANT), key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        _verifier().verify(unsigned)


def test_algorithm_restriction_still_applies_to_tenant_tokens() -> None:
    token = _token(_claims(tenant_id=SLUG_TENANT), algorithm="HS256")

    with pytest.raises(InvalidTokenError):
        _verifier(algorithms=["RS256"]).verify(token)


@pytest.mark.parametrize("claim", ["exp", "sub", "iss", "aud"])
def test_required_claims_are_still_enforced(claim: str) -> None:
    payload = _claims(tenant_id=SLUG_TENANT)
    payload.pop(claim)

    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(payload))


# --- M. Exact tenant identifier format contract ---------------------------


@pytest.mark.parametrize("value", ["acme", "acme-north", "acme_north", "Acme123", UUID_TENANT])
def test_required_identifier_shapes_are_accepted(value: str) -> None:
    assert is_valid_tenant_id(value)
    assert _verifier().verify(_token(_claims(tenant_id=value))).tenant_id == value


@pytest.mark.parametrize(
    "value",
    [
        "acme north",
        " acme",
        "acme ",
        "acme/north",
        "acme\\north",
        "",
        "\x00",
        "\x1f",
        "acme\x00north",
        "acme\x7fnorth",
        "a" * 65,
    ],
)
def test_required_identifier_shapes_are_rejected(value: str) -> None:
    assert not is_valid_tenant_id(value)
    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(_claims(tenant_id=value)))


@pytest.mark.parametrize("value", [1, 1.0, True, False, ["acme"], {"tenant": "acme"}])
def test_non_string_identifiers_are_rejected(value: object) -> None:
    assert not is_valid_tenant_id(value)
    with pytest.raises(InvalidTokenError):
        _verifier().verify(_token(_claims(tenant_id=value)))


# --- N. Tenant claim configuration validation -----------------------------


def _dev_settings(**overrides) -> AuthSettings:
    values = {"auth_mode": "development", "dev_auth_enabled": True, "host": "127.0.0.1"}
    values.update(overrides)
    return AuthSettings(**values)


def _tenant_claim_errors(claim: object) -> list[str]:
    return [
        error
        for error in _dev_settings(tenant_claim=claim).configuration_errors()
        if "OPSPILOT_AUTH_TENANT_CLAIM" in error
    ]


@pytest.mark.parametrize(
    "claim", ["tenant_id", "org.tenant_id", "org.tenant.tenant_id", "roles", "app_metadata.tenant"]
)
def test_valid_tenant_claim_paths_are_accepted(claim: str) -> None:
    assert _tenant_claim_errors(claim) == []


@pytest.mark.parametrize(
    "claim",
    [
        "",
        "   ",
        " tenant_id",
        "tenant_id ",
        "\ttenant_id",
        ".tenant_id",
        "tenant_id.",
        "org..tenant_id",
        "org. tenant_id",
        "org.tenant_id ",
        "..",
    ],
)
def test_invalid_tenant_claim_paths_are_configuration_errors(claim: str) -> None:
    errors = _tenant_claim_errors(claim)

    assert errors and "non-empty claim name" in errors[0]


def test_tenant_claim_default_remains_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSPILOT_AUTH_TENANT_CLAIM", raising=False)

    assert AuthSettings().tenant_claim == "tenant_id"
    assert _tenant_claim_errors("tenant_id") == []


def test_malformed_tenant_claim_fails_at_startup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPSPILOT_AUTH_TENANT_CLAIM", "org..tenant_id")

    assert _tenant_claim_errors("org..tenant_id")
    with pytest.raises(RuntimeError):
        with TestClient(_dev_app(tmp_path)):
            pass


def test_valid_dotted_tenant_claim_still_verifies_tokens() -> None:
    settings = _settings(tenant_claim="app_metadata.tenant")
    token = _token(_claims(app_metadata={"tenant": SLUG_TENANT}))

    assert _verifier(settings=settings).verify(token).tenant_id == SLUG_TENANT
