"""MCP tool adapters.

These functions delegate to existing OpsPilot implementations. They do not
contain diagnostic algorithms, retrieval algorithms, or incident business
rules.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..incidents import IncidentService
from ..incidents.exceptions import (
    IncidentError,
    IncidentNotFoundError,
    InvalidInputError,
    InvalidStateTransitionError,
    InvestigationNotFoundError,
    TicketNotFoundError,
)
from ..investigation_tools import (
    analyze_logs as canonical_analyze_logs,
)
from ..investigation_tools import (
    analyze_stack_trace as canonical_analyze_stack_trace,
)
from ..investigation_tools import (
    classify_http_error as canonical_classify_http_error,
)
from ..investigation_tools import (
    correlate_incidents as canonical_correlate_incidents,
)
from ..investigation_tools import (
    diagnose_database_error as canonical_diagnose_database_error,
)
from ..investigation_tools import (
    validate_configuration as canonical_validate_configuration,
)
from ..retrieval import ALLOWED_FILTER_FIELDS
from .dependencies import MCPDependencies
from .exceptions import MCPToolError

# Hard bounds for MCP-exposed inputs.
_MAX_QUERY_CHARS = 2000
_MAX_TOP_K = 20
_MAX_LOG_CHARS = 200_000
_MAX_LOG_LINES = 10_000
_MAX_CONFIG_CHARS = 100_000
_MAX_ERROR_CHARS = 200_000
_MAX_TRACE_CHARS = 200_000
_MAX_TEXT_FIELD_CHARS = 5000
_MAX_ID_CHARS = 200
_MAX_LIMIT = 100
_MAX_OFFSET = 100_000


def _clamp_int(value: Any, *, minimum: int, maximum: int, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MCPToolError(f"{name} must be an integer") from exc
    if result < minimum:
        raise MCPToolError(f"{name} must be at least {minimum}")
    if result > maximum:
        raise MCPToolError(f"{name} must be at most {maximum}")
    return result


def _require_text(value: Any, *, name: str, max_chars: int) -> str:
    if value is None:
        raise MCPToolError(f"{name} is required")
    text = str(value)
    if not text.strip():
        raise MCPToolError(f"{name} must not be empty")
    if len(text) > max_chars:
        raise MCPToolError(f"{name} exceeds {max_chars} characters")
    return text


def _require_id(value: Any, *, name: str) -> str:
    text = _require_text(value, name=name, max_chars=_MAX_ID_CHARS)
    return text.strip()


def _incident_to_dict(incident: Any) -> dict[str, Any]:
    if hasattr(incident, "model_dump"):
        return incident.model_dump(mode="json")
    return dict(incident)


def _ticket_to_dict(ticket: Any) -> dict[str, Any]:
    if hasattr(ticket, "model_dump"):
        return ticket.model_dump(mode="json")
    return dict(ticket)


def _investigation_to_dict(investigation: Any) -> dict[str, Any]:
    if hasattr(investigation, "model_dump"):
        return investigation.model_dump(mode="json")
    return dict(investigation)


def _evidence_to_dict(evidence: Any) -> dict[str, Any]:
    if hasattr(evidence, "model_dump"):
        return evidence.model_dump(mode="json")
    return dict(evidence)


def _approval_to_dict(approval: Any) -> dict[str, Any]:
    if hasattr(approval, "model_dump"):
        return approval.model_dump(mode="json")
    return dict(approval)


def _resolution_to_dict(resolution: Any) -> dict[str, Any]:
    if hasattr(resolution, "model_dump"):
        return resolution.model_dump(mode="json")
    return dict(resolution)


def _search_result_to_dict(result: Any) -> dict[str, Any]:
    data = {
        "chunk_id": getattr(result, "chunk_id", None),
        "parent_chunk_id": getattr(result, "parent_chunk_id", None),
        "doc_id": getattr(result, "doc_id", None),
        "title": getattr(result, "title", None),
        "category": getattr(result, "category", None),
        "component": getattr(result, "component", None),
        "severity": getattr(result, "severity", None),
        "source_path": getattr(result, "source_path", None),
        "chunk_text": getattr(result, "chunk_text", None),
        "cosine_distance": getattr(result, "cosine_distance", None),
        "semantic_similarity": getattr(result, "semantic_similarity", None),
        "semantic_rank": getattr(result, "semantic_rank", None),
        "bm25_score": getattr(result, "bm25_score", None),
        "bm25_rank": getattr(result, "bm25_rank", None),
        "rrf_score": getattr(result, "rrf_score", None),
        "reranker_score": getattr(result, "reranker_score", None),
        "final_rank": getattr(result, "final_rank", None),
        "tokenizer_name": getattr(result, "tokenizer_name", None),
        "tokenizer_revision": getattr(result, "tokenizer_revision", None),
        "token_count": getattr(result, "token_count", None),
        "source_char_start": getattr(result, "source_char_start", None),
        "source_char_end": getattr(result, "source_char_end", None),
        "metadata": getattr(result, "metadata", None),
    }
    return data


def _diagnostic_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return json.loads(json.dumps(result, default=str))


def _handle_incident_error(exc: Exception) -> None:
    if isinstance(exc, (IncidentNotFoundError, TicketNotFoundError, InvestigationNotFoundError)):
        raise MCPToolError(str(exc)) from exc
    if isinstance(exc, InvalidInputError):
        raise MCPToolError(f"invalid input: {exc}") from exc
    if isinstance(exc, InvalidStateTransitionError):
        raise MCPToolError(f"invalid state transition: {exc}") from exc
    if isinstance(exc, IncidentError):
        raise MCPToolError(f"incident operation failed: {exc}") from exc
    raise MCPToolError("operation failed") from exc


def register_tools(server: MCPServer, deps: MCPDependencies) -> None:
    """Register all controlled MCP tools on ``server``."""

    service: IncidentService = deps.incident_service

    @server.tool(
        name="search_knowledge_base",
        description=(
            "Search the OpsPilot technical knowledge base using hybrid retrieval. "
            "Returns ranked chunks with provenance and metadata. Read-only."
        ),
    )
    def search_knowledge_base(
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_text = _require_text(query, name="query", max_chars=_MAX_QUERY_CHARS)
        bounded_top_k = _clamp_int(top_k, minimum=1, maximum=_MAX_TOP_K, name="top_k")
        validated_filters: dict[str, Any] | None = None
        if filters is not None:
            if not isinstance(filters, dict):
                raise MCPToolError("filters must be an object")
            validated_filters = {}
            for key, value in filters.items():
                if key not in ALLOWED_FILTER_FIELDS:
                    raise MCPToolError(f"unsupported filter field: {key!r}")
                validated_filters[key] = value
        try:
            engine = deps.get_search_engine()
            results = engine.search(query_text, top_k=bounded_top_k, filters=validated_filters)
        except Exception as exc:  # pragma: no cover - defensive
            raise MCPToolError("knowledge base search failed") from exc
        return {
            "query": query_text,
            "top_k": bounded_top_k,
            "result_count": len(results),
            "results": [_search_result_to_dict(item) for item in results],
        }

    @server.tool(
        name="analyze_logs",
        description=(
            "Analyze untrusted log text and return structured diagnostic findings. "
            "Secrets are redacted by default."
        ),
    )
    def analyze_logs(
        log_text: str,
        max_input_chars: int = _MAX_LOG_CHARS,
        max_lines: int = _MAX_LOG_LINES,
        redact_secrets: bool = True,
        truncate: bool = True,
    ) -> dict[str, Any]:
        text = _require_text(log_text, name="log_text", max_chars=_MAX_LOG_CHARS)
        bounded_chars = _clamp_int(
            max_input_chars, minimum=1, maximum=_MAX_LOG_CHARS, name="max_input_chars"
        )
        bounded_lines = _clamp_int(max_lines, minimum=1, maximum=_MAX_LOG_LINES, name="max_lines")
        try:
            result = canonical_analyze_logs(
                text,
                max_input_chars=bounded_chars,
                max_lines=bounded_lines,
                redact=redact_secrets,
                truncate=truncate,
            )
        except Exception as exc:
            raise MCPToolError(f"log analysis failed: {exc}") from exc
        return _diagnostic_to_dict(result)

    @server.tool(
        name="analyze_stack_trace",
        description="Analyze Python stack trace text and return structured findings.",
    )
    def analyze_stack_trace(
        trace_text: str,
        max_input_chars: int = _MAX_TRACE_CHARS,
        max_lines: int = _MAX_LOG_LINES,
        redact_secrets: bool = True,
        truncate: bool = True,
    ) -> dict[str, Any]:
        text = _require_text(trace_text, name="trace_text", max_chars=_MAX_TRACE_CHARS)
        bounded_chars = _clamp_int(
            max_input_chars, minimum=1, maximum=_MAX_TRACE_CHARS, name="max_input_chars"
        )
        bounded_lines = _clamp_int(max_lines, minimum=1, maximum=_MAX_LOG_LINES, name="max_lines")
        try:
            result = canonical_analyze_stack_trace(
                text,
                max_input_chars=bounded_chars,
                max_lines=bounded_lines,
                redact=redact_secrets,
                truncate=truncate,
            )
        except Exception as exc:
            raise MCPToolError(f"stack trace analysis failed: {exc}") from exc
        return _diagnostic_to_dict(result)

    @server.tool(
        name="classify_http_error",
        description="Classify an HTTP status code and optional response details.",
    )
    def classify_http_error(
        status_code: int,
        response_text: str | None = None,
        headers: dict[str, Any] | None = None,
        retry_after: Any = None,
    ) -> dict[str, Any]:
        try:
            code = int(status_code)
        except (TypeError, ValueError) as exc:
            raise MCPToolError("status_code must be an integer") from exc
        if code < 100 or code > 599:
            raise MCPToolError("status_code must be between 100 and 599")
        options: dict[str, Any] = {}
        if response_text is not None:
            options["response_text"] = _require_text(
                response_text, name="response_text", max_chars=_MAX_TEXT_FIELD_CHARS
            )
        if headers is not None:
            if not isinstance(headers, dict):
                raise MCPToolError("headers must be an object")
            options["headers"] = headers
        if retry_after is not None:
            options["retry_after"] = retry_after
        try:
            result = canonical_classify_http_error(code, **options)
        except Exception as exc:
            raise MCPToolError(f"http classification failed: {exc}") from exc
        return _diagnostic_to_dict(result)

    @server.tool(
        name="validate_configuration",
        description="Validate a configuration mapping against rules or presets.",
    )
    def validate_configuration(
        config: dict[str, Any],
        rules: dict[str, Any] | None = None,
        required_fields: list[str] | None = None,
        preset: str | None = None,
        environment: str | None = None,
        redact_secrets: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise MCPToolError("config must be an object")
        try:
            serialized = json.dumps(config, default=str)
        except (TypeError, ValueError) as exc:
            raise MCPToolError("config is not serializable") from exc
        if len(serialized) > _MAX_CONFIG_CHARS:
            raise MCPToolError(f"config exceeds {_MAX_CONFIG_CHARS} characters")
        options: dict[str, Any] = {
            "redact": redact_secrets,
        }
        if rules is not None:
            options["rules"] = rules
        if required_fields is not None:
            options["required_fields"] = required_fields
        if preset is not None:
            options["preset"] = preset
        if environment is not None:
            options["environment"] = environment
        try:
            result = canonical_validate_configuration(config, **options)
        except Exception as exc:
            raise MCPToolError(f"configuration validation failed: {exc}") from exc
        return _diagnostic_to_dict(result)

    @server.tool(
        name="diagnose_database_error",
        description="Diagnose database error text against known patterns.",
    )
    def diagnose_database_error(
        error_text: str,
        max_input_chars: int = _MAX_ERROR_CHARS,
        redact_secrets: bool = True,
        truncate: bool = True,
    ) -> dict[str, Any]:
        text = _require_text(error_text, name="error_text", max_chars=_MAX_ERROR_CHARS)
        bounded_chars = _clamp_int(
            max_input_chars, minimum=1, maximum=_MAX_ERROR_CHARS, name="max_input_chars"
        )
        try:
            result = canonical_diagnose_database_error(
                text,
                max_input_chars=bounded_chars,
                redact=redact_secrets,
                truncate=truncate,
            )
        except Exception as exc:
            raise MCPToolError(f"database diagnosis failed: {exc}") from exc
        return _diagnostic_to_dict(result)

    @server.tool(
        name="correlate_incidents",
        description="Correlate supplied events and findings into proposed groups.",
    )
    def correlate_incidents(
        events: list[Any],
        findings: list[Any] | None = None,
        time_window_minutes: float = 5.0,
        redact_secrets: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(events, list):
            raise MCPToolError("events must be a list")
        try:
            serialized = json.dumps(events, default=str)
        except (TypeError, ValueError) as exc:
            raise MCPToolError("events are not serializable") from exc
        if len(serialized) > _MAX_CONFIG_CHARS:
            raise MCPToolError(f"events exceed {_MAX_CONFIG_CHARS} characters")
        options: dict[str, Any] = {
            "redact": redact_secrets,
            "time_window_minutes": time_window_minutes,
        }
        if findings is not None:
            if not isinstance(findings, list):
                raise MCPToolError("findings must be a list")
            options["findings"] = findings
        try:
            result = canonical_correlate_incidents(events, **options)
        except Exception as exc:
            raise MCPToolError(f"incident correlation failed: {exc}") from exc
        return _diagnostic_to_dict(result)

    @server.tool(
        name="create_incident",
        description="Create an incident through the controlled incident service.",
    )
    def create_incident(
        title: str,
        description: str | None = None,
        severity: str = "medium",
        priority: str = "medium",
        source: str | None = None,
        environment: str | None = None,
        service_name: str | None = None,
    ) -> dict[str, Any]:
        title_text = _require_text(title, name="title", max_chars=_MAX_TEXT_FIELD_CHARS)
        payload: dict[str, Any] = {
            "title": title_text,
            "severity": severity,
            "priority": priority,
        }
        if description is not None:
            payload["description"] = _require_text(
                description, name="description", max_chars=_MAX_TEXT_FIELD_CHARS
            )
        if source is not None:
            payload["source"] = _require_text(
                source, name="source", max_chars=_MAX_TEXT_FIELD_CHARS
            )
        if environment is not None:
            payload["environment"] = _require_text(
                environment, name="environment", max_chars=_MAX_TEXT_FIELD_CHARS
            )
        if service_name is not None:
            payload["service_name"] = _require_text(
                service_name, name="service_name", max_chars=_MAX_TEXT_FIELD_CHARS
            )
        try:
            incident = service.create_incident(payload)
        except Exception as exc:
            _handle_incident_error(exc)
        return _incident_to_dict(incident)

    @server.tool(
        name="get_incident",
        description="Retrieve an incident by ID or incident number.",
    )
    def get_incident(incident_id: str) -> dict[str, Any]:
        value = _require_id(incident_id, name="incident_id")
        try:
            incident = service.get_incident(value)
        except IncidentNotFoundError:
            try:
                incident = service.get_incident_by_number(value)
            except IncidentNotFoundError as exc:
                raise MCPToolError(str(exc)) from exc
        except Exception as exc:
            _handle_incident_error(exc)
        return _incident_to_dict(incident)

    @server.tool(
        name="list_incidents",
        description="List incidents with optional status and severity filters.",
    )
    def list_incidents(
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        severity: str | None = None,
    ) -> dict[str, Any]:
        bounded_limit = _clamp_int(limit, minimum=1, maximum=_MAX_LIMIT, name="limit")
        bounded_offset = _clamp_int(offset, minimum=0, maximum=_MAX_OFFSET, name="offset")
        try:
            incidents = service.list_incidents(
                limit=bounded_limit, offset=bounded_offset, status=status, severity=severity
            )
        except Exception as exc:
            _handle_incident_error(exc)
        return {
            "limit": bounded_limit,
            "offset": bounded_offset,
            "count": len(incidents),
            "incidents": [_incident_to_dict(item) for item in incidents],
        }

    @server.tool(
        name="get_incident_history",
        description=(
            "Retrieve an incident with its tickets, investigations, approvals, and resolutions."
        ),
    )
    def get_incident_history(incident_id: str) -> dict[str, Any]:
        value = _require_id(incident_id, name="incident_id")
        try:
            history = service.get_incident_history(value)
        except Exception as exc:
            _handle_incident_error(exc)
        return {
            "incident": _incident_to_dict(history["incident"]),
            "tickets": [_ticket_to_dict(item) for item in history.get("tickets", [])],
            "investigations": [
                _investigation_to_dict(item) for item in history.get("investigations", [])
            ],
            "approvals": [_approval_to_dict(item) for item in history.get("approvals", [])],
            "resolutions": [_resolution_to_dict(item) for item in history.get("resolutions", [])],
        }

    @server.tool(
        name="create_ticket",
        description="Create a ticket linked to an existing incident.",
    )
    def create_ticket(
        incident_id: str,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        assignee: str | None = None,
    ) -> dict[str, Any]:
        incident_value = _require_id(incident_id, name="incident_id")
        title_text = _require_text(title, name="title", max_chars=_MAX_TEXT_FIELD_CHARS)
        payload: dict[str, Any] = {
            "incident_id": incident_value,
            "title": title_text,
            "priority": priority,
        }
        if description is not None:
            payload["description"] = _require_text(
                description, name="description", max_chars=_MAX_TEXT_FIELD_CHARS
            )
        if assignee is not None:
            payload["assignee"] = _require_text(
                assignee, name="assignee", max_chars=_MAX_TEXT_FIELD_CHARS
            )
        try:
            ticket = service.create_ticket(payload)
        except Exception as exc:
            _handle_incident_error(exc)
        return _ticket_to_dict(ticket)

    @server.tool(
        name="get_ticket",
        description="Retrieve a ticket by ID.",
    )
    def get_ticket(ticket_id: str) -> dict[str, Any]:
        value = _require_id(ticket_id, name="ticket_id")
        try:
            ticket = service.get_ticket(value)
        except Exception as exc:
            _handle_incident_error(exc)
        return _ticket_to_dict(ticket)

    @server.tool(
        name="list_incident_tickets",
        description="List tickets for an incident.",
    )
    def list_incident_tickets(incident_id: str) -> dict[str, Any]:
        value = _require_id(incident_id, name="incident_id")
        try:
            tickets = service.list_tickets(value)
        except Exception as exc:
            _handle_incident_error(exc)
        return {
            "incident_id": value,
            "count": len(tickets),
            "tickets": [_ticket_to_dict(item) for item in tickets],
        }

    @server.tool(
        name="get_investigation",
        description="Retrieve a persisted investigation by ID.",
    )
    def get_investigation(investigation_id: str) -> dict[str, Any]:
        value = _require_id(investigation_id, name="investigation_id")
        try:
            investigation = service.get_investigation(value)
        except Exception as exc:
            _handle_incident_error(exc)
        return _investigation_to_dict(investigation)

    @server.tool(
        name="list_incident_investigations",
        description="List investigations for an incident.",
    )
    def list_incident_investigations(incident_id: str) -> dict[str, Any]:
        value = _require_id(incident_id, name="incident_id")
        try:
            investigations = service.list_investigations(value)
        except Exception as exc:
            _handle_incident_error(exc)
        return {
            "incident_id": value,
            "count": len(investigations),
            "investigations": [_investigation_to_dict(item) for item in investigations],
        }

    @server.tool(
        name="list_investigation_evidence",
        description="List evidence records for an investigation.",
    )
    def list_investigation_evidence(investigation_id: str) -> dict[str, Any]:
        value = _require_id(investigation_id, name="investigation_id")
        try:
            evidence = service.list_evidence(value)
        except Exception as exc:
            _handle_incident_error(exc)
        return {
            "investigation_id": value,
            "count": len(evidence),
            "evidence": [_evidence_to_dict(item) for item in evidence],
        }
