"""Orchestration service for end-to-end OpsPilot investigations."""

from __future__ import annotations

import inspect
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.app.agent import OpsPilotAgent
from backend.app.agent.models import InvestigationRequest
from backend.app.incidents import IncidentService
from backend.app.incidents.exceptions import (
    DuplicateRecordError,
    InvalidStateTransitionError,
)
from backend.app.incidents.models import InvestigationStatus
from backend.app.orchestration.exceptions import (
    AIServiceUnavailableError,
    InvestigationResumeError,
    InvestigationStartError,
)
from backend.app.orchestration.schemas import (
    InvestigationApprovalResponse,
    InvestigationStartRequest,
    InvestigationStartResponse,
)

ACTIVE_WORKFLOW_STATUSES = {"created", "running", "awaiting_approval", "resuming"}


def _checkpoint_path_from_env() -> str | None:
    raw = os.getenv("OPSPILOT_CHECKPOINT_DB", "").strip()
    return raw or None


_SECRET_PATTERN = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]+|(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+)"
)


def _sanitize_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    text = _SECRET_PATTERN.sub("[redacted]", text)
    return text[:500]


def _accepts_keyword(method: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return True
    if name in parameters:
        return True
    return any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


class InvestigationOrchestrationService:
    """Coordinate incident, agent, approval, and persistence workflows."""

    def __init__(
        self,
        *,
        incident_service: IncidentService | None = None,
        service: IncidentService | None = None,
        database: Any = None,
        db_path: str | None = None,
        repository: Any = None,
        checkpoint_db_path: str | None = None,
        checkpoint_path: str | None = None,
        checkpoint_db: str | None = None,
        checkpointer: Any = None,
        in_memory: bool = False,
        agent: Any = None,
        llm_provider: Any = None,
        retrieval_engine: Any = None,
        rag_service: Any = None,
        search_engine: Any = None,
        ai_runtime: Any = None,
        **kwargs: Any,
    ) -> None:
        incident_service = incident_service or service
        if incident_service is not None:
            self.incident_service = incident_service
        else:
            self.incident_service = IncidentService(
                database=database, db_path=db_path, repository=repository
            )

        self._owned_checkpointer = None
        resolved_checkpoint = (
            checkpoint_db_path
            or checkpoint_path
            or checkpoint_db
            or kwargs.get("checkpoint_db_path")
        )
        if resolved_checkpoint is None:
            resolved_checkpoint = kwargs.get("checkpoint_path") or kwargs.get("checkpoint_db")
        if resolved_checkpoint is None:
            resolved_checkpoint = _checkpoint_path_from_env()

        if agent is not None:
            self.agent = agent
            self.checkpointer = getattr(agent, "checkpointer", None)
        else:
            if checkpointer is not None:
                self.checkpointer = checkpointer
            elif in_memory:
                self.checkpointer = None
            else:
                from backend.app.agent.checkpointer import SQLiteCheckpointer

                manager = SQLiteCheckpointer(resolved_checkpoint)
                self.checkpointer = manager.open()
                self._owned_checkpointer = manager
            self.agent = OpsPilotAgent(
                llm_provider=llm_provider,
                retrieval_engine=retrieval_engine or search_engine,
                rag_service=rag_service,
                checkpointer=self.checkpointer,
                in_memory=self.checkpointer is None,
            )
            if self.checkpointer is None:
                self.checkpointer = getattr(self.agent, "checkpointer", None)

        self.llm_provider = llm_provider
        self.retrieval_engine = retrieval_engine or search_engine
        self.rag_service = rag_service
        # Explicit runtime marker from the application lifespan. Tests that
        # inject a provider/agent never set this and keep working unchanged.
        self.ai_runtime = ai_runtime

    def _ensure_ai_available(self) -> None:
        """Refuse new investigations when the configured runtime is not ready."""
        runtime = getattr(self, "ai_runtime", None)
        if runtime is None:
            return
        if getattr(runtime, "ready", False):
            return
        message = getattr(runtime, "unavailable_message", None)
        raise AIServiceUnavailableError(
            message() if callable(message) else "AI runtime is not available"
        )

    def close(self) -> None:
        if self._owned_checkpointer is not None:
            try:
                self._owned_checkpointer.close()
            except Exception:
                pass
            self._owned_checkpointer = None
        agent = getattr(self, "agent", None)
        if agent is not None and hasattr(agent, "close"):
            try:
                agent.close()
            except Exception:
                pass

    def _thread_id(self) -> str:
        return f"thread-{uuid.uuid4().hex}"

    def _incident_context(self, incident: Any) -> str:
        return (
            f"Incident: {getattr(incident, 'incident_number', '')} "
            f"({getattr(incident, 'id', '')})\n"
            f"Title: {getattr(incident, 'title', '')}\n"
            f"Severity: {getattr(incident, 'severity', '')}\n"
            f"Priority: {getattr(incident, 'priority', '')}\n"
            f"Status: {getattr(incident, 'status', '')}\n"
            f"Environment: {getattr(incident, 'environment', '')}\n"
            f"Service: {getattr(incident, 'service_name', '')}\n"
            f"Description: {getattr(incident, 'description', '') or ''}"
        )

    def _prepare_request(self, incident: Any, payload: Any) -> InvestigationRequest:
        fallback_question = (
            f"Investigate incident {getattr(incident, 'incident_number', '')}: "
            f"{getattr(incident, 'title', '')}"
        ).strip()
        if isinstance(payload, InvestigationStartRequest):
            req = payload.to_agent_request(fallback_question)
        elif isinstance(payload, InvestigationRequest):
            req = payload.model_copy(deep=True)
        else:
            req = InvestigationStartRequest.model_validate(payload).to_agent_request(
                fallback_question
            )
        existing_context = req.incident_context
        generated = self._incident_context(incident)
        req.incident_context = (
            f"{existing_context}\n\n{generated}" if existing_context else generated
        )
        return req

    def _persist_evidence(self, investigation_id: str, state: dict[str, Any]) -> None:
        service = self.incident_service
        existing = service.list_evidence(investigation_id)
        existing_keys = {
            (getattr(item, "evidence_type", None), getattr(item, "source", None))
            for item in existing
        }

        diagnostic_results = state.get("diagnostic_results") or {}
        for tool, result in diagnostic_results.items():
            key = ("diagnostic", tool)
            if key in existing_keys:
                continue
            service.store_evidence(
                investigation_id,
                "diagnostic",
                source=tool,
                content=result,
                metadata={"tool": tool},
            )
            existing_keys.add(key)

        for item in state.get("retrieval_results") or []:
            if not isinstance(item, dict):
                item = getattr(item, "model_dump", lambda: item)()
                if not isinstance(item, dict):
                    item = {}
            chunk_id = item.get("chunk_id") or item.get("id")
            key = ("knowledge_base", chunk_id)
            if key in existing_keys:
                continue
            service.store_evidence(
                investigation_id,
                "knowledge_base",
                source=chunk_id,
                content=item,
                metadata={
                    "doc_id": item.get("doc_id"),
                    "title": item.get("title"),
                    "source_path": item.get("source_path"),
                },
            )
            existing_keys.add(key)

        correlation = state.get("correlation_results")
        if correlation:
            key = ("correlation", "incident_correlator")
            if key not in existing_keys:
                service.store_evidence(
                    investigation_id,
                    "correlation",
                    source="incident_correlator",
                    content=correlation,
                    metadata={},
                )
                existing_keys.add(key)

    def _persist_resume_output(self, investigation_id: str, state: dict[str, Any]) -> None:
        report = state.get("investigation_report")
        if report is not None:
            self.incident_service.persist_pending_report(
                investigation_id,
                report,
                status=InvestigationStatus.completed.value,
            )
        self._persist_evidence(investigation_id, state)

    def _mark_resume_failed(
        self,
        investigation_id: str,
        incident_id: str | None,
        principal: Any,
        decision: str,
        error: str,
    ) -> None:
        self.incident_service.update_investigation(
            investigation_id,
            workflow_status="resume_failed",
            last_error=error[:500],
        )
        if incident_id:
            self.incident_service.record_incident_event(
                incident_id,
                "resume_failed",
                actor=getattr(principal, "subject", None),
                metadata={
                    "investigation_id": investigation_id,
                    "error": error[:500],
                    "decision": decision,
                },
            )

    def start_investigation(
        self, incident_id: str, payload: Any, principal: Any = None
    ) -> InvestigationStartResponse:
        self._ensure_ai_available()
        incident = self.incident_service.get_incident(incident_id)

        active = self.incident_service.has_active_investigation(incident_id)
        if active is not None:
            raise InvalidStateTransitionError(
                "an investigation is already active for this incident"
            )

        request = self._prepare_request(incident, payload)
        thread_id = self._thread_id()

        try:
            investigation = self.incident_service.create_investigation(
                incident_id=incident_id,
                status=InvestigationStatus.started.value,
                workflow_status="created",
                thread_id=thread_id,
                summary=None,
            )
        except DuplicateRecordError:
            raise InvalidStateTransitionError(
                "an investigation is already active for this incident"
            )

        self.incident_service.update_investigation(
            investigation.id,
            workflow_status="running",
            status=InvestigationStatus.running.value,
        )

        # Update incident lifecycle when valid.
        try:
            self.incident_service.transition_incident(incident_id, "investigating")
        except InvalidStateTransitionError:
            pass

        self.incident_service.record_incident_event(
            incident_id,
            "investigation_started",
            actor=getattr(principal, "subject", None),
            metadata={"investigation_id": investigation.id},
        )

        try:
            investigate_kwargs: dict[str, Any] = {"thread_id": thread_id}
            if _accepts_keyword(self.agent.investigate, "investigation_id"):
                investigate_kwargs["investigation_id"] = investigation.id
            state = self.agent.investigate(request, **investigate_kwargs)
        except Exception as exc:
            sanitized = _sanitize_error(exc)
            self.incident_service.update_investigation(
                investigation.id,
                workflow_status="failed",
                status=InvestigationStatus.failed.value,
                last_error=sanitized,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self.incident_service.record_incident_event(
                incident_id,
                "investigation_failed",
                actor=getattr(principal, "subject", None),
                metadata={"investigation_id": investigation.id, "error": sanitized},
            )
            raise InvestigationStartError("investigation execution failed")

        interrupted = "__interrupt__" in (state or {})
        report = (state or {}).get("investigation_report")
        execution_status = (state or {}).get("execution_status", "pending")

        if interrupted:
            try:
                if report is not None:
                    self.incident_service.persist_pending_report(
                        investigation.id,
                        report,
                        status=InvestigationStatus.running.value,
                    )
                self._persist_evidence(investigation.id, state or {})
            except Exception as exc:
                sanitized = _sanitize_error(exc)
                self.incident_service.update_investigation(
                    investigation.id,
                    workflow_status="failed",
                    status=InvestigationStatus.failed.value,
                    last_error=sanitized,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                self.incident_service.record_incident_event(
                    incident_id,
                    "investigation_failed",
                    actor=getattr(principal, "subject", None),
                    metadata={
                        "investigation_id": investigation.id,
                        "error": sanitized,
                    },
                )
                raise InvestigationStartError("investigation result persistence failed")
            approval = None
            approvals = self.incident_service.list_approvals(investigation_id=investigation.id)
            if approvals:
                approval = approvals[0]
            else:
                approval = self.incident_service.create_approval(
                    incident_id=incident_id,
                    investigation_id=investigation.id,
                    approval_type="human_review",
                )
            self.incident_service.update_investigation(
                investigation.id,
                workflow_status="awaiting_approval",
                status=InvestigationStatus.running.value,
            )
            try:
                self.incident_service.transition_incident(incident_id, "awaiting_approval")
            except InvalidStateTransitionError:
                pass
            self.incident_service.record_incident_event(
                incident_id,
                "human_review_requested",
                actor=getattr(principal, "subject", None),
                metadata={
                    "investigation_id": investigation.id,
                    "approval_id": getattr(approval, "id", None),
                },
            )
            return InvestigationStartResponse(
                incident_id=incident_id,
                investigation_id=investigation.id,
                workflow_status="awaiting_approval",
                approval_status=getattr(approval, "status", "pending"),
                message="investigation paused for human approval",
            )

        # Synchronous completion/failure without interrupt.
        final_status = (
            "completed"
            if execution_status
            in {
                "completed",
                "approved",
                "validated_report",
            }
            else "failed"
        )
        if report is not None:
            self.incident_service.persist_pending_report(
                investigation.id,
                report,
                status=(
                    InvestigationStatus.completed.value
                    if final_status == "completed"
                    else InvestigationStatus.failed.value
                ),
            )
        self._persist_evidence(investigation.id, state or {})
        workflow_status = "completed" if final_status == "completed" else "failed"
        self.incident_service.update_investigation(
            investigation.id,
            workflow_status=workflow_status,
            status=(
                InvestigationStatus.completed.value
                if final_status == "completed"
                else InvestigationStatus.failed.value
            ),
            last_error=(
                None
                if final_status == "completed"
                else (
                    _sanitize_error(Exception(str(error_details)))
                    if (error_details := (state or {}).get("error_details"))
                    else "investigation did not complete"
                )
            ),
        )
        self.incident_service.record_incident_event(
            incident_id,
            "investigation_completed" if final_status == "completed" else "investigation_failed",
            actor=getattr(principal, "subject", None),
            metadata={"investigation_id": investigation.id},
        )
        return InvestigationStartResponse(
            incident_id=incident_id,
            investigation_id=investigation.id,
            workflow_status=workflow_status,
            approval_status=None,
            message=(
                "investigation completed" if final_status == "completed" else "investigation failed"
            ),
        )

    def get_investigation(self, investigation_id: str) -> Any:
        return self.incident_service.get_investigation(investigation_id)

    def get_approval(self, investigation_id: str) -> Any:
        approvals = self.incident_service.list_approvals(investigation_id=investigation_id)
        if not approvals:
            return None
        return approvals[0]

    def approve_investigation(
        self,
        investigation_id: str,
        decision: str,
        reason: str | None = None,
        principal: Any = None,
    ) -> InvestigationApprovalResponse:
        investigation = self.incident_service.get_investigation(investigation_id)
        incident_id = getattr(investigation, "incident_id", None)
        if investigation.workflow_status not in {"awaiting_approval", "resume_failed"}:
            raise InvalidStateTransitionError("investigation is not awaiting approval")

        approvals = self.incident_service.list_approvals(investigation_id=investigation_id)
        if not approvals:
            raise InvalidStateTransitionError("no pending approval found")
        approval = approvals[0]
        if getattr(approval, "status", None) != "pending":
            # Allow retry of a previously recorded decision after resume failure.
            recorded_decision = (
                "approved" if getattr(approval, "status", "") == "approved" else "rejected"
            )
            if recorded_decision != decision:
                raise InvalidStateTransitionError("conflicting approval decision")
            if investigation.workflow_status not in {"resume_failed", "awaiting_approval"}:
                raise InvalidStateTransitionError("approval decision has already been processed")
        else:
            self.incident_service.record_approval(
                approval.id,
                decision,
                decided_by=getattr(principal, "subject", None),
                reason=reason,
            )
            # Record the decision even if the graph resume later fails: the
            # approval itself is durable and remains traceable.
            self.incident_service.record_incident_event(
                incident_id,
                ("approval_approved" if decision == "approved" else "approval_rejected"),
                actor=getattr(principal, "subject", None),
                metadata={
                    "investigation_id": investigation_id,
                    "decision": decision,
                    "reason": reason,
                },
            )

        # Concurrency-safe claim: only one caller can move an investigation
        # into resuming. A resume_failed investigation may be retried with the
        # same recorded decision; two concurrent retries cannot both win.
        with self.incident_service.database.transaction() as conn:
            cur = conn.execute(
                "UPDATE investigations SET workflow_status = ?, updated_at = ? "
                "WHERE id = ? AND workflow_status IN (?, ?)",
                (
                    "resuming",
                    datetime.now(timezone.utc).isoformat(),
                    investigation_id,
                    "awaiting_approval",
                    "resume_failed",
                ),
            )
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT workflow_status FROM investigations WHERE id = ?",
                    (investigation_id,),
                ).fetchone()
                status = row["workflow_status"] if row else None
                if status == "resuming":
                    raise InvalidStateTransitionError("investigation is already being resumed")
                raise InvalidStateTransitionError("investigation is not awaiting approval")

        thread_id = getattr(investigation, "thread_id", None)
        if not thread_id:
            self._mark_resume_failed(
                investigation_id,
                incident_id,
                principal,
                decision,
                "missing workflow thread id",
            )
            raise InvestigationResumeError("missing workflow thread id")

        try:
            resume_kwargs: dict[str, Any] = {
                "thread_id": thread_id,
                "decision": decision,
            }
            if _accepts_keyword(self.agent.resume, "reviewer_comment"):
                resume_kwargs["reviewer_comment"] = reason
            state = self.agent.resume(**resume_kwargs)
        except Exception as exc:
            sanitized = _sanitize_error(exc)
            self._mark_resume_failed(investigation_id, incident_id, principal, decision, sanitized)
            raise InvestigationResumeError("workflow resume failed")

        try:
            execution_status = (state or {}).get("execution_status", "pending")
            approval_status = (state or {}).get("approval_status", decision)
            self._persist_resume_output(investigation_id, state or {})
        except Exception as exc:
            sanitized = _sanitize_error(exc)
            self._mark_resume_failed(investigation_id, incident_id, principal, decision, sanitized)
            raise InvestigationResumeError("workflow result persistence failed")

        final_workflow_status = (
            "completed"
            if execution_status in {"completed", "approved", "validated_report", "rejected"}
            else "failed"
        )
        self.incident_service.update_investigation(
            investigation_id,
            workflow_status=final_workflow_status,
            status=(
                InvestigationStatus.completed.value
                if final_workflow_status == "completed"
                else InvestigationStatus.failed.value
            ),
            last_error=None,
        )

        # Incident returns to investigating after review; never auto-resolve.
        try:
            self.incident_service.transition_incident(incident_id, "investigating")
        except InvalidStateTransitionError:
            pass

        self.incident_service.record_incident_event(
            incident_id,
            "workflow_resumed",
            actor=getattr(principal, "subject", None),
            metadata={"investigation_id": investigation_id},
        )
        self.incident_service.record_incident_event(
            incident_id,
            (
                "investigation_completed"
                if final_workflow_status == "completed"
                else "investigation_failed"
            ),
            actor=getattr(principal, "subject", None),
            metadata={"investigation_id": investigation_id},
        )

        return InvestigationApprovalResponse(
            investigation_id=investigation_id,
            incident_id=incident_id,
            workflow_status=final_workflow_status,
            approval_status=approval_status,
            message="investigation resumed",
            result={"execution_status": execution_status},
        )

    start = start_investigation
    resume = approve_investigation
    decide = approve_investigation
    retry_resume = approve_investigation


OrchestrationService = InvestigationOrchestrationService
