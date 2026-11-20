# OpsPilot Investigation Tools

## Architecture

`backend/app/investigation_tools/` is an independent, deterministic Python package.
It does not use LangGraph, LLMs, databases, or network access. Each tool is a
pure function returning a serializable Pydantic result.

## Tools

- `analyze_logs(log_text)` - log parsing and pattern detection.
- `analyze_stack_trace(trace_text)` - Python traceback analysis.
- `classify_http_error(status_code, ...)` - HTTP status classification.
- `validate_configuration(config, ...)` - configuration validation.
- `diagnose_database_error(error_text)` - database error pattern detection.
- `correlate_incidents(events, ...)` - correlation of diagnostic events.

## Shared Models

- `DiagnosticFinding`: code, title, description, severity, evidence, source,
  remediation_suggestions, plus observed facts, patterns, explanations, next steps.
- `DiagnosticResult`: tool_name, status, summary, findings, analyzed_items,
  warnings, metadata.
- `CorrelationResult`: correlation groups, findings, analyzed items, warnings.
- `CorrelationGroup`: group_id, event_ids, finding_ids, shared_evidence, timeline,
  possible dependency relationships, alternative explanations, limitations.

## Input Limits

Configurable `max_input_chars`, `max_lines`, etc. Truncation is reported in
warnings and metadata; no silent truncation.

## Redaction

Sensitive keys and inline secrets are redacted. Full secret values are never
returned. No environment variables are read automatically.

## Security Restrictions

No shell execution, no eval, no network, no filesystem access, no environment
secret access. Inputs are treated as untrusted data.

## Limitations

Heuristic results are not confirmed root causes. Correlation is not causation.
Unknown errors return informational findings requesting more data.

## LangGraph Consumption

Each function has a stable signature and returns serializable models, making
them straightforward to wrap as LangGraph tools in a later milestone.
