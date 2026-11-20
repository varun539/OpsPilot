"""Durable LangGraph interrupt semantics for human approval (Phase 22).

The Phase 21.2 throwaway harness looked for ``__interrupt__`` inside
``get_state().values``. LangGraph records an interrupt as a checkpoint pending
write, so these tests assert the supported representation instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.agent import OpsPilotAgent
from backend.app.agent.checkpointer import SQLiteCheckpointer
from backend.app.agent.models import InvestigationPlan
from backend.app.incidents import IncidentService
from backend.app.incidents.exceptions import InvalidStateTransitionError
from backend.app.orchestration.schemas import InvestigationStartRequest
from backend.app.orchestration.service import InvestigationOrchestrationService
from backend.app.rag.models import InvestigationReport, RAGResponse


class StaticRAG:
    """Valid insufficient-evidence report; approval must complete the workflow."""

    provider_name = "test"
    model = "test"

    def investigate_with_evidence(self, question: str, evidence, **kwargs) -> RAGResponse:
        return RAGResponse(
            question=question,
            report=InvestigationReport(
                summary="No usable evidence was available.",
                incident_category="insufficient_evidence",
                evidence_status="insufficient",
            ),
            generation_status="insufficient_evidence",
            valid=True,
            citation_valid=True,
        )


class PromptProvider:
    model = "test"

    def generate_structured(self, *, output_schema, **kwargs):
        class Result:
            output = InvestigationPlan(selected_tools=["log_analyzer"], rationale="logs")
            model = "test"
            usage = None

        return Result()


def test_interrupt_is_recorded_as_a_checkpoint_pending_write(tmp_path: Path) -> None:
    manager = SQLiteCheckpointer(str(tmp_path / "checkpoints.sqlite"))
    saver = manager.open()
    agent = OpsPilotAgent(rag_service=StaticRAG(), checkpointer=saver, in_memory=False)
    thread_id = "thread-interrupt-regression"
    try:
        paused = agent.investigate(
            {"question": "Why did the service fail?", "logs": "ERROR boom"},
            thread_id=thread_id,
        )

        assert "__interrupt__" in paused
        # The workflow is paused before human_review's own update is committed.
        assert paused.get("execution_status") != "completed"
        assert paused.get("approval_status") in (None, "not_requested")

        snapshot = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        writes = list(getattr(snapshot, "pending_writes", None) or [])
        interrupt_writes = [write for write in writes if str(write[1]) == "__interrupt__"]
        assert interrupt_writes, "no __interrupt__ pending write was recorded"
        payloads = interrupt_writes[0][2]
        assert isinstance(payloads, list) and payloads
        payload = getattr(payloads[0], "value", payloads[0])
        assert isinstance(payload, dict)
        assert payload.get("approval_choices") == ["approved", "rejected"]
        assert payload.get("investigation_id")

        resumed = agent.resume(thread_id=thread_id, decision="approved")
        assert resumed.get("execution_status") == "completed"
        assert resumed.get("thread_id") == thread_id
    finally:
        agent.close()
        manager.close()


def test_approval_cannot_be_repeated_after_completion(tmp_path: Path) -> None:
    service = IncidentService(db_path=str(tmp_path / "app.sqlite"))
    orchestration = InvestigationOrchestrationService(
        incident_service=service,
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        llm_provider=PromptProvider(),
        rag_service=StaticRAG(),
    )
    try:
        incident = service.create_incident(title="repeat approval")
        started = orchestration.start_investigation(
            incident.id, InvestigationStartRequest(logs="ERROR boom")
        )
        thread_id = service.get_investigation(started.investigation_id).thread_id
        assert started.workflow_status == "awaiting_approval"

        completed = orchestration.approve_investigation(
            started.investigation_id, "approved", reason="first"
        )
        assert completed.workflow_status == "completed"
        assert service.get_investigation(started.investigation_id).thread_id == thread_id

        with pytest.raises(InvalidStateTransitionError):
            orchestration.approve_investigation(
                started.investigation_id, "approved", reason="second"
            )

        investigation = service.get_investigation(started.investigation_id)
        assert investigation.status == "completed"
        assert len(service.list_approvals(investigation_id=started.investigation_id)) == 1
        assert len(service.list_investigations(incident.id)) == 1
    finally:
        orchestration.close()
        service.database.close()
