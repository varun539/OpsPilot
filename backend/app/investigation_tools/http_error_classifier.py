"""Deterministic HTTP error classification tool."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import Any

from .models import DiagnosticFinding, DiagnosticResult, Severity
from .sanitization import redact_text

TOOL_NAME = "http_error_classifier"

# Knowledge-base document IDs associated with specific statuses.
_KB_REFS: dict[int, list[str]] = {
    400: [],
    401: ["KB-004"],
    403: ["KB-004"],
    404: [],
    408: ["KB-005"],
    409: [],
    422: [],
    429: ["KB-006"],
    500: ["KB-001"],
    502: ["KB-007"],
    503: ["KB-007"],
    504: ["KB-005"],
}

# Detailed entries for common statuses.
_DETAILS: dict[int, dict[str, Any]] = {
    400: {
        "category": "client_error",
        "explanation": "The request was malformed or failed server-side validation.",
        "common_causes": [
            "Missing or invalid request body fields.",
            "Malformed JSON or query parameters.",
            "Content-Type mismatch.",
        ],
        "recommendations": [
            "Validate the request payload against the API schema.",
            "Inspect client-side serialization and required fields.",
        ],
    },
    401: {
        "category": "client_error",
        "explanation": "Authentication is required or the supplied credentials are invalid.",
        "common_causes": [
            "Missing Authorization header.",
            "Expired or invalid token.",
            "Wrong issuer, audience, or signing key.",
        ],
        "recommendations": [
            "Verify token validity, expiry, and signing configuration.",
            "Check for clock skew and key rotation.",
        ],
    },
    403: {
        "category": "client_error",
        "explanation": "The authenticated identity is not permitted to perform this action.",
        "common_causes": [
            "Insufficient permissions or roles.",
            "Resource-level access policy denial.",
            "CSRF or origin restrictions.",
        ],
        "recommendations": [
            "Review authorization policies and role assignments.",
            "Confirm the correct identity is being used.",
        ],
    },
    404: {
        "category": "client_error",
        "explanation": "The requested resource or route does not exist.",
        "common_causes": [
            "Incorrect URL or resource identifier.",
            "Resource was deleted or never created.",
            "Routing or prefix mismatch behind a proxy.",
        ],
        "recommendations": [
            "Verify the request path and resource identifier.",
            "Check proxy routing and root_path configuration.",
        ],
    },
    408: {
        "category": "client_error",
        "explanation": "The server timed out waiting for the request.",
        "common_causes": [
            "Slow client upload.",
            "Network latency or packet loss.",
            "Proxy idle timeout.",
        ],
        "recommendations": [
            "Retry the request; check client and network latency.",
            "Review timeout configuration at proxy and server.",
        ],
    },
    409: {
        "category": "client_error",
        "explanation": "The request conflicts with the current state of the resource.",
        "common_causes": [
            "Duplicate creation attempt.",
            "Stale update or version mismatch.",
            "Concurrent modification conflict.",
        ],
        "recommendations": [
            "Fetch the current resource state and retry with updated data.",
            "Use idempotency keys where appropriate.",
        ],
    },
    422: {
        "category": "client_error",
        "explanation": "The request was well-formed but semantically invalid.",
        "common_causes": [
            "Validation errors in business logic.",
            "Invalid field values or relationships.",
            "Unprocessable entity due to state constraints.",
        ],
        "recommendations": [
            "Inspect validation error details in the response body.",
            "Correct the offending fields and retry.",
        ],
    },
    429: {
        "category": "client_error",
        "explanation": "Rate limiting or request throttling has been applied.",
        "common_causes": [
            "Exceeded configured request quota.",
            "Burst traffic beyond token bucket capacity.",
            "Shared or mis-derived rate-limit key.",
        ],
        "recommendations": [
            "Honor Retry-After and use exponential backoff with jitter.",
            "Review rate-limit key derivation and quotas.",
        ],
    },
    500: {
        "category": "server_error",
        "explanation": "Internal server-side application failure.",
        "common_causes": [
            "Unhandled exception in application code.",
            "Response validation failure.",
            "Misconfiguration consumed at request time.",
        ],
        "recommendations": [
            "Inspect server logs and tracebacks for the failing request.",
            "Reproduce with server exceptions enabled.",
        ],
    },
    502: {
        "category": "server_error",
        "explanation": "Bad gateway or upstream response failure.",
        "common_causes": [
            "Upstream service unavailable or returned invalid response.",
            "Proxy could not connect to backend.",
            "Backend crashed or restarted.",
        ],
        "recommendations": [
            "Check upstream service health and proxy configuration.",
            "Verify backend binding and reachability.",
        ],
    },
    503: {
        "category": "server_error",
        "explanation": "Service temporarily unavailable.",
        "common_causes": [
            "Service overloaded or shutting down.",
            "Dependency unavailable.",
            "Maintenance or deployment in progress.",
        ],
        "recommendations": [
            "Retry with backoff; check service health and capacity.",
            "Inspect dependency availability and deployment state.",
        ],
    },
    504: {
        "category": "server_error",
        "explanation": "Gateway timeout.",
        "common_causes": [
            "Upstream did not respond within the proxy deadline.",
            "Slow query or blocked event loop.",
            "Missing downstream timeout budget.",
        ],
        "recommendations": [
            "Measure where time is spent; fix retained work.",
            "Align timeout budgets across layers.",
        ],
    },
}


def _category_for(code: int) -> str:
    if 100 <= code < 200:
        return "informational"
    if 200 <= code < 300:
        return "success"
    if 300 <= code < 400:
        return "redirection"
    if 400 <= code < 500:
        return "client_error"
    if 500 <= code < 600:
        return "server_error"
    return "unknown"


def _reason_phrase(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return "Unknown Status Code"


def _parse_retry_after(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"raw": value}
    if value is None:
        return result
    if isinstance(value, (int, float)):
        result["seconds"] = int(value)
        return result
    text = str(value).strip()
    result["raw"] = text
    # seconds?
    if re.fullmatch(r"\d+", text):
        result["seconds"] = int(text)
        return result
    # HTTP date
    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = (dt - datetime.now(timezone.utc)).total_seconds()
            result["seconds"] = max(0, int(delta))
            result["http_date"] = text
        return result
    except (TypeError, ValueError, OverflowError):
        return result


def classify_http_error(status_code: int, **options: Any) -> DiagnosticResult:
    response_text = options.get("response_text", options.get("response_body", options.get("body")))
    headers = options.get("headers")
    retry_after = options.get("retry_after", options.get("Retry_After", options.get("retry-after")))
    max_snippet = options.get("max_snippet_chars", 500)

    warnings: list[str] = []

    # Coerce status code
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return DiagnosticResult(
            tool_name=TOOL_NAME,
            status="invalid_input",
            summary="Invalid HTTP status code supplied.",
            findings=[],
            analyzed_items=0,
            warnings=["status_code must be an integer between 100 and 599."],
            metadata={"supplied_status_code": status_code},
        )

    if code < 100 or code > 599:
        return DiagnosticResult(
            tool_name=TOOL_NAME,
            status="invalid_input",
            summary="HTTP status code out of valid range.",
            findings=[],
            analyzed_items=0,
            warnings=[f"Status code {code} is outside the valid 100-599 range."],
            metadata={"status_code": code},
        )

    # Retry-After from headers if not explicitly provided
    if retry_after is None and isinstance(headers, dict):
        for k, v in headers.items():
            if str(k).lower() == "retry-after":
                retry_after = v
                break

    reason = _reason_phrase(code)
    category = _DETAILS.get(code, {}).get("category", _category_for(code))
    detail = _DETAILS.get(code, {})
    kb_refs = _KB_REFS.get(code, [])

    # Severity
    if code >= 500:
        severity = Severity.high
    elif code == 429 or code in (401, 403):
        severity = Severity.medium
    elif code >= 400:
        severity = Severity.medium
    else:
        severity = Severity.informational

    retry_info = _parse_retry_after(retry_after) if retry_after is not None else None

    findings: list[DiagnosticFinding] = []
    finding = DiagnosticFinding(
        code=f"HTTP_{code}",
        title=f"HTTP {code} {reason}",
        description=detail.get("explanation", f"HTTP {code} {reason}."),
        severity=severity,
        evidence=[f"Status code: {code}"]
        + ([f"Retry-After: {retry_after}"] if retry_after is not None else []),
        source=TOOL_NAME,
        remediation_suggestions=detail.get(
            "recommendations", ["Review the failing request and server logs."]
        ),
        observed_facts=[f"HTTP status code {code} ({reason})."],
        detected_patterns=[f"http_{code}"],
        potential_explanations=detail.get("common_causes", []),
        recommended_next_steps=detail.get("recommendations", []),
        metadata={"kb_refs": kb_refs},
    )
    findings.append(finding)

    # Response text handling
    response_snippet = None
    if response_text is not None:
        text = str(response_text)
        snippet = text[:max_snippet] if max_snippet else text
        if len(text) > (max_snippet or len(text)):
            warnings.append(f"Response text truncated to {max_snippet} characters.")
        response_snippet = redact_text(snippet)

    # Additional finding for retry-after
    if retry_info and "seconds" in retry_info:
        findings.append(
            DiagnosticFinding(
                code="HTTP_RETRY_AFTER",
                title="Retry-After information present",
                description=f"Retry suggested after {retry_info['seconds']} seconds.",
                severity=Severity.informational,
                evidence=[f"Retry-After: {retry_after}"],
                source=TOOL_NAME,
                remediation_suggestions=["Wait the indicated interval before retrying."],
                observed_facts=[f"Retry-After resolves to {retry_info['seconds']} seconds."],
            )
        )

    metadata: dict[str, Any] = {
        "status_code": code,
        "reason_phrase": reason,
        "category": category,
        "http_category": category,
        "kb_refs": kb_refs,
        "related_kb_documents": kb_refs,
        "knowledge_base_categories": list(kb_refs),
        "retry_after": retry_info,
        "response_text_snippet": response_snippet,
        "response_text_length": len(str(response_text)) if response_text is not None else 0,
        "truncated": bool(warnings),
    }

    common_causes = detail.get("common_causes", [])
    recommendations = detail.get("recommendations", [])

    summary = f"Classified HTTP {code} {reason} as {category.replace('_', ' ')}."

    return DiagnosticResult(
        tool_name=TOOL_NAME,
        status="classified",
        summary=summary,
        findings=findings,
        analyzed_items=1,
        warnings=warnings,
        metadata=metadata,
        observed_facts=[f"HTTP status code {code} observed."],
        detected_patterns=[f"http_{code}"],
        potential_explanations=common_causes,
        recommended_next_steps=recommendations,
    )
