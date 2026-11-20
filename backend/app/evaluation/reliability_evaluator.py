"""Failure-handling and reliability benchmark.

Runs controlled failures through the real orchestration service, LangGraph
graph, retrieval runtime, SQLite persistence and approval path. Each scenario
declares its expected classification, so a failed run being persisted as a
successful investigation would be measured rather than hidden.

Two families of fault are covered:

* **Provider faults** (REL-001..REL-008) - timeout, HTTP error, refusal,
  malformed and empty output, plus approval after failure and restart recovery.
* **Non-LLM faults** (REL-009..REL-016) - retrieval exception, diagnostic tool
  exception, invalid checkpoint state, persistence failure, malformed incident
  input, state serialization failure, duplicate approval transition and an
  unexpected graph-node exception.

Everything is deterministic and offline; no LLM API is called.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

from backend.app.agent.models import InvestigationPlan
from backend.app.incidents import IncidentService
from backend.app.llm.base import LLMResult, LLMUsage
from backend.app.llm.exceptions import (
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
)
from backend.app.orchestration.schemas import InvestigationStartRequest
from backend.app.orchestration.service import InvestigationOrchestrationService
from backend.app.rag.models import (
    EvidenceReference,
    InvestigationReport,
    RAGResponse,
    RetrievedEvidence,
)

from .metrics import average
from .models import CaseResult, SuiteResult
from .retrieval_evaluator import build_retrieval_runtime


class PlannerProvider:
    """Planner behavior only; reports come from the RAG fixture."""

    model = "reliability-planner"
    provider_name = "reliability"

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def generate_structured(self, *, output_schema: Any, **_: Any) -> LLMResult:
        self.calls += 1
        if output_schema.__name__ != "InvestigationPlan":
            raise AssertionError("reports must be produced by the RAG fixture")
        if self.mode == "timeout":
            raise LLMTimeoutError("Gemini request timed out")
        if self.mode == "invalid_selection":
            return LLMResult(
                output={"selected_tools": ["shell_executor"], "rationale": "run a shell"},
                model=self.model,
                usage=LLMUsage(total_tokens=1),
            )
        return LLMResult(
            output=InvestigationPlan(selected_tools=["log_analyzer"], rationale="logs"),
            model=self.model,
            usage=LLMUsage(total_tokens=1),
        )


class ReportRAG:
    """Report behavior: a grounded success or a classified provider failure."""

    provider_name = "reliability"
    model = "reliability-rag"

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def investigate_with_evidence(
        self, question: str, evidence: Any, **_: Any
    ) -> RAGResponse | None:
        self.calls += 1
        if self.mode == "http_503":
            raise LLMResponseError("Gemini API request failed (status 503)")
        if self.mode == "malformed":
            raise ValueError("invalid JSON in provider output")
        if self.mode == "refusal":
            raise LLMRefusalError("Gemini response was blocked")
        if self.mode == "empty":
            return None

        items = [
            RetrievedEvidence(
                chunk_id=str(item.get("chunk_id") or ""),
                doc_id=str(item.get("doc_id") or ""),
                title=str(item.get("title") or ""),
                source_path=str(item.get("source_path") or ""),
                chunk_text=str(item.get("chunk_text") or ""),
            )
            for item in (evidence or [])
            if isinstance(item, dict) and item.get("chunk_id")
        ]
        if not items:
            return RAGResponse(
                question=question,
                report=InvestigationReport(
                    summary="No retrieved evidence was available.",
                    incident_category="insufficient_evidence",
                    evidence_status="insufficient",
                ),
                generation_status="insufficient_evidence",
                valid=True,
                citation_valid=True,
            )
        chunk_id = items[0].chunk_id
        report = InvestigationReport(
            summary="Connection pool exhaustion is supported by the retrieved runbook.",
            incident_category="database_pool",
            evidence_status="sufficient",
            root_cause_hypotheses=[
                {
                    "title": "Pool exhaustion",
                    "explanation": "Demand exceeded the configured pool limit.",
                    "supporting_chunk_ids": [chunk_id],
                    "evidence_strength": "medium",
                }
            ],
            supporting_evidence=[
                EvidenceReference(
                    chunk_id=chunk_id,
                    doc_id=items[0].doc_id,
                    title=items[0].title,
                    reason="Retrieved runbook.",
                )
            ],
            recommended_actions=[
                {
                    "description": "Inspect pool sizing and connection release.",
                    "rationale": "Both explain pool exhaustion.",
                    "supporting_chunk_ids": [chunk_id],
                    "risk_level": "low",
                    "requires_human_review": True,
                }
            ],
        )
        if self.mode == "unserializable":
            # A value the JSON encoder explicitly refuses. This simulates a
            # state/report that cannot be serialized for persistence.
            report.limitations = [object()]  # type: ignore[list-item]
        return RAGResponse(
            question=question,
            report=report,
            retrieved_evidence=items,
            provider=self.provider_name,
            model=self.model,
            generation_status="success",
            valid=True,
            citation_valid=True,
        )


class _FailingSearch:
    """Retrieval engine that always raises, for the retrieval-fault scenario."""

    def search(self, *_: Any, **__: Any) -> Any:
        raise ConnectionError("vector store is unavailable")


@dataclass(frozen=True)
class Scenario:
    case_id: str
    description: str
    planner_mode: str = "ok"
    report_mode: str = "success"
    # none | retrieval | tool | checkpoint | persistence | input |
    # serialization | approval | graph
    fault: str = "none"
    expect_report_valid: bool = True
    expect_start_status: str = "awaiting_approval"
    expect_final_status: str = "completed"
    expected_classification: str = "successful_investigation"
    approve: bool = True
    restart_before_approval: bool = False
    expect_evidence: bool = True
    expect_raise: str | None = None
    expect_degraded: bool = False


SCENARIOS: list[Scenario] = [
    Scenario(
        "REL-001",
        "Successful generation, human review and approval.",
        expected_classification="successful_investigation",
    ),
    Scenario(
        "REL-002",
        "Planner timeout falls back deterministically; report still succeeds.",
        planner_mode="timeout",
        expected_classification="planner_degraded_valid_report",
        expect_degraded=True,
    ),
    Scenario(
        "REL-003",
        "Report HTTP 503 pauses with an invalid report and is not completed.",
        report_mode="http_503",
        expect_report_valid=False,
        expect_final_status="awaiting_approval",
        expected_classification="provider_failure_paused",
        approve=False,
    ),
    Scenario(
        "REL-004",
        "Approving after failed generation must fail, not complete.",
        report_mode="http_503",
        expect_report_valid=False,
        expect_final_status="failed",
        expected_classification="approval_after_failure_blocked",
    ),
    Scenario(
        "REL-005",
        "Malformed provider output is a classified failure.",
        report_mode="malformed",
        expect_report_valid=False,
        expect_final_status="failed",
        expected_classification="malformed_output_blocked",
    ),
    Scenario(
        "REL-006",
        "Empty provider output is a classified failure.",
        report_mode="empty",
        expect_report_valid=False,
        expect_final_status="failed",
        expected_classification="empty_output_blocked",
    ),
    Scenario(
        "REL-007",
        "Provider refusal is a classified failure.",
        report_mode="refusal",
        expect_report_valid=False,
        expect_final_status="failed",
        expected_classification="refusal_blocked",
    ),
    Scenario(
        "REL-008",
        "Failed generation is still blocked after restart recovery.",
        report_mode="http_503",
        expect_report_valid=False,
        expect_final_status="failed",
        expected_classification="recovery_failure_blocked",
        restart_before_approval=True,
    ),
    # --- Non-LLM faults -------------------------------------------------------
    Scenario(
        "REL-009",
        "Retrieval engine raises; investigation degrades instead of crashing.",
        fault="retrieval",
        expect_final_status="completed",
        expected_classification="retrieval_failure_degraded",
        expect_evidence=False,
        expect_degraded=True,
    ),
    Scenario(
        "REL-010",
        "A diagnostic tool raises; the failure is recorded, not swallowed.",
        fault="tool",
        expected_classification="tool_failure_degraded",
        expect_degraded=True,
    ),
    Scenario(
        "REL-011",
        "Missing checkpoint thread binding; resume fails safely and is never completed.",
        fault="checkpoint",
        expect_final_status="resume_failed",
        expected_classification="invalid_checkpoint_state_blocked",
    ),
    Scenario(
        "REL-012",
        "Persistence raises during start; the investigation is failed, not completed.",
        fault="persistence",
        expect_raise="InvestigationStartError",
        expect_final_status="failed",
        expected_classification="persistence_failure_failed",
        approve=False,
    ),
    Scenario(
        "REL-013",
        "Malformed incident input is rejected before any investigation is created.",
        fault="input",
        expect_raise="ValidationError",
        expect_final_status="failed",
        expected_classification="invalid_input_rejected",
        approve=False,
    ),
    Scenario(
        "REL-014",
        "Unserializable report state fails the start instead of persisting garbage.",
        fault="serialization",
        expect_raise="InvestigationStartError",
        expect_final_status="failed",
        expected_classification="serialization_failure_failed",
        approve=False,
    ),
    Scenario(
        "REL-015",
        "A second approval on a completed investigation is rejected.",
        fault="approval",
        expected_classification="duplicate_approval_blocked",
    ),
    Scenario(
        "REL-016",
        "Unexpected graph-node exception fails the start without a completed status.",
        fault="graph",
        expect_raise="InvestigationStartError",
        expect_final_status="failed",
        expected_classification="unexpected_node_failure",
        approve=False,
    ),
    Scenario(
        "REL-017",
        "Invalid planner tool selection falls back deterministically and stays safe.",
        planner_mode="invalid_selection",
        expected_classification="planner_invalid_selection_degraded",
        expect_degraded=True,
    ),
]


def _run_scenario(scenario: Scenario, runtime: Any) -> CaseResult:
    started = time.perf_counter()
    failures: list[str] = []
    directory = Path(tempfile.mkdtemp(prefix="opspilot-rel-eval-"))
    app_db = str(directory / "app.sqlite")
    checkpoint_db = str(directory / "checkpoints.sqlite")

    def build() -> tuple[IncidentService, InvestigationOrchestrationService]:
        service = IncidentService(db_path=app_db)
        retrieval = _FailingSearch() if scenario.fault == "retrieval" else runtime.hybrid
        orchestration = InvestigationOrchestrationService(
            incident_service=service,
            checkpoint_db_path=checkpoint_db,
            llm_provider=PlannerProvider(scenario.planner_mode),
            retrieval_engine=retrieval,
            rag_service=ReportRAG(
                "unserializable" if scenario.fault == "serialization" else scenario.report_mode
            ),
        )
        return service, orchestration

    service, orchestration = build()

    def _start(payload: Any) -> Any:
        if scenario.fault == "tool":
            with mock.patch(
                "backend.app.agent.nodes.execute_tool",
                side_effect=RuntimeError("diagnostic tool crashed"),
            ):
                return orchestration.start_investigation(incident.id, payload)
        if scenario.fault == "persistence":
            with mock.patch.object(
                IncidentService,
                "persist_pending_report",
                side_effect=sqlite3.OperationalError("database or disk is full"),
            ):
                return orchestration.start_investigation(incident.id, payload)
        if scenario.fault == "graph":
            # build_tool_inputs runs outside the per-tool try/except, so this
            # escapes the node exactly like an unexpected defect would.
            with mock.patch(
                "backend.app.agent.nodes.build_tool_inputs",
                side_effect=RuntimeError("unexpected graph-node failure"),
            ):
                return orchestration.start_investigation(incident.id, payload)
        return orchestration.start_investigation(incident.id, payload)

    try:
        incident = service.create_incident(title="reliability benchmark")
        payload: Any
        if scenario.fault == "input":
            payload = {"question": "Why is the pool exhausted?", "http_status": 999}
        else:
            payload = InvestigationStartRequest(
                question="Why is the database connection pool exhausted?",
                logs="ERROR QueuePool limit reached, connection timed out",
            )

        raised: str | None = None
        started_response: Any = None
        try:
            started_response = _start(payload)
        except Exception as exc:  # noqa: BLE001 - classification is the subject
            raised = type(exc).__name__

        if scenario.expect_raise is not None:
            if raised != scenario.expect_raise:
                failures.append(f"raised {raised} != {scenario.expect_raise}")
            rows = service.list_investigations(incident.id)
            if scenario.fault == "input":
                if rows:
                    failures.append("malformed input created an investigation record")
            elif not rows:
                failures.append("failure did not leave an investigation record")
            else:
                persisted = rows[0]
                if persisted.status == "completed":
                    failures.append("failed start persisted as completed")
                if persisted.workflow_status == "completed":
                    failures.append("failed start has workflow_status completed")
                last_error = persisted.last_error or ""
                if not last_error:
                    failures.append("failure did not record a sanitized last_error")
                elif "Traceback" in last_error:
                    failures.append("raw traceback leaked into last_error")
                elif "sk-" in last_error:
                    failures.append("credential leaked into last_error")
            return CaseResult(
                case_id=scenario.case_id,
                suite="reliability",
                passed=not failures,
                metrics={
                    "expected_classification": scenario.expected_classification,
                    "raised": raised,
                    "false_completion": False,
                    "evidence_retained": None,
                    "planner_observable": None,
                    # A scenario that aborts during start cannot be expected to
                    # complete, regardless of its final-status label.
                    "expected_completion": False,
                    "classification_correct": not failures,
                },
                failures=failures,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        investigation = service.get_investigation(started_response.investigation_id)
        thread_id = investigation.thread_id
        state = dict(
            orchestration.agent.graph.get_state(
                {"configurable": {"thread_id": thread_id}}
            ).values
        )
        evidence = service.list_evidence(started_response.investigation_id)
        report = investigation.report or {}
        observed_valid = started_response.report_valid
        paused = started_response.workflow_status == scenario.expect_start_status

        if paused is False:
            failures.append(
                f"start status {started_response.workflow_status!r} != "
                f"{scenario.expect_start_status!r}"
            )
        if observed_valid != scenario.expect_report_valid:
            failures.append(
                f"report_valid {observed_valid} != {scenario.expect_report_valid}"
            )
        if not scenario.expect_report_valid:
            if report.get("generation_status") != "provider_error":
                failures.append("failed report was not persisted as provider_error")
            if investigation.status == "completed":
                failures.append("failed report was completed before approval")

        planner_status = str(state.get("planner_status") or "")
        planner_observable = bool(planner_status)
        if scenario.planner_mode == "timeout":
            if not planner_status.startswith("fallback"):
                failures.append("planner degradation was not recorded in state")
        degradation_signals = (
            planner_status.startswith("fallback")
            or bool(state.get("error_details"))
            or bool(state.get("tool_errors"))
        )
        if scenario.expect_degraded and not degradation_signals:
            failures.append("degradation was not observable in state")

        if scenario.fault == "retrieval":
            if not state.get("error_details"):
                failures.append("retrieval exception was not recorded in error_details")
            if state.get("retrieval_results"):
                failures.append("retrieval results present despite engine failure")
        if scenario.fault == "tool":
            if not state.get("tool_errors"):
                failures.append("diagnostic tool exception was not recorded in tool_errors")
            if not any("failed or were skipped" in w for w in state.get("warnings") or []):
                failures.append("diagnostic tool failure produced no operator warning")

        selected = list(state.get("selected_tools") or [])
        allowed = {
            "log_analyzer",
            "stack_trace_analyzer",
            "http_error_classifier",
            "config_validator",
            "database_diagnostics",
            "incident_correlator",
        }
        if not set(selected).issubset(allowed):
            failures.append(f"unauthorized tool reached the graph: {selected}")

        if scenario.fault == "checkpoint":
            # Invalid checkpoint state: the graph thread binding is gone, so the
            # stored state cannot be resumed. The orchestration must refuse to
            # invent a resume rather than completing on an empty checkpoint.
            with sqlite3.connect(app_db, timeout=30) as conn:
                conn.execute(
                    "UPDATE investigations SET thread_id = NULL WHERE id = ?",
                    (started_response.investigation_id,),
                )

        if scenario.restart_before_approval:
            orchestration.close()
            service.database.close()
            service, orchestration = build()
            investigation = service.get_investigation(started_response.investigation_id)
            if investigation.workflow_status != "awaiting_approval":
                failures.append("restart did not preserve the awaiting_approval pause")

        final_status = investigation.workflow_status
        approval_status: str | None = None
        if scenario.approve:
            if scenario.fault == "checkpoint":
                try:
                    orchestration.approve_investigation(
                        started_response.investigation_id, "approved", reason="benchmark"
                    )
                    failures.append("approval succeeded with no checkpoint state")
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ != "InvestigationResumeError":
                        failures.append(
                            f"checkpoint failure raised {type(exc).__name__} "
                            "instead of InvestigationResumeError"
                        )
                investigation = service.get_investigation(started_response.investigation_id)
                final_status = investigation.workflow_status
                if final_status == "completed":
                    failures.append("missing checkpoint state still completed")
                if not investigation.last_error:
                    failures.append("resume failure did not record last_error")
            else:
                response = orchestration.approve_investigation(
                    started_response.investigation_id, "approved", reason="benchmark"
                )
                approval_status = response.approval_status
                final_status = response.workflow_status
                investigation = service.get_investigation(started_response.investigation_id)
                if scenario.fault == "approval":
                    try:
                        orchestration.approve_investigation(
                            started_response.investigation_id,
                            "approved",
                            reason="duplicate",
                        )
                        failures.append("duplicate approval was accepted")
                    except Exception as exc:  # noqa: BLE001
                        if type(exc).__name__ != "InvalidStateTransitionError":
                            failures.append(
                                f"duplicate approval raised {type(exc).__name__}"
                            )
                    approvals = service.list_approvals(
                        investigation_id=started_response.investigation_id
                    )
                    if len(approvals) != 1:
                        failures.append(
                            f"duplicate approval created {len(approvals)} approval rows"
                        )
                    investigation = service.get_investigation(
                        started_response.investigation_id
                    )
                    final_status = investigation.workflow_status

        if final_status != scenario.expect_final_status:
            failures.append(
                f"final workflow {final_status!r} != {scenario.expect_final_status!r}"
            )
        if scenario.expect_final_status == "failed":
            if investigation.status != "failed":
                failures.append("investigation status was not failed")
            if not investigation.last_error:
                failures.append("failure did not record a sanitized last_error")
            elif "Traceback" in investigation.last_error:
                failures.append("raw traceback leaked into last_error")
            if approval_status != "approved":
                failures.append("human decision was not preserved on failure")
        if scenario.expect_final_status == "completed":
            if investigation.status != "completed":
                failures.append("investigation did not persist as completed")
            if not (investigation.report or {}).get("valid"):
                failures.append("completed investigation report is not valid")
        if scenario.expect_final_status == "resume_failed":
            if investigation.status == "completed":
                failures.append("resume failure left the investigation completed")
            if investigation.status != "failed" and investigation.workflow_status != (
                "resume_failed"
            ):
                failures.append("resume failure was not classified")

        retained = len(evidence) > 0 and any(
            item.evidence_type == "knowledge_base" for item in evidence
        )
        if scenario.expect_evidence and not retained:
            failures.append("evidence was not retained")
        if not scenario.expect_evidence and retained:
            failures.append("evidence retained despite the injected retrieval failure")

        metrics = {
            "expected_classification": scenario.expected_classification,
            "report_valid": bool(observed_valid),
            "planner_status": planner_status,
            "planner_observable": planner_observable,
            "evidence_rows": len(evidence),
            "evidence_retained": retained,
            "tool_errors": len(state.get("tool_errors") or []),
            "planner_fallback_used": planner_status.startswith("fallback"),
            "evidence_expected": scenario.expect_evidence,
            "expected_completion": scenario.expect_final_status == "completed",
            "final_workflow_status": final_status,
            "final_status": investigation.status,
            "approval_status": approval_status,
            "false_completion": False,
            "classification_correct": not failures,
        }
        return CaseResult(
            case_id=scenario.case_id,
            suite="reliability",
            passed=not failures,
            metrics=metrics,
            failures=failures,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    finally:
        try:
            orchestration.close()
        except Exception:
            pass
        try:
            service.database.close()
        except Exception:
            pass
        shutil.rmtree(directory, ignore_errors=True)


def evaluate_reliability(
    scenarios: list[Scenario] | None = None,
    *,
    runtime: Any = None,
) -> SuiteResult:
    started = time.perf_counter()
    scenarios = scenarios or SCENARIOS
    owns_runtime = runtime is None
    runtime = runtime or build_retrieval_runtime("offline")
    try:
        results = [_run_scenario(scenario, runtime) for scenario in scenarios]
    finally:
        if owns_runtime:
            runtime.close()

    # Only rows that produced a report can be classified as report-failure rows;
    # scenarios that abort before report generation are classified by their own
    # expected exception instead of being silently counted as report failures.
    observed = [r for r in results if r.metrics.get("report_valid") is not None]
    failure_rows = [r for r in observed if not r.metrics["report_valid"]]
    non_llm_rows = [
        r for r in results if int(r.case_id.split("-")[1]) >= 9
    ]
    completed_failures = sum(
        1
        for r in failure_rows
        if r.metrics.get("final_status") == "completed"
        or r.metrics.get("final_workflow_status") == "completed"
    )
    metrics = {
        "scenarios": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "pass_rate": sum(1 for r in results if r.passed) / len(results) if results else 0.0,
        "correct_classification_rate": average(
            1.0 if r.metrics.get("classification_correct") else 0.0 for r in results
        ),
        "failed_report_scenarios": len(failure_rows),
        "failed_reports_completed_count": completed_failures,
        "non_llm_fault_scenarios": len(non_llm_rows),
        "non_llm_fault_pass_rate": average(
            1.0 if r.passed else 0.0 for r in non_llm_rows
        ),
        "false_completion_count": sum(
            1 for r in results if r.metrics.get("false_completion")
        ),
        "failed_report_blocking_rate": average(
            1.0
            if r.metrics.get("final_status") != "completed"
            and r.metrics.get("final_workflow_status") != "completed"
            else 0.0
            for r in failure_rows
        ),
        # Only scenarios that are supposed to complete belong in this rate; a
        # valid report that could not be resumed (REL-011) is not a success.
        "successful_investigation_rate": average(
            1.0 if r.metrics.get("final_workflow_status") == "completed" else 0.0
            for r in results
            if r.metrics.get("expected_completion")
        ),
        # Retention is only meaningful where evidence could exist at all; the
        # retrieval-failure scenario has nothing to retain by construction.
        "evidence_retention_rate": average(
            1.0 if r.metrics.get("evidence_retained") else 0.0
            for r in results
            if r.metrics.get("evidence_expected") is not False
            and r.metrics.get("evidence_retained") is not None
        ),
        "evidence_unavailable_scenarios": sum(
            1 for r in results if r.metrics.get("evidence_expected") is False
        ),
        "planner_degradation_observability_rate": average(
            1.0 if r.metrics.get("planner_observable") else 0.0
            for r in results
            if str(r.metrics.get("planner_status") or "").startswith("fallback")
        ),
        "recovery_failure_blocked": next(
            (
                r.metrics.get("final_workflow_status") == "failed"
                for r in results
                if r.case_id == "REL-008"
            ),
            None,
        ),
    }
    return SuiteResult(
        suite="reliability",
        mode="offline",
        dataset_version="reliability_scenarios_v2",
        cases=results,
        metrics=metrics,
        limitations=[
            "Providers are deterministic doubles; no live provider behavior is measured.",
            "Each scenario uses a fresh temporary SQLite app and checkpoint database.",
            "Failure classification is defined by this scenario list, not by an external "
            "ground-truth corpus.",
            "Faults are injected in-process (mocks and corrupted state), not by killing "
            "real infrastructure.",
        ],
        duration_ms=(time.perf_counter() - started) * 1000,
    )
