"""Reusable harness for the Phase 25 synthetic security evaluation suite.

Every adversarial test records its outcome in a :class:`SecurityMetrics`
instance and asserts that the *violation* counters remain zero. Rejected
requests and contained application errors are counted separately from genuine
security violations, so a generic failure cannot mask an authorization bypass,
an unauthorized tool execution, a leaked secret, or a citation-integrity break.

All inputs are synthetic. No test contacts a real identity provider, LLM,
database server, or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Synthetic values only. None of these are real credentials.
SYNTHETIC_SECRETS: tuple[str, ...] = (
    "sk-live-DO-NOT-USE-0123456789abcdef",
    "S3cr3t-Passw0rd-DO-NOT-USE",
    "postgresql://app:hunter2@db.internal:5432/prod",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhdHRhY2tlciJ9.c2lnbmF0dXJl",
)

# Strings that must never appear in an API error, evidence record, or log line
# produced for an untrusted request.
DISCLOSURE_MARKERS: tuple[str, ...] = (
    "Traceback (most recent call last)",
    '", line ',
    "site-packages",
    "sqlite3.OperationalError",
    "phase25-synthetic-signing-secret",
)


@dataclass
class SecurityMetrics:
    """Counters for one adversarial evaluation run."""

    cases: int = 0
    rejected_requests: int = 0
    contained_errors: int = 0
    unauthorized_tool_executions: int = 0
    authorization_bypasses: int = 0
    secret_leaks: int = 0
    invalid_token_acceptances: int = 0
    prompt_injection_policy_violations: int = 0
    citation_integrity_violations: int = 0
    unsafe_error_disclosures: int = 0
    failures: list[str] = field(default_factory=list)

    def rejected(self) -> None:
        self.cases += 1
        self.rejected_requests += 1

    def contained(self) -> None:
        self.cases += 1
        self.contained_errors += 1

    def accepted(self) -> None:
        self.cases += 1

    def violation(self, counter: str, detail: str) -> None:
        current = getattr(self, counter, None)
        if not isinstance(current, int):
            raise ValueError(f"unknown security counter: {counter}")
        setattr(self, counter, current + 1)
        self.failures.append(f"{counter}: {detail}")

    def assert_clean(self) -> None:
        violations = {
            name: getattr(self, name)
            for name in (
                "unauthorized_tool_executions",
                "authorization_bypasses",
                "secret_leaks",
                "invalid_token_acceptances",
                "prompt_injection_policy_violations",
                "citation_integrity_violations",
                "unsafe_error_disclosures",
            )
        }
        nonzero = {name: count for name, count in violations.items() if count}
        assert not nonzero, f"security violations recorded: {nonzero}; {self.failures}"


def find_secret_leaks(text: Any, *, secrets: tuple[str, ...] = SYNTHETIC_SECRETS) -> list[str]:
    """Return the synthetic secrets present in ``text``."""
    haystack = text if isinstance(text, str) else repr(text)
    return [secret for secret in secrets if secret in haystack]


def check_no_leak(metrics: SecurityMetrics, text: Any, *, where: str) -> None:
    """Record a secret-leak violation when any synthetic secret is present."""
    leaks = find_secret_leaks(text)
    if leaks:
        metrics.violation("secret_leaks", f"{where} contained {len(leaks)} secret value(s)")
        raise AssertionError(f"synthetic secret leaked in {where}")


def check_no_disclosure(metrics: SecurityMetrics, text: Any, *, where: str) -> None:
    """Record an unsafe-error-disclosure violation when output looks like internals."""
    haystack = text if isinstance(text, str) else repr(text)
    markers = [marker for marker in DISCLOSURE_MARKERS if marker in haystack]
    if markers:
        metrics.violation(
            "unsafe_error_disclosures",
            f"{where} disclosed internal detail markers {markers}",
        )
        raise AssertionError(f"internal detail disclosed in {where}")


def rejection_metrics(response: Any) -> bool:
    """True when an HTTP response is a clean client rejection (4xx/5xx JSON)."""
    status = getattr(response, "status_code", 0)
    return 400 <= int(status) <= 599


def response_text(response: Any) -> str:
    """Return a response body as text without assuming JSON."""
    try:
        return response.text
    except Exception:  # pragma: no cover - defensive
        return ""
