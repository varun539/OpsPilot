"""HTTP hardening middleware for the OpsPilot API.

Two concerns live here, both deliberately independent of route handlers:

* a hard ceiling on request bodies, so a single request cannot force the
  synchronous investigation pipeline to buffer unbounded input; and
* reflected-value sanitization (``X-Request-ID``) plus conservative response
  security headers.

The middleware never inspects or logs request content.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

DEFAULT_MAX_REQUEST_BYTES = 2_097_152  # 2 MiB

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


def configured_max_request_bytes() -> int:
    """Return the request body ceiling, allowing an explicit override.

    ``0`` disables the ceiling. Invalid values fall back to the default rather
    than disabling the control.
    """
    raw = os.getenv("OPSPILOT_MAX_REQUEST_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_REQUEST_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REQUEST_BYTES
    if value < 0:
        return DEFAULT_MAX_REQUEST_BYTES
    return value


def sanitize_request_id(value: Any) -> str:
    """Return a safe request identifier.

    Client-supplied values are reflected into a response header and error
    envelopes, so anything outside a conservative character set is replaced
    instead of being echoed.
    """
    if isinstance(value, str) and _REQUEST_ID_RE.fullmatch(value):
        return value
    return str(uuid.uuid4())


class _BodyTooLarge(Exception):
    """Internal signal that the streaming body exceeded the configured ceiling."""


async def _send_error(send: Any, scope: dict[str, Any], *, status: int, code: str, message: str):
    state = scope.get("state") or {}
    request_id = sanitize_request_id(state.get("request_id"))
    body = json.dumps(
        {"error": {"code": code, "message": message, "request_id": request_id}}
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                *_SECURITY_HEADERS,
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` with HTTP 413.

    A declared ``Content-Length`` is checked before the handler runs. Bodies
    sent without one (chunked) are counted as they arrive so the ceiling still
    holds.
    """

    def __init__(self, app: Any, *, max_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                if declared is not None and declared > self.max_bytes:
                    await _send_error(
                        send,
                        scope,
                        status=413,
                        code="payload_too_large",
                        message="request body exceeds the configured limit",
                    )
                    return
                break

        received = 0
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b"") or b"")
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            # If the application already began a response the status cannot be
            # changed; the truncated request is still bounded, which is the
            # security property that matters.
            if not response_started:
                await _send_error(
                    send,
                    scope,
                    status=413,
                    code="payload_too_large",
                    message="request body exceeds the configured limit",
                )


def apply_security_headers(headers: Any, *, path: str) -> None:
    """Attach conservative response headers when they are not already set."""
    for name, value in _SECURITY_HEADERS:
        key = name.decode("ascii")
        if key not in headers:
            headers[key] = value.decode("ascii")
    if path.startswith("/api/") and "cache-control" not in headers:
        headers["cache-control"] = "no-store"
