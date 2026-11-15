"""Node implementations for the OpsPilot investigation graph."""


from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from backend.app.agent.models import InvestigationRequest
from backend.app.agent.planner import InvestigationPlanner
from backend.app.agent.state import DEFAULT_STATE
from backend.app.agent.tool_registry import (
    MAX_TOOL_INVOCATIONS,
    TOOL_NAMES,
    build_tool_inputs,
    execute_tool,
)
from backend.app.investigation_tools import correlate_incidents
from backend.app.rag.citation_validator import CitationValidator
from backend.app.rag.models import RAGResponse

ALLOWED_TOOLS = set(TOOL_NAMES)


def _merge(state: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    base = dict(DEFAULT_STATE)
    base.update(state or {})
    base.update(updates)
    return base


def _available_inputs(request: InvestigationRequest) -> list[str]:
    available: list[str] = []
    if request.logs:
        available.append("logs")
    if request.stack_trace:
        available.append("stack_trace")
    if request.http_status is not None:
        available.append("http_status")
    if request.configuration is not None:
        available.append("configuration")
    if request.database_error:
        available.append("database_error")
    if request.events:
        available.append("events")
    return available


def _to_dict_result(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            try:
                return value.model_dump()
            except Exception:
                pass
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return dataclasses.asdict(value)
        except Exception:
            pass
    return value


def validate_request(state: dict[str, Any], **_: Any) -> dict[str, Any]:
    raw = (state or {}).get("request")
    try:
        request = InvestigationRequest.model_validate(raw or {})
    except Exception as exc:
        return _merge(
            state,
            {
                "execution_status": "failed",
                "error_details": [str(exc)],
                "warnings": list((state or {}).get("warnings", [])),
            },
        )

    investigation_id = (state or {}).get("investigation_id") or f"inv-{uuid.uuid4().hex[:12]}"
    thread_id = (state or {}).get("thread_id") or f"thread-{uuid.uuid4().hex[:12]}"
    available = _available_inputs(request)
    return _merge(
        state,
        {
            "investigation_id": investigation_id,
            "thread_id": thread_id,
            "request": request.model_dump(),
            "available_inputs": available,
            "execution_status": "validated",
            "warnings": list((state or {}).get("warnings", [])),
            "error_details": list((state or {}).get("error_details", [])),
        },
    )


def classify_incident(state: dict[str, Any], **_: Any) -> dict[str, Any]:
    request = (state or {}).get("request") or {}
    available = set((state or {}).get("available_inputs", []))
    incident_type = "unknown"
    severity = "medium"
    if "http_status" in available:
        status = request.get("http_status")
        if isinstance(status, int) and status >= 500:
            incident_type = "server_error"
            severity = "high"
        elif isinstance(status, int) and status >= 400:
            incident_type = "client_error"
            severity = "medium"
    if "database_error" in available:
        incident_type = "database_error"
        severity = "high"
    if "stack_trace" in available:
        incident_type = "application_exception"
    if "logs" in available and incident_type == "unknown":
        incident_type = "log_anomaly"
    classification = {
        "incident_type": incident_type,
        "severity": severity,
        "available_inputs": sorted(available),
    }
    return _merge(
        state,
        {
            "incident_classification": classification,
            "execution_status": "classified",
        },
    )


def retrieve_knowledge(
    state: dict[str, Any], *, retrieval_engine: Any = None, **_: Any
) -> dict[str, Any]:
    request = (state or {}).get("request") or {}
    question = request.get("question", "")
    top_k = request.get("requested_top_k") or 5
    filters = request.get("retrieval_filters")
    warnings = list((state or {}).get("warnings", []))
    error_details = list((state or {}).get("error_details", []))

    results: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {"top_k": top_k, "filters": filters, "retrieved_count": 0}
    if retrieval_engine is not None:
        try:
            raw = retrieval_engine.search(question, top_k=top_k, filters=filters)
            results = [_to_dict_result(item) for item in (raw or [])]
        except Exception as exc:
            error_details.append(f"Retrieval failed: {exc}")
    else:
        warnings.append("No retrieval engine configured; knowledge retrieval skipped.")

    metadata["retrieved_count"] = len(results)
    evidence_status = "sufficient" if results else "insufficient"
    return _merge(
        state,
        {
            "retrieval_results": results,
            "retrieval_metadata": metadata,
            "evidence_status": evidence_status,
            "warnings": warnings,
            "error_details": error_details,
            "execution_status": "retrieved",
        },
    )


def plan_investigation(
    state: dict[str, Any], *, llm_provider: Any = None, **_: Any
) -> dict[str, Any]:
    request_raw = (state or {}).get("request") or {}
    try:
        request = InvestigationRequest.model_validate(request_raw)
    except Exception as exc:
        return _merge(
            state,
            {"execution_status": "failed", "error_details": [str(exc)]},
        )

    planner = InvestigationPlanner(llm_provider=llm_provider)
    plan, used = planner.plan(
        request,
        list((state or {}).get("available_inputs", [])),
        list((state or {}).get("retrieval_results", [])),
    )
    selected = [tool for tool in plan.selected_tools if tool in ALLOWED_TOOLS]
    selected = selected[:6]
    return _merge(
        state,
        {
            "investigation_plan": plan.model_dump(),
            "selected_tools": selected,
            "planner_used": used,
            "fallback_used": plan.fallback,
            "plan_rationale": plan.rationale,
            "missing_information": plan.missing_information,
            "execution_status": "planned",
        },
    )


def execute_diagnostics(state: dict[str, Any], **_: Any) -> dict[str, Any]:
    state = state or {}
    selected = list(state.get("selected_tools", []))
    diagnostic_results = dict(state.get("diagnostic_results", {}) or {})
    tool_errors = list(state.get("tool_errors", []) or [])
    warnings = list(state.get("warnings", []) or [])

    for tool in selected[:MAX_TOOL_INVOCATIONS]:
        if tool not in ALLOWED_TOOLS:
            tool_errors.append({"tool": tool, "error": "tool not allowed"})
            continue
        inputs = build_tool_inputs(tool, state)
        if inputs is None:
            tool_errors.append({"tool": tool, "error": "missing required input"})
            continue
        try:
            result = execute_tool(tool, state)
            diagnostic_results[tool] = result
        except Exception as exc:
            tool_errors.append({"tool": tool, "error": str(exc)})

    if tool_errors:
        warnings.append("One or more diagnostic tools failed or were skipped.")

    return _merge(
        state,
        {
            "diagnostic_results": diagnostic_results,
            "tool_errors": tool_errors,
            "warnings": warnings,
            "execution_status": "diagnostics_completed",
        },
    )


def correlate_findings(state: dict[str, Any], **_: Any) -> dict[str, Any]:
    state = state or {}
    request = state.get("request") or {}
    diagnostic_results = state.get("diagnostic_results", {}) or {}
    events = request.get("events")
    warnings = list(state.get("warnings", []) or [])
    error_details = list(state.get("error_details", []) or [])

    if "incident_correlator" in diagnostic_results:
        return _merge(
            state,
            {
                "correlation_results": diagnostic_results.get("incident_correlator"),
                "execution_status": "correlation_completed",
            },
        )

    if not events:
        return _merge(
            state,
            {
                "correlation_results": None,
                "execution_status": "correlation_skipped",
            },
        )

    try:
        result = correlate_incidents(events)
        correlation = _to_dict_result(result)
        return _merge(
            state,
            {
                "correlation_results": correlation,
                "execution_status": "correlation_completed",
            },
        )
    except Exception as exc:
        error_details.append(f"Correlation failed: {exc}")
        warnings.append("Incident correlation failed.")
        return _merge(
            state,
            {
                "correlation_results": None,
                "warnings": warnings,
                "error_details": error_details,
                "execution_status": "correlation_failed",
            },
        )


def synthesize_report(
    state: dict[str, Any],
    *,
    llm_provider: Any = None,
    rag_service: Any = None,
    **_: Any,
) -> dict[str, Any]:
    state = state or {}
    request = state.get("request") or {}
    question = request.get("question", "")
    evidence = list(state.get("retrieval_results", []) or [])
    diagnostic_results = state.get("diagnostic_results", {}) or {}
    correlation_results = state.get("correlation_results")
    warnings = list(state.get("warnings", []) or [])
    error_details = list(state.get("error_details", []) or [])

    rag_response: RAGResponse | None = None
    if rag_service is not None and hasattr(rag_service, "investigate_with_evidence"):
        try:
            rag_response = rag_service.investigate_with_evidence(
                question,
                evidence,
                diagnostic_findings=diagnostic_results,
                correlation_results=correlation_results,
                filters=request.get("retrieval_filters"),
                top_k=request.get("requested_top_k"),
            )
        except Exception as exc:
            error_details.append(f"RAG synthesis failed: {exc}")
    elif llm_provider is not None:
        try:
            rag_response = _synthesize_with_provider(
                llm_provider,
                question,
                evidence,
                diagnostic_results,
                correlation_results,
            )
        except Exception as exc:
            error_details.append(f"RAG synthesis failed: {exc}")
    else:
        warnings.append("No LLM provider configured; using insufficient-evidence response.")
        rag_response = _insufficient_rag_response(question, evidence)

    if rag_response is None:
        rag_response = _insufficient_rag_response(question, evidence)

    evidence_status = state.get("evidence_status", "unknown")
    if getattr(rag_response, "retrieved_evidence", None):
        evidence_status = "sufficient"
    elif evidence_status not in {"sufficient", "insufficient"}:
        evidence_status = "insufficient"

    execution_status = "synthesized"
    if getattr(rag_response, "generation_status", "") == "insufficient_evidence":
        execution_status = "insufficient_evidence"
    if not getattr(rag_response, "valid", True):
        execution_status = "failed"

    return _merge(
        state,
        {
            "investigation_report": _to_dict_result(rag_response),
            "evidence_status": evidence_status,
            "warnings": warnings,
            "error_details": error_details,
            "execution_status": execution_status,
        },
    )


def _synthesize_with_provider(
    llm_provider: Any,
    question: str,
    evidence: list[dict[str, Any]],
    diagnostic_results: dict[str, Any],
    correlation_results: Any,
) -> RAGResponse:
    from backend.app.rag.context_builder import ContextBuilder
    from backend.app.rag.models import InvestigationReport
    from backend.app.rag.prompts import build_user_prompt

    context = ContextBuilder().build(evidence)
    evidence_items = context.items
    diagnostic_text = _format_diagnostics(diagnostic_results, correlation_results)

    if not evidence_items and not diagnostic_text:
        report = _insufficient_report(
            "No retrieved evidence or diagnostic findings were available."
        )
        return _build_rag_response(question, report, [], "insufficient_evidence", True, True, [])

    if not evidence_items:
        report = _insufficient_report("No relevant knowledge-base evidence was retrieved.")
        return _build_rag_response(question, report, [], "insufficient_evidence", True, True, [])

    user_prompt = build_user_prompt(question, context.formatted_context)
    if diagnostic_text:
        user_prompt += "\n\nDiagnostic findings (deterministic):\n" + diagnostic_text

    llm_result = llm_provider.generate_structured(
        system_instructions=_system_prompt(),
        user_input=user_prompt,
        output_schema=InvestigationReport,
        timeout=30.0,
        max_output_tokens=1800,
    )
    report = llm_result.output
    if not isinstance(report, InvestigationReport):
        report = InvestigationReport.model_validate(report)

    validation = CitationValidator().validate(report, evidence_items)
    source_references = [
        {
            "chunk_id": item.chunk_id,
            "doc_id": item.doc_id,
            "title": item.title,
            "source_path": item.source_path,
        }
        for item in evidence_items
    ]
    if not validation.valid:
        return _build_rag_response(
            question,
            report,
            evidence_items,
            "citation_validation_failed",
            False,
            False,
            validation.errors,
            source_references,
            llm_result,
        )
    return _build_rag_response(
        question,
        report,
        evidence_items,
        "success",
        True,
        True,
        [],
        source_references,
        llm_result,
    )


def _system_prompt() -> str:
    from backend.app.rag.prompts import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _format_diagnostics(diagnostic_results: dict[str, Any], correlation_results: Any) -> str:
    lines: list[str] = []
    for tool, result in (diagnostic_results or {}).items():
        if not isinstance(result, dict):
            continue
        lines.append(f"[{tool}]")
        lines.append(f"status: {result.get('status', 'unknown')}")
        for finding in (result.get("findings") or [])[:10]:
            if isinstance(finding, dict):
                lines.append(f"- {finding.get('code', '')}: {finding.get('title', '')}")
    if correlation_results and isinstance(correlation_results, dict):
        lines.append("[correlation]")
        for group in (correlation_results.get("correlation_groups") or [])[:5]:
            if isinstance(group, dict):
                lines.append(f"- {group.get('correlation_reason', '')}")
    return "\n".join(lines).strip()


def _insufficient_report(reason: str) -> Any:
    from backend.app.rag.models import InvestigationReport

    return InvestigationReport(
        summary=reason,
        incident_category="insufficient_evidence",
        affected_components=[],
        evidence_status="insufficient",
        root_cause_hypotheses=[],
        supporting_evidence=[],
        recommended_actions=[],
        missing_information=[
            "Relevant knowledge-base evidence for this incident.",
            "Logs, metrics, timestamps, and affected component details.",
        ],
        limitations=[reason],
    )


def _insufficient_rag_response(question: str, evidence: list[dict[str, Any]]) -> RAGResponse:
    report = _insufficient_report("No usable evidence was available for investigation.")
    return RAGResponse(
        question=question,
        report=report,
        retrieved_evidence=[],
        source_references=[],
        retrieval_metadata={"retrieved_count": len(evidence)},
        provider="",
        model="",
        usage=None,
        generation_status="insufficient_evidence",
        valid=True,
        citation_valid=True,
        validation_errors=[],
    )


def _build_rag_response(
    question: str,
    report: Any,
    evidence_items: list[Any],
    status: str,
    valid: bool,
    citation_valid: bool,
    errors: list[str],
    source_references: list[dict[str, Any]] | None = None,
    llm_result: Any = None,
) -> RAGResponse:
    from backend.app.rag.models import RetrievedEvidence

    retrieved = []
    for item in evidence_items or []:
        if isinstance(item, RetrievedEvidence):
            retrieved.append(item)
        else:
            retrieved.append(
                RetrievedEvidence(
                    chunk_id=str(getattr(item, "chunk_id", "")),
                    chunk_text=str(getattr(item, "chunk_text", "")),
                )
            )
    refs = source_references
    if refs is None:
        refs = [
            {
                "chunk_id": item.chunk_id,
                "doc_id": item.doc_id,
                "title": item.title,
                "source_path": item.source_path,
            }
            for item in retrieved
        ]
    usage = None
    model = ""
    if llm_result is not None:
        usage_raw = getattr(llm_result, "usage", None)
        if usage_raw is not None:
            usage = {}
            for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(usage_raw, field_name, None)
                if value is not None:
                    usage[field_name] = value
        model = getattr(llm_result, "model", "")
    return RAGResponse(
        question=question,
        report=report,
        retrieved_evidence=retrieved,
        source_references=refs,
        retrieval_metadata={"retrieved_count": len(retrieved)},
        provider="OpenAIProvider" if llm_result is not None else "",
        model=model,
        usage=usage,
        generation_status=status,
        valid=valid,
        citation_valid=citation_valid,
        validation_errors=errors,
    )


def validate_report(state: dict[str, Any], **_: Any) -> dict[str, Any]:
    state = state or {}
    report = state.get("investigation_report")
    warnings = list(state.get("warnings", []) or [])
    if not isinstance(report, dict):
        return _merge(state, {"execution_status": "failed", "warnings": warnings})
    valid = bool(report.get("valid", True))
    citation_valid = bool(report.get("citation_valid", True))
    if not valid or not citation_valid:
        warnings.append("Investigation report failed validation.")
        return _merge(
            state,
            {
                "warnings": warnings,
                "execution_status": "failed",
            },
        )
    return _merge(state, {"execution_status": "validated_report", "warnings": warnings})


def human_review(state: dict[str, Any], **_: Any) -> dict[str, Any]:
    # No side effects before interrupt.
    from langgraph.types import interrupt

    state = state or {}
    report = state.get("investigation_report") or {}
    report_obj = report.get("report", report) if isinstance(report, dict) else {}
    payload = {
        "investigation_id": state.get("investigation_id", ""),
        "incident_summary": report_obj.get("summary", "") if isinstance(report_obj, dict) else "",
        "evidence_status": state.get("evidence_status", "unknown"),
        "hypotheses": (
            report_obj.get("root_cause_hypotheses", [])
            if isinstance(report_obj, dict)
            else []
        ),
        "recommended_actions": (
            report_obj.get("recommended_actions", [])
            if isinstance(report_obj, dict)
            else []
        ),
        "supporting_evidence": (
            report_obj.get("supporting_evidence", [])
            if isinstance(report_obj, dict)
            else []
        ),
        "limitations": report_obj.get("limitations", []) if isinstance(report_obj, dict) else [],
        "approval_choices": ["approved", "rejected"],
    }
    decision = interrupt(payload)

    approval_status = "invalid"
    approval_decision: dict[str, Any] | None = None
    error_details = list(state.get("error_details", []) or [])
    if isinstance(decision, str):
        decision = {"decision": decision}
    if isinstance(decision, dict):
        value = str(decision.get("decision", "")).strip().lower()
        if value in {"approved", "rejected"}:
            approval_status = value
            approval_decision = {
                "decision": value,
                "reviewer_comment": decision.get("reviewer_comment") or decision.get("comment"),
            }
        else:
            error_details.append(f"Invalid approval decision: {value!r}")
            approval_decision = {"decision": value, "error": "invalid decision"}
    else:
        error_details.append("Invalid approval decision payload.")
        approval_decision = {"error": "invalid payload"}

    execution_status = "awaiting_approval"
    if approval_status == "approved":
        execution_status = "approved"
    elif approval_status == "rejected":
        execution_status = "rejected"
    elif approval_status == "invalid":
        execution_status = "failed"

    return _merge(
        state,
        {
            "approval_status": approval_status,
            "approval_decision": approval_decision,
            "error_details": error_details,
            "execution_status": execution_status,
        },
    )


def finalize(state: dict[str, Any], **_: Any) -> dict[str, Any]:
    state = state or {}
    approval_status = state.get("approval_status", "not_requested")
    execution_status = state.get("execution_status", "pending")
    if approval_status == "approved":
        execution_status = "completed"
    elif approval_status == "rejected":
        execution_status = "rejected"
    elif approval_status == "invalid":
        execution_status = "failed"
    return _merge(state, {"execution_status": execution_status})