"""JWT verification using a trusted JWKS endpoint."""

from __future__ import annotations

import logging
from typing import Any

import jwt
from jwt import PyJWKClient

from backend.app.auth.config import AuthSettings
from backend.app.auth.exceptions import AuthConfigurationError, InvalidTokenError
from backend.app.auth.models import Principal

logger = logging.getLogger("opspilot.auth")

_RECOGNIZED_ROLES = {"viewer", "operator", "admin"}


def _get_claim(claims: dict[str, Any], claim_name: str) -> Any:
    current: Any = claims
    for part in claim_name.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _role_rank(role: str) -> int:
    return {"viewer": 1, "operator": 2, "admin": 3}.get(role, 0)


def normalize_roles(raw: Any, claim_name: str) -> list[str]:
    if isinstance(raw, str):
        # Providers commonly emit space- or comma-separated role strings.
        values = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    elif isinstance(raw, dict):
        # Some providers nest roles under a key; flatten common shapes.
        values = []
        for key in ("roles", "groups", "values"):
            if key in raw:
                nested = raw[key]
                if isinstance(nested, (list, tuple, set, str)):
                    values.extend([nested] if isinstance(nested, str) else list(nested))
        if not values:
            values = list(raw.values())
    else:
        values = []

    recognized: list[str] = []
    for value in values:
        role = str(value).strip().lower()
        if role in _RECOGNIZED_ROLES and role not in recognized:
            recognized.append(role)
    if not recognized:
        recognized.append("viewer")
    return recognized


class JWTVerifier:
    """Verify bearer tokens against a configured trusted JWKS endpoint."""

    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings
        self._client: PyJWKClient | None = None

    def _configured(self) -> bool:
        return self.settings.production_configured()

    def _client_instance(self) -> PyJWKClient:
        if self._client is None:
            self._client = PyJWKClient(
                self.settings.jwks_url,
                cache_keys=True,
                max_cached_keys=32,
                cache_jwk_set=True,
                lifespan=300,
                timeout=10,
            )
        return self._client

    def verify(self, token: str) -> Principal:
        if not self._configured():
            raise AuthConfigurationError("authentication is not configured")
        if not token or not isinstance(token, str):
            raise InvalidTokenError("missing token")

        client = self._client_instance()
        try:
            signing_key = client.get_signing_key_from_jwt(token)
        except Exception as exc:  # pragma: no cover - network/JWKS failure path
            # Retry once with a fresh client to handle key rotation/cache staleness.
            try:
                self._client = None
                signing_key = self._client_instance().get_signing_key_from_jwt(token)
            except Exception as retry_exc:
                logger.warning("JWKS key lookup failed: %s", type(retry_exc).__name__)
                raise InvalidTokenError("invalid token") from retry_exc
            logger.debug("JWKS retry succeeded after: %s", type(exc).__name__)

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self.settings.algorithms,
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": False,
                    "verify_aud": True,
                    "verify_iss": True,
                    "require": ["exp", "sub", "iss", "aud"],
                },
            )
        except jwt.PyJWTError as exc:
            logger.info("JWT verification failed: %s", type(exc).__name__)
            raise InvalidTokenError("invalid token") from exc

        subject = claims.get("sub")
        if not subject:
            raise InvalidTokenError("missing subject")

        scopes_raw = claims.get("scope") or claims.get("scopes") or []
        if isinstance(scopes_raw, str):
            scopes = scopes_raw.split()
        elif isinstance(scopes_raw, (list, tuple, set)):
            scopes = [str(item) for item in scopes_raw]
        else:
            scopes = []

        roles = normalize_roles(
            _get_claim(claims, self.settings.roles_claim), self.settings.roles_claim
        )
        return Principal(subject=str(subject), roles=roles, scopes=scopes)

    verify_token = verify
