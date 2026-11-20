"""Planner degradation must be observable without leaking provider output."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest

from backend.app.agent.models import InvestigationPlan
from backend.app.agent.planner import (
    PLANNER_STATUS_FALLBACK,
    PLANNER_STATUS_USED,
    classify_planner_error,
    planner_status,
)
from backend.app.incidents import IncidentService
from backend.app.incidents.database import ApplicationDatabase
from backend.app.llm.base import LLMResult, LLMUsage
from backend.app.llm.exceptions import (
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
)
from backend.app.orchestration.schemas import InvestigationStartRequest
from backend.app.orchestration.service import InvestigationOrchestrationService


class _Provider:
    model = "planner-observability"
    provider_name = "observability"

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def generate_structured(self, *, output_schema: Any, **_: Any) -> LLMResult:
        self.calls += 1
        if self.mode == "timeout":
            raise LLMTimeoutError("Gemini request timed out")
        if self.mode == "refusal":
            raise LLMRefusalError("response was blocked by safety filters")
        if self.mode == "http_503":
            raise LLMResponseError("Gemini API request failed (status 503)")
        if self.mode == "unknown_tool":
            return LLMResult(
                output={"selected_tools": ["shell_executor"], "rationale": "run a shell"},
                model=self.model,
                usage=LLMUsage(total_tokens=1),
            )
        if self.mode == "secret":
            raise LLMResponseError(
                "request failed with api_key=sk-live-supersecret and token=abc123"
            )
        return LLMResult(
            output=InvestigationPlan(selected_tools=["log_analyzer"], rationale="logs"),
            model=self.model,
            usage=LLMUsage(total_tokens=1),
        )


class _Rag:
    provider_name = "observability"
    model = "observability-rag"

    def investigate_with_evidence(self, question: str, evidence: Any, **_: Any) -> None:
        return None


def _run(mode: str, llm_provider: Any = "default") -> tuple[Any, Any, Any]:
    directory = Path(tempfile.mkdtemp(prefix="opspilot-planner-obs-"))
    service = IncidentService(db_path=str(directory / "app.sqlite"))
    orchestration = InvestigationOrchestrationService(
        incident_service=service,
        checkpoint_db_path=str(directory / "checkpoints.sqlite"),
        llm_provider=_Provider(mode) if llm_provider == "default" else llm_provider,
        rag_service=_Rag(),
    )
    incident = service.create_incident(title="planner observability")
    response = orchestration.start_investigation(
        incident.id,
        InvestigationStartRequest(question="Why did the API fail?", logs="ERROR boom"),
    )
    investigation = service.get_investigation(response.investigation_id)
    return service, orchestration, investigation


@pytest.mark.parametrize(
    "error,expected",
    [
        (None, "none"),
        ("", "none"),
        ("LLMTimeoutError: Gemini request timed out", "timeout"),
        ("LLMRefusalError: blocked by safety filters", "refusal"),
        ("ValueError: unknown tool selected", "invalid_selection"),
        ("ValueError: too many selected tools", "invalid_selection"),
        ("ValidationError: selected_tools must be a list", "validation"),
        ("ValueError: provider returned an empty response", "empty"),
        ("ConnectionResetError: peer reset", "provider_error"),
    ],
)
def test_error_classification(error: str | None, expected: str) -> None:
    assert classify_planner_error(error) == expected


def test_status_vocabulary_is_finite_and_distinct() -> None:
    assert planner_status(used=True, configured=True, error=None) == PLANNER_STATUS_USED
    # No provider configured is the documented deterministic path, not a fault.
    assert planner_status(used=False, configured=False, error=None) == PLANNER_STATUS_FALLBACK
    assert (
        planner_status(used=False, configured=True, error="LLMTimeoutError: timed out")
        == "fallback_timeout"
    )
    invalid = planner_status(
        used=False, configured=True, error="ValueError: unknown tool selected"
    )
    assert invalid == "fallback_invalid_selection"
    assert invalid != "fallback_timeout"


def test_valid_planner_selection_is_recorded_as_used() -> None:
    service, orchestration, investigation = _run("ok")
    try:
        assert investigation.planner_used is True
        assert investigation.planner_status == "used"
        assert investigation.planner_error is None
    finally:
        orchestration.close()
        service.database.close()


def test_timeout_is_recorded_and_distinguishable() -> None:
    service, orchestration, investigation = _run("timeout")
    try:
        assert investigation.planner_used is False
        assert investigation.planner_status == "fallback_timeout"
        assert "LLMTimeoutError" in (investigation.planner_error or "")
        # Degradation never turns a failed report into a completed investigation.
        assert investigation.status != "completed"
    finally:
        orchestration.close()
        service.database.close()


def test_unknown_tool_is_rejected_and_never_executed() -> None:
    service, orchestration, investigation = _run("unknown_tool")
    try:
        assert investigation.planner_status == "fallback_invalid_selection"
        state = dict(
            orchestration.agent.graph.get_state(
                {"configurable": {"thread_id": investigation.thread_id}}
            ).values
        )
        assert "shell_executor" not in (state.get("selected_tools") or [])
        assert "shell_executor" not in (state.get("diagnostic_results") or {})
    finally:
        orchestration.close()
        service.database.close()


def test_provider_secrets_are_redacted_in_the_read_model() -> None:
    service, orchestration, investigation = _run("secret")
    try:
        text = f"{investigation.planner_status} {investigation.planner_error}"
        assert "sk-live-supersecret" not in text
        assert "abc123" not in text
        assert "redacted" in text
    finally:
        orchestration.close()
        service.database.close()


def test_historical_investigation_without_planner_columns_reads_as_unknown() -> None:
    directory = Path(tempfile.mkdtemp(prefix="opspilot-planner-legacy-"))
    service = IncidentService(db_path=str(directory / "app.sqlite"))
    incident = service.create_incident(title="legacy")
    investigation = service.create_investigation(incident_id=incident.id)
    # Simulate a row written before Phase 23: columns exist but are NULL.
    with sqlite3.connect(str(directory / "app.sqlite")) as conn:
        conn.execute(
            "UPDATE investigations SET planner_used = NULL, planner_status = NULL, "
            "planner_error = NULL WHERE id = ?",
            (investigation.id,),
        )
    reloaded = service.get_investigation(investigation.id)
    assert reloaded.planner_used is None
    assert reloaded.planner_status is None
    assert reloaded.planner_error is None
    service.database.close()


def test_old_database_schema_is_migrated_additively() -> None:
    directory = Path(tempfile.mkdtemp(prefix="opspilot-planner-migration-"))
    path = str(directory / "legacy.sqlite")
    with sqlite3.connect(path) as conn:
        # Minimal pre-Phase-23 investigations table, no planner columns.
        conn.execute(
            "CREATE TABLE investigations ("
            "id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, "
            "investigation_number TEXT NOT NULL UNIQUE, status TEXT NOT NULL, "
            "summary TEXT, suspected_root_cause TEXT, report TEXT, "
            "started_at TEXT NOT NULL, completed_at TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
    database = ApplicationDatabase(path)
    database.initialize()
    with database.transaction() as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(investigations)").fetchall()
        }
    database.close()
    assert {"planner_used", "planner_status", "planner_error"} <= columns
