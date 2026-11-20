"""Fixed registry for OpsPilot diagnostic tools."""

from __future__ import annotations

from typing import Any, Callable

from backend.app.investigation_tools import (
    analyze_logs,
    analyze_stack_trace,
    classify_http_error,
    correlate_incidents,
    diagnose_database_error,
    validate_configuration,
)

TOOL_NAMES = [
    "log_analyzer",
    "stack_trace_analyzer",
    "http_error_classifier",
    "config_validator",
    "database_diagnostics",
    "incident_correlator",
]

REQUIRED_INPUTS: dict[str, list[str]] = {
    "log_analyzer": ["logs"],
    "stack_trace_analyzer": ["stack_trace"],
    "http_error_classifier": ["http_status"],
    "config_validator": ["configuration"],
    "database_diagnostics": ["database_error"],
    "incident_correlator": ["events"],
}

MAX_TOOL_INVOCATIONS = 6


def _as_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            try:
                return value.model_dump()
            except Exception:
                pass
    return value


def build_tool_inputs(tool_name: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """Build validated inputs for a tool from graph state."""
    request = state.get("request") or {}
    if tool_name == "log_analyzer":
        text = request.get("logs")
        if not text:
            return None
        return {"log_text": text}
    if tool_name == "stack_trace_analyzer":
        text = request.get("stack_trace")
        if not text:
            return None
        return {"trace_text": text}
    if tool_name == "http_error_classifier":
        status = request.get("http_status")
        if status is None:
            return None
        kwargs: dict[str, Any] = {"status_code": int(status)}
        if request.get("response_text") is not None:
            kwargs["response_text"] = request.get("response_text")
        if request.get("response_headers") is not None:
            kwargs["headers"] = request.get("response_headers")
        return kwargs
    if tool_name == "config_validator":
        config = request.get("configuration")
        if config is None:
            return None
        kwargs = {"config": config}
        if request.get("configuration_rules") is not None:
            kwargs["rules"] = request.get("configuration_rules")
        return kwargs
    if tool_name == "database_diagnostics":
        text = request.get("database_error")
        if not text:
            return None
        return {"error_text": text}
    if tool_name == "incident_correlator":
        events = request.get("events")
        if not events:
            return None
        return {"events": events}
    return None


def get_tool_callable(tool_name: str) -> Callable[..., Any] | None:
    mapping = {
        "log_analyzer": analyze_logs,
        "stack_trace_analyzer": analyze_stack_trace,
        "http_error_classifier": classify_http_error,
        "config_validator": validate_configuration,
        "database_diagnostics": diagnose_database_error,
        "incident_correlator": correlate_incidents,
    }
    return mapping.get(tool_name)


def execute_tool(tool_name: str, state: dict[str, Any]) -> dict[str, Any]:
    """Execute one fixed diagnostic tool and return a serializable result."""
    func = get_tool_callable(tool_name)
    if func is None:
        raise ValueError(f"unknown tool: {tool_name}")
    inputs = build_tool_inputs(tool_name, state)
    if inputs is None:
        raise ValueError(f"missing required input for {tool_name}")
    result = func(**inputs)
    return _as_dict(result) if not isinstance(result, dict) else result
