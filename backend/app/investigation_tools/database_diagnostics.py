"""Deterministic database error diagnostics tool."""

from __future__ import annotations

import re
from typing import Any

from .models import DiagnosticFinding, DiagnosticResult, InputSizeError, Severity
from .sanitization import redact_text

TOOL_NAME = "database_diagnostics"

# Each pattern: (code, title, severity, regex, explanation, causes, diagnostics, kb_refs)
_PATTERNS: list[dict[str, Any]] = [
    {
        "code": "DB_CONNECTION_REFUSED",
        "title": "Database connection refused",
        "severity": Severity.high,
        "regex": re.compile(r"connection refused|could not connect to server", re.I),
        "explanation": "The client could not establish a TCP connection to the database server.",
        "causes": [
            "Database service is down or not listening on the expected port.",
            "Wrong host or port in the connection string.",
            "Firewall or security group blocking the port.",
        ],
        "diagnostics": [
            "Verify host, port, and service status.",
            "Test TCP connectivity from the application runtime.",
        ],
        "kb_refs": ["KB-003"],
    },
    {
        "code": "DB_CONNECTION_TIMEOUT",
        "title": "Database connection timeout",
        "severity": Severity.high,
        "regex": re.compile(r"connection timed out|connect timeout|timeout.*connect", re.I),
        "explanation": "The connection attempt did not complete within the configured timeout.",
        "causes": [
            "Network latency or packet loss.",
            "Firewall silently dropping packets.",
            "Database overloaded or unreachable.",
        ],
        "diagnostics": [
            "Test connectivity and measure latency.",
            "Check firewall and routing from the runtime.",
        ],
        "kb_refs": ["KB-003", "KB-005"],
    },
    {
        "code": "DB_DNS_RESOLUTION_FAILURE",
        "title": "DNS resolution failure",
        "severity": Severity.high,
        "regex": re.compile(             (
                 r"name or service not known|could not translate host name|"
                 r"temporary failure in name resolution|dns"
             ),
            re.I,
        ),
        "explanation": "The database hostname could not be resolved to an address.",
        "causes": [
            "Incorrect hostname in connection string.",
            "DNS server unavailable.",
            "Address-family mismatch (IPv6 vs IPv4).",
        ],
        "diagnostics": [
            "Resolve the hostname from the application runtime.",
            "Compare DNS results across environments.",
        ],
        "kb_refs": ["KB-003"],
    },
    {
        "code": "DB_AUTHENTICATION_FAILURE",
        "title": "Database authentication failure",
        "severity": Severity.high,
        "regex": re.compile(             (
                 r"password authentication failed|authentication failed|"
                 r"invalid password|tenant or user not found"
             ),
            re.I,
        ),
        "explanation": "The supplied database credentials were rejected.",
        "causes": [
            "Wrong username or password.",
            "Credential rotation mismatch.",
            "User does not exist on the target database.",
        ],
        "diagnostics": [
            "Verify credentials in the secret manager.",
            "Confirm target database and user exist.",
        ],
        "kb_refs": ["KB-002", "KB-003"],
    },
    {
        "code": "DB_DOES_NOT_EXIST",
        "title": "Database does not exist",
        "severity": Severity.high,
        "regex": re.compile(
            r"database .* does not exist|invalidcatalogname|database not found", re.I
        ),
        "explanation": "The target database name could not be found on the server.",
        "causes": [
            "Typo in database name.",
            "Database not provisioned or migrated.",
        ],
        "diagnostics": [
            "List databases on the server.",
            "Verify migration and provisioning steps.",
        ],
        "kb_refs": ["KB-003"],
    },
    {
        "code": "DB_TOO_MANY_CONNECTIONS",
        "title": "Too many database connections",
        "severity": Severity.high,
        "regex": re.compile(             (
                 r"too many clients already|remaining connection slots are reserved|"
                 r"too many connections"
             ),
            re.I,
        ),
        "explanation": "The database server has reached its connection limit.",
        "causes": [
            "Connection leak or excessive pool size.",
            "Too many application replicas or workers.",
            "Long-running transactions holding connections.",
        ],
        "diagnostics": [
            "Inspect pg_stat_activity and application pool sizes.",
            "Reduce concurrency or pool size.",
        ],
        "kb_refs": ["KB-008", "KB-003"],
    },
    {
        "code": "DB_POOL_EXHAUSTION",
        "title": "Connection pool exhaustion",
        "severity": Severity.high,
        "regex": re.compile(
            r"queuepool limit|pool limit.*reached|connection timed out.*pool|pool.*exhaust", re.I
        ),
        "explanation": "The application connection pool is exhausted and checkout timed out.",
        "causes": [
            "Concurrency exceeds pool capacity.",
            "Connections retained too long or leaked.",
        ],
        "diagnostics": [
            "Monitor pool checkedout count.",
            "Review session lifecycle and teardown.",
        ],
        "kb_refs": ["KB-008"],
    },
    {
        "code": "DB_CONNECTION_LEAK",
        "title": "Possible connection leak",
        "severity": Severity.medium,
        "regex": re.compile(r"connection leak|leaked connection", re.I),
        "explanation": "A connection may not have been returned to the pool.",
        "causes": [
            "Missing close/teardown on exception paths.",
            "Session stored beyond request lifetime.",
        ],
        "diagnostics": [
            "Check teardown in error paths.",
            "Monitor checkedout count over sequential requests.",
        ],
        "kb_refs": ["KB-008"],
    },
    {
        "code": "DB_SSL_ERROR",
        "title": "Database SSL/TLS error",
        "severity": Severity.high,
        "regex": re.compile(r"ssl|tls|certificate verify failed|server does not support ssl", re.I),
        "explanation": (
            "TLS negotiation or certificate validation failed for the database connection."
        ),
        "causes": [
            "Missing or invalid CA certificate.",
            "SSL mode mismatch.",
            "Proxy intercepting TLS.",
        ],
        "diagnostics": [
            "Verify sslmode and CA configuration.",
            "Test TLS handshake to the database host.",
        ],
        "kb_refs": ["KB-003"],
    },
    {
        "code": "DB_CONNECTION_RESET",
        "title": "Database connection reset",
        "severity": Severity.medium,
        "regex": re.compile(r"connection reset|broken pipe|server closed the connection", re.I),
        "explanation": "An established database connection was reset or closed unexpectedly.",
        "causes": [
            "Server restart or failover.",
            "Idle connection dropped by intermediary.",
            "Network instability.",
        ],
        "diagnostics": [
            "Check server logs and failover events.",
            "Enable pool_pre_ping and pool_recycle.",
        ],
        "kb_refs": ["KB-003", "KB-008"],
    },
    {
        "code": "DB_DEADLOCK",
        "title": "Database deadlock detected",
        "severity": Severity.high,
        "regex": re.compile(r"deadlock detected", re.I),
        "explanation": "Two or more transactions blocked each other, causing a deadlock.",
        "causes": [
            "Conflicting lock acquisition order.",
            "Long-running transactions.",
        ],
        "diagnostics": [
            "Review transaction ordering and lock usage.",
            "Reduce transaction scope.",
        ],
        "kb_refs": ["KB-008"],
    },
    {
        "code": "DB_TRANSACTION_FAILURE",
        "title": "Database transaction failure",
        "severity": Severity.medium,
        "regex": re.compile(r"transaction|rollback|commit failed|could not serialize", re.I),
        "explanation": "A transaction failed to complete successfully.",
        "causes": [
            "Constraint violation.",
            "Serialization failure.",
            "Deadlock or timeout.",
        ],
        "diagnostics": [
            "Inspect the failing SQL and constraints.",
            "Review isolation levels and retry logic.",
        ],
        "kb_refs": ["KB-008"],
    },
    {
        "code": "DB_INVALID_CONNECTION_STRING",
        "title": "Invalid database connection string",
        "severity": Severity.high,
        "regex": re.compile(             (
                 r"could not parse.*url|invalid.*connection string|"
                 r"sqlalchemy.*argumenterror|could not parse sqlalchemy"
             ),
            re.I,
        ),
        "explanation": "The database connection string could not be parsed.",
        "causes": [
            "Wrong scheme or malformed URL.",
            "Unencoded special characters in password.",
            "Mixed direct/pooler settings.",
        ],
        "diagnostics": [
            "Parse the URL without exposing credentials.",
            "Re-copy the connection string from the provider.",
        ],
        "kb_refs": ["KB-003", "KB-002"],
    },
    {
        "code": "DB_PREPARED_STATEMENT_ERROR",
        "title": "Prepared statement error (pooler mismatch)",
        "severity": Severity.medium,
        "regex": re.compile(             (
                 r"prepared statement .* does not exist|"
                 r"cannot insert multiple commands into a prepared statement"
             ),
            re.I,
        ),
        "explanation": "Prepared statements are being used against a transaction-mode pooler.",
        "causes": [
            "Driver prepared statements enabled with transaction-mode pooling.",
        ],
        "diagnostics": [
            "Disable statement caching for transaction-mode poolers.",
        ],
        "kb_refs": ["KB-003"],
    },
]


