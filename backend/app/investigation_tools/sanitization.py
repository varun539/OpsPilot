"""Input sanitization and secret redaction helpers.

All investigation tools treat their input as untrusted data.  These helpers
provide bounded parsing and redaction so that secrets are never returned in
diagnostic output.
"""

from __future__ import annotations

import re
from typing import Any

# Keys whose values must never be returned verbatim.
SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "client_secret",
    "credential",
    "auth",
    "authorization",
    "bearer",
    "dsn",
    "connection_string",
    "database_url",
    "db_url",
)

_REDACTED = "<redacted>"

# key=value or key: value (JSON-ish or env-ish)
_KV_RE = re.compile(
    (
        r"(?P<key>[A-Za-z0-9_.\-]{1,64})\s*[:=]\s*"
        r"(?P<quote>[\"']?)(?P<value>[^\s,\"'}\]]{4,})(?P=quote)"
    ),
    re.IGNORECASE,
)

# Bearer tokens
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}")

# URLs with embedded credentials
_URL_CREDS_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")

# Long hex/base64-ish tokens (>=20 chars) that are not ordinary words
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def is_sensitive_key(key: Any) -> bool:
    """Return True if a configuration key looks sensitive."""
    lowered = str(key).lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact_value(key: Any, value: Any) -> Any:
    """Return a redacted placeholder when the key or value looks sensitive."""
    if is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        # Redact obvious inline secrets even under non-sensitive keys.
        if _URL_CREDS_RE.search(value):
            value = _URL_CREDS_RE.sub(r"\1<redacted>:<redacted>@", value)
        if _BEARER_RE.search(value):
            value = _BEARER_RE.sub("Bearer <redacted>", value)
        return value
    if isinstance(value, dict):
        return {k: redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(key, item) for item in value]
    return value


def redact_text(text: str) -> str:
    """Redact secrets from arbitrary untrusted text."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)

    result = text
    for match in list(_KV_RE.finditer(text)):
        key = match.group("key")
        if not is_sensitive_key(key):
            continue
        full = match.group(0)
        quote = match.group("quote")
        if "=" in full.split(key, 1)[-1][:2]:
            replacement = f"{key}={quote}{_REDACTED}{quote}"
        else:
            replacement = f"{key}: {quote}{_REDACTED}{quote}"
        result = result.replace(full, replacement, 1)

    result = _BEARER_RE.sub("Bearer <redacted>", result)
    result = _URL_CREDS_RE.sub(r"\1<redacted>:<redacted>@", result)
    return result


def redact_mapping(mapping: Any) -> Any:
    """Return a copy of a mapping with sensitive values redacted."""
    if isinstance(mapping, dict):
        return {k: redact_value(k, v) for k, v in mapping.items()}
    return mapping


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """Truncate text to ``limit`` characters, reporting whether truncation occurred."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if limit is None or limit <= 0:
        return text, False
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def split_lines_bounded(text: str, max_lines: int) -> tuple[list[str], bool]:
    """Split text into lines, bounded by ``max_lines``."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    lines = text.splitlines()
    if max_lines is None or max_lines <= 0 or len(lines) <= max_lines:
        return lines, False
    return lines[:max_lines], True
