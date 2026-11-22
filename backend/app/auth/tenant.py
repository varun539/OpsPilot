"""Tenant identifier validation for verified tenant identity.

Tenant identity may only come from a successfully verified JWT claim (or, in
explicit development mode, from server-side configuration). This module holds
the single conservative format check used by both paths so the two can never
drift apart.
"""

from __future__ import annotations

import re
from typing import Any

from backend.app.auth.exceptions import InvalidTokenError

# Conservative: alphanumeric first character, then up to 63 more alphanumeric,
# underscore or hyphen characters. No whitespace, control characters, dots,
# slashes or other path separators can satisfy this pattern.
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

MAX_TENANT_ID_LENGTH = 64


def is_valid_tenant_id(value: object) -> bool:
    """Return True only for a well-formed tenant identifier."""
    if not isinstance(value, str):
        return False
    if len(value) > MAX_TENANT_ID_LENGTH:
        return False
    return TENANT_ID_PATTERN.fullmatch(value) is not None


def tenant_id_from_claim(value: Any) -> str | None:
    """Validate a tenant value read from a verified JWT claim.

    ``None`` means the claim was absent, which is permitted by the transitional
    Phase 26.1 contract. Any present-but-invalid value (empty string, wrong
    type, whitespace, control characters, path separators, excessive length) is
    rejected: a token that asserts a malformed tenant must not authenticate.
    """
    if value is None:
        return None
    if not is_valid_tenant_id(value):
        raise InvalidTokenError("invalid tenant claim")
    return value
