"""OpsPilot deterministic investigation tools.

This package contains independent, offline, deterministic diagnostic tools.
No LangGraph, LLM, database, or network access is used here.
"""

from __future__ import annotations

from .config_validator import validate_configuration
from .database_diagnostics import diagnose_database_error
from .http_error_classifier import classify_http_error
from .incident_correlator import correlate_incidents
from .log_analyzer import analyze_logs
from .models import (
    CorrelationGroup,
    CorrelationResult,
    DiagnosticFinding,
    DiagnosticResult,
    InputSizeError,
    InputValidationError,
    InvestigationToolError,
    Severity,
)
from .sanitization import (
    is_sensitive_key,
    redact_mapping,
    redact_text,
    redact_value,
    split_lines_bounded,
    truncate_text,
)
from .stack_trace_analyzer import analyze_stack_trace

__all__ = [
    "analyze_logs",
    "analyze_stack_trace",
    "classify_http_error",
    "validate_configuration",
    "diagnose_database_error",
    "correlate_incidents",
    "DiagnosticFinding",
    "DiagnosticResult",
    "CorrelationGroup",
    "CorrelationResult",
    "Severity",
    "InvestigationToolError",
    "InputValidationError",
    "InputSizeError",
    "redact_text",
    "redact_value",
    "redact_mapping",
    "is_sensitive_key",
    "truncate_text",
    "split_lines_bounded",
]

# Convenience aliases for future LangGraph wrapping.
analyze_log = analyze_logs
analyze_stacktrace = analyze_stack_trace
classify_http = classify_http_error
validate_config = validate_configuration
diagnose_db_error = diagnose_database_error
correlate = correlate_incidents
