"""Typed state for the OpsPilot LangGraph investigation workflow."""

from __future__ import annotations

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    """JSON-serializable state carried by the investigation graph."""

    investigation_id: str
    thread_id: str
    request: dict[str, Any] | None
    incident_classification: dict[str, Any] | None
    available_inputs: list[str]
    investigation_plan: dict[str, Any] | None
    selected_tools: list[str]
    retrieval_results: list[dict[str, Any]]
    retrieval_metadata: dict[str, Any]
    diagnostic_results: dict[str, dict[str, Any]]
    tool_errors: list[dict[str, Any]]
    correlation_results: dict[str, Any] | None
    investigation_report: dict[str, Any] | None
    evidence_status: str
    warnings: list[str]
    approval_status: str
    approval_decision: dict[str, Any] | None
    execution_status: str
    error_details: list[str]
    planner_used: bool
    fallback_used: bool
    missing_information: list[str]
    plan_rationale: str
    max_steps: int


DEFAULT_STATE: dict[str, Any] = {
    "investigation_id": "",
    "thread_id": "",
    "request": None,
    "incident_classification": None,
    "available_inputs": [],
    "investigation_plan": None,
    "selected_tools": [],
    "retrieval_results": [],
    "retrieval_metadata": {},
    "diagnostic_results": {},
    "tool_errors": [],
    "correlation_results": None,
    "investigation_report": None,
    "evidence_status": "unknown",
    "warnings": [],
    "approval_status": "not_requested",
    "approval_decision": None,
    "execution_status": "pending",
    "error_details": [],
    "planner_used": False,
    "fallback_used": False,
    "missing_information": [],
    "plan_rationale": "",
    "max_steps": 25,
}
