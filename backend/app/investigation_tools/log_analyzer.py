"""Deterministic log analysis tool.

The analyzer parses untrusted log text line by line. It does not execute,
evaluate, or follow instructions found inside logs, and it never performs
network or filesystem access.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any

from .models import DiagnosticFinding, DiagnosticResult, InputSizeError, Severity
from .sanitization import redact_text, split_lines_bounded, truncate_text

TOOL_NAME = "log_analyzer"

# Level tokens are only accepted near the start of a line (or in a JSON field) so
# that the word "error" inside a message does not change the entry's level.
_LEVEL_RE = re.compile(
    r"\b(CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG|TRACE)\b",
    re.IGNORECASE,
)
_LEVEL_ALIASES = {
    "CRITICAL": "CRITICAL",
    "FATAL": "CRITICAL",
    "ERROR": "ERROR",
    "ERR": "ERROR",
    "WARNING": "WARNING",
    "WARN": "WARNING",
    "INFO": "INFO",
    "DEBUG": "DEBUG",
    "TRACE": "TRACE",
}
_LEVEL_PREFIX_CHARS = 80

_TIMESTAMP_PATTERNS = (
    re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?"),
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"),
)

# HTTP status codes are only extracted in contexts that make them meaningful,
# to avoid matching quantities such as "500 ms".
_HTTP_CONTEXT_RES = (
    re.compile(r"HTTP/\d(?:\.\d)?\"?\s+(\d{3})\b"),
    re.compile(r"\bstatus(?:_code|\s+code)?\s*[=:]\s*(\d{3})\b", re.IGNORECASE),
    re.compile(r"\"\s+(\d{3})\s+[A-Z]"),
    re.compile(r"\breturned\s+(\d{3})\b", re.IGNORECASE),
    re.compile(
        r"\b(4\d{2}|5\d{2})\s+(?:Bad Request|Unauthorized|Forbidden|Not Found|"
        r"Request Timeout|Conflict|Unprocessable|Too Many Requests|"
        r"Internal Server Error|Bad Gateway|Service Unavailable|"
        r"Gateway Timeout)\b"
    ),
)
_HTTP_CODE_RE = re.compile(r"\b([45]\d{2})\b")

_EXCEPTION_RE = re.compile(r"\b([A-Za-z_][\w.]*(?:Error|Exception|Warning|Fault|Panic))\b")
_CORR_KEYS_RE = re.compile(
    r"(?i)(?:request[_-]?id|requestid|correlation[_-]?id|correlationid|"
    r"trace[_-]?id|traceid|req[_-]?id|reqid|x-request-id)\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9_\-.]+)"
)
_LOGGER_AFTER_LEVEL_RE = re.compile(
    r"\b(?:CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG|TRACE)\b[:\s]+"
    r"([A-Za-z_][\w.\-]*)[\s:\-]",
    re.IGNORECASE,
)

_LEVEL_JSON_KEYS = ("level", "severity", "log_level", "lvl", "levelname")
_TIMESTAMP_JSON_KEYS = ("timestamp", "time", "@timestamp", "ts", "datetime")
_LOGGER_JSON_KEYS = ("logger", "logger_name", "name", "module")
_MESSAGE_JSON_KEYS = ("message", "msg", "event", "text")
_CORR_JSON_FRAGMENTS = (
    "request_id",
    "requestid",
    "correlation_id",
    "correlationid",
    "trace_id",
    "traceid",
    "req_id",
    "reqid",
)

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
)


def _parse_timestamp(value: str) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().rstrip(",")
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text.rstrip("Z"), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_level(value: Any) -> str | None:
    if value is None:
        return None
    return _LEVEL_ALIASES.get(str(value).strip().upper())


def _detect_level(line: str, parsed_json: dict[str, Any] | None) -> str:
    if parsed_json:
        for key in _LEVEL_JSON_KEYS:
            level = _normalize_level(parsed_json.get(key))
            if level:
                return level
    prefix = line[:_LEVEL_PREFIX_CHARS]
    match = _LEVEL_RE.search(prefix)
    if match:
        return _normalize_level(match.group(1)) or "UNKNOWN"
    # ``level=ERROR`` style anywhere on the line.
    explicit = re.search(r"\blevel\s*[=:]\s*\"?([A-Za-z]+)", line, re.IGNORECASE)
    if explicit:
        return _normalize_level(explicit.group(1)) or "UNKNOWN"
    return "UNKNOWN"


def _extract_timestamp(line: str, parsed_json: dict[str, Any] | None) -> str | None:
    if parsed_json:
        for key in _TIMESTAMP_JSON_KEYS:
            value = parsed_json.get(key)
            if value:
                return str(value)
    for regex in _TIMESTAMP_PATTERNS:
        match = regex.search(line)
        if match:
            return match.group(0)
    return None


def _extract_logger(line: str, parsed_json: dict[str, Any] | None) -> str | None:
    if parsed_json:
        for key in _LOGGER_JSON_KEYS:
            value = parsed_json.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    match = _LOGGER_AFTER_LEVEL_RE.search(line)
    if match:
        candidate = match.group(1)
        if not candidate.isdigit() and candidate.upper() not in _LEVEL_ALIASES:
            return candidate
    return None


def _try_json(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _extract_statuses(line: str, parsed_json: dict[str, Any] | None) -> list[int]:
    codes: list[int] = []
    for regex in _HTTP_CONTEXT_RES:
        for match in regex.finditer(line):
            codes.append(int(match.group(1)))
    if parsed_json:
        for key, value in parsed_json.items():
            lowered = str(key).lower()
            if "status" not in lowered:
                continue
            try:
                code = int(value)
            except (TypeError, ValueError):
                continue
            if 100 <= code <= 599:
                codes.append(code)
    # Fall back to a bare 4xx/5xx code only when the line looks like a request log.
    if not codes and ("GET " in line or "POST " in line or "PUT " in line or "DELETE " in line):
        for match in _HTTP_CODE_RE.finditer(line):
            codes.append(int(match.group(1)))
    return sorted(set(codes))


def _extract_correlation_ids(line: str, parsed_json: dict[str, Any] | None) -> list[str]:
    ids = _CORR_KEYS_RE.findall(line)
    if parsed_json:
        for key, value in parsed_json.items():
            if any(fragment in str(key).lower() for fragment in _CORR_JSON_FRAGMENTS):
                if value is not None and str(value).strip():
                    ids.append(str(value))
    return sorted(set(ids))


def _extract_exceptions(line: str, parsed_json: dict[str, Any] | None) -> list[str]:
    names = _EXCEPTION_RE.findall(line)
    if parsed_json:
        for value in parsed_json.values():
            if isinstance(value, str):
                names.extend(_EXCEPTION_RE.findall(value))
    return sorted(set(names))


def analyze_logs(log_text: str, **options: Any) -> DiagnosticResult:
    """Analyze untrusted log text and return structured findings.

    Options (all optional):
        max_input_chars / max_chars: character ceiling (default 200_000).
        max_lines / max_input_lines: line ceiling (default 10_000).
        redact / redact_secrets: redact secrets in returned evidence (default True).
        truncate: allow truncation instead of raising (default True).
    """
    max_input_chars = int(options.get("max_input_chars", options.get("max_chars", 200_000)))
    max_lines = int(options.get("max_lines", options.get("max_input_lines", 10_000)))
    redact = bool(options.get("redact", options.get("redact_secrets", True)))
    allow_truncate = bool(options.get("truncate", True))

    warnings: list[str] = []
    text = "" if log_text is None else str(log_text)

    if len(text) > max_input_chars:
        if not allow_truncate:
            raise InputSizeError(f"input exceeds max_input_chars ({len(text)} > {max_input_chars})")
        text, _ = truncate_text(text, max_input_chars)
        warnings.append(f"Input truncated to {max_input_chars} characters.")

    lines, lines_truncated = split_lines_bounded(text, max_lines)
    if lines_truncated:
        warnings.append(f"Input truncated to {max_lines} lines.")

    entries: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        parsed = _try_json(raw_line)
        entries.append(
            {
                "line_number": line_number,
                "level": _detect_level(raw_line, parsed),
                "timestamp": _extract_timestamp(raw_line, parsed),
                "logger": _extract_logger(raw_line, parsed),
                "exceptions": _extract_exceptions(raw_line, parsed),
                "http_statuses": _extract_statuses(raw_line, parsed),
                "correlation_ids": _extract_correlation_ids(raw_line, parsed),
                "raw": raw_line,
            }
        )

    truncated = bool(warnings)

    if not entries:
        return DiagnosticResult(
            tool_name=TOOL_NAME,
            status="empty",
            summary="No log entries were found in the supplied input.",
            findings=[],
            analyzed_items=0,
            warnings=warnings,
            metadata={"level_counts": {}, "truncated": truncated},
        )

    level_counts: Counter[str] = Counter(entry["level"] for entry in entries)

    message_counter: Counter[str] = Counter()
    for entry in entries:
        normalized = entry["raw"]
        for regex in _TIMESTAMP_PATTERNS:
            normalized = regex.sub("", normalized)
        message_counter[re.sub(r"\s+", " ", normalized.strip().lower())] += 1
    repeated = {message: count for message, count in message_counter.items() if count > 1}

    error_times = [
        parsed
        for entry in entries
        if entry["level"] in {"ERROR", "CRITICAL"}
        for parsed in [_parse_timestamp(entry["timestamp"])]
        if parsed is not None
    ]

    exception_counter: Counter[str] = Counter(
        name for entry in entries for name in entry["exceptions"]
    )
    status_counter: Counter[int] = Counter(
        code for entry in entries for code in entry["http_statuses"]
    )
    correlation_counter: Counter[str] = Counter(
        cid for entry in entries for cid in entry["correlation_ids"]
    )
    logger_names = sorted({entry["logger"] for entry in entries if entry["logger"]})

    def evidence_for(level: str, limit: int) -> list[str]:
        return [
            redact_text(entry["raw"]) if redact else entry["raw"]
            for entry in entries
            if entry["level"] == level
        ][:limit]

    findings: list[DiagnosticFinding] = []
    critical_count = level_counts.get("CRITICAL", 0)
    error_count = level_counts.get("ERROR", 0)
    warning_count = level_counts.get("WARNING", 0)

    if critical_count:
        findings.append(
            DiagnosticFinding(
                code="LOG_CRITICAL_ENTRIES",
                title="Critical or fatal log entries detected",
                description=f"Found {critical_count} CRITICAL/FATAL log entries.",
                severity=Severity.critical,
                evidence=evidence_for("CRITICAL", 5),
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Inspect the critical entries and their associated stack traces.",
                    "Correlate with deployment and configuration changes.",
                ],
                observed_facts=[f"{critical_count} critical/fatal entries observed."],
                detected_patterns=["critical_log_entries"],
            )
        )

    if error_count:
        findings.append(
            DiagnosticFinding(
                code="LOG_ERROR_ENTRIES",
                title="Error log entries detected",
                description=f"Found {error_count} ERROR log entries.",
                severity=Severity.high,
                evidence=evidence_for("ERROR", 5),
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Review the error messages and associated exceptions.",
                    "Group by repeated messages to identify the dominant failure.",
                ],
                observed_facts=[f"{error_count} error entries observed."],
                detected_patterns=["error_log_entries"],
            )
        )

    if warning_count:
        findings.append(
            DiagnosticFinding(
                code="LOG_WARNING_ENTRIES",
                title="Warning log entries detected",
                description=f"Found {warning_count} WARNING log entries.",
                severity=Severity.medium,
                evidence=evidence_for("WARNING", 3),
                source=TOOL_NAME,
                remediation_suggestions=["Review warnings for early signs of failure."],
                observed_facts=[f"{warning_count} warning entries observed."],
                detected_patterns=["warning_log_entries"],
            )
        )

    if repeated:
        top = sorted(repeated.items(), key=lambda item: (-item[1], item[0]))[:5]
        findings.append(
            DiagnosticFinding(
                code="LOG_REPEATED_MESSAGES",
                title="Repeated log messages detected",
                description=f"Found {len(repeated)} repeated message pattern(s).",
                severity=Severity.medium,
                evidence=[
                    f"{count}x: {(redact_text(message[:200]) if redact else message[:200])}"
                    for message, count in top
                ],
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Investigate the most frequent repeated message first.",
                ],
                observed_facts=[
                    f"{sum(repeated.values())} total occurrences across repeated patterns."
                ],
                detected_patterns=["repeated_log_messages"],
            )
        )

    if exception_counter:
        findings.append(
            DiagnosticFinding(
                code="LOG_EXCEPTION_NAMES",
                title="Exception names detected in logs",
                description="Python exception types referenced in log entries.",
                severity=Severity.high,
                evidence=[f"{name} ({count})" for name, count in exception_counter.most_common(10)],
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Inspect the stack traces associated with these exceptions."
                ],
                observed_facts=[f"{len(exception_counter)} distinct exception type(s) observed."],
                detected_patterns=["exception_names_in_logs"],
            )
        )

    for codes, finding_code, label, severity in (
        ([code for code in status_counter if code >= 500], "LOG_HTTP_5XX", "5xx", Severity.high),
        (
            [code for code in status_counter if 400 <= code < 500],
            "LOG_HTTP_4XX",
            "4xx",
            Severity.medium,
        ),
    ):
        if not codes:
            continue
        findings.append(
            DiagnosticFinding(
                code=finding_code,
                title=f"HTTP {label} status codes in logs",
                description=(
                    f"HTTP {label} statuses observed: "
                    f"{dict(sorted((code, status_counter[code]) for code in codes))}."
                ),
                severity=severity,
                evidence=[f"{code} ({status_counter[code]})" for code in sorted(codes)],
                source=TOOL_NAME,
                remediation_suggestions=["Inspect the requests that produced these statuses."],
                observed_facts=[f"{len(codes)} distinct HTTP {label} status code(s)."],
                detected_patterns=[f"http_{label}_in_logs"],
            )
        )

    if correlation_counter:
        findings.append(
            DiagnosticFinding(
                code="LOG_CORRELATION_IDS",
                title="Correlation/request IDs detected",
                description=(
                    f"Found {len(correlation_counter)} distinct correlation or request ID(s)."
                ),
                severity=Severity.informational,
                evidence=sorted(correlation_counter)[:10],
                source=TOOL_NAME,
                remediation_suggestions=["Use these IDs to correlate events across components."],
                observed_facts=[f"{sum(correlation_counter.values())} total ID occurrence(s)."],
                detected_patterns=["correlation_ids_in_logs"],
            )
        )

    has_server_errors = any(code >= 500 for code in status_counter)
    if critical_count or error_count or has_server_errors:
        status = "issues_found"
    elif warning_count or any(400 <= code < 500 for code in status_counter):
        status = "warnings_found"
    else:
        status = "ok"

    summary_parts = [f"Analyzed {len(entries)} log entries."]
    if critical_count:
        summary_parts.append(f"{critical_count} critical.")
    if error_count:
        summary_parts.append(f"{error_count} errors.")
    if warning_count:
        summary_parts.append(f"{warning_count} warnings.")
    if repeated:
        summary_parts.append(f"{len(repeated)} repeated patterns.")

    potential_explanations: list[str] = []
    if repeated:
        potential_explanations.append(
            "Repeated messages may indicate a persistent underlying failure."
        )
    if status_counter.get(500):
        potential_explanations.append("HTTP 500 entries suggest unhandled server-side exceptions.")
    if status_counter.get(429):
        potential_explanations.append(
            "HTTP 429 entries suggest request throttling or a client retry storm."
        )

    observed_facts = [f"{len(entries)} log entries analyzed."]
    if error_times:
        observed_facts.append(
            f"Errors observed between {min(error_times).isoformat()} and "
            f"{max(error_times).isoformat()}."
        )

    metadata: dict[str, Any] = {
        "level_counts": dict(sorted(level_counts.items())),
        "total_lines_processed": len(lines),
        "total_entries": len(entries),
        "logger_names": logger_names,
        "exception_counts": dict(sorted(exception_counter.items())),
        "http_status_counts": {str(code): count for code, count in sorted(status_counter.items())},
        "repeated_message_count": len(repeated),
        "correlation_ids": sorted(correlation_counter),
        "earliest_error_timestamp": min(error_times).isoformat() if error_times else None,
        "latest_error_timestamp": max(error_times).isoformat() if error_times else None,
        "truncated": truncated,
    }

    return DiagnosticResult(
        tool_name=TOOL_NAME,
        status=status,
        summary=" ".join(summary_parts),
        findings=findings,
        analyzed_items=len(entries),
        warnings=warnings,
        metadata=metadata,
        observed_facts=observed_facts,
        detected_patterns=sorted(
            {pattern for finding in findings for pattern in finding.detected_patterns}
        ),
        potential_explanations=potential_explanations,
        recommended_next_steps=[
            "Review the highest-severity findings first.",
            "Correlate findings with deployment and configuration changes.",
        ],
    )