def diagnose_database_error(error_text: str, **options: Any) -> DiagnosticResult:
    max_input_chars = options.get("max_input_chars", options.get("max_chars", 200000))
    redact = options.get("redact", options.get("redact_secrets", True))
    allow_truncate = options.get("truncate", True)

    from .sanitization import truncate_text

    warnings: list[str] = []
    text = "" if error_text is None else str(error_text)
    if len(text) > max_input_chars:
        if not allow_truncate:
            raise InputSizeError("input exceeds max_input_chars")
        text, _ = truncate_text(text, max_input_chars)
        warnings.append(f"Input truncated to {max_input_chars} characters.")

    if not text.strip():
        return DiagnosticResult(
            tool_name=TOOL_NAME,
            status="empty",
            summary="No database error text was provided.",
            findings=[],
            analyzed_items=0,
            warnings=warnings,
            metadata={"truncated": False},
        )

    findings: list[DiagnosticFinding] = []
    matched_codes: set[str] = set()
    for pattern in _PATTERNS:
        if pattern["regex"].search(text):
            matched_codes.add(pattern["code"])
            # evidence: redacted snippet of matching line
            snippet = text.strip()[:300]
            findings.append(
                DiagnosticFinding(
                    code=pattern["code"],
                    title=pattern["title"],
                    description=pattern["explanation"],
                    severity=pattern["severity"],
                    evidence=[redact_text(snippet) if redact else snippet],
                    source=TOOL_NAME,
                    remediation_suggestions=pattern["diagnostics"]
                    + [f"KB refs: {', '.join(pattern['kb_refs'])}"],
                    observed_facts=[
                        f"Pattern '{pattern['title']}' matched the supplied error text."
                    ],
                    detected_patterns=[pattern["code"].lower()],
                    potential_explanations=pattern["causes"],
                    recommended_next_steps=pattern["diagnostics"],
                    metadata={"kb_refs": pattern["kb_refs"]},
                )
            )

    if not findings:
        findings.append(
            DiagnosticFinding(
                code="DB_UNKNOWN_ERROR",
                title="Unrecognized database error",                 description=(
                     "The supplied error text did not match any known database "
                     "error pattern."
                 ),
                severity=Severity.medium,
                evidence=[redact_text(text[:300]) if redact else text[:300]],
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Provide the full error message and stack trace.",
                    "Identify the database driver and version.",
                    "Check connectivity, credentials, and resource limits.",
                ],
                observed_facts=["No known database error pattern matched."],
                detected_patterns=["unknown_database_error"],
                potential_explanations=[
                    "The error may be driver-specific or truncated.",
                ],
                recommended_next_steps=[
                    "Collect complete logs and database server status.",
                ],
            )
        )
        status = "unknown"
    else:
        status = "issues_found"

    kb_refs = sorted({ref for p in _PATTERNS for ref in p["kb_refs"] if p["code"] in matched_codes})

    metadata: dict[str, Any] = {
        "matched_patterns": sorted(matched_codes),
        "pattern_count": len(matched_codes),
        "kb_refs": kb_refs,
        "related_kb_documents": kb_refs,
        "truncated": len(text) < len(str(error_text or "")),
    }

    summary = (
        f"Matched {len(matched_codes)} known database error pattern(s)."
        if matched_codes
        else "No known database error pattern matched."
    )

    observed_facts = (
        [f"{len(matched_codes)} known pattern(s) matched."]
        if matched_codes
        else ["No known pattern matched."]
    )
    potential_explanations = []
    for p in _PATTERNS:
        if p["code"] in matched_codes:
            potential_explanations.extend(p["causes"])
    recommended_next_steps = []
    for p in _PATTERNS:
        if p["code"] in matched_codes:
            recommended_next_steps.extend(p["diagnostics"])

    return DiagnosticResult(
        tool_name=TOOL_NAME,
        status=status,
        summary=summary,
        findings=findings,
        analyzed_items=1,
        warnings=warnings,
        metadata=metadata,
        observed_facts=observed_facts,
        detected_patterns=sorted(matched_codes),
        potential_explanations=potential_explanations,
        recommended_next_steps=recommended_next_steps
        or ["Collect additional diagnostic information."],
    )
