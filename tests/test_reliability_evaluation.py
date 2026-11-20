"""Reliability benchmark: failed reports must never complete as investigations."""

from __future__ import annotations

from backend.app.evaluation.reliability_evaluator import SCENARIOS, evaluate_reliability


def test_reliability_scenarios_cover_required_failure_modes() -> None:
    report_modes = {scenario.report_mode for scenario in SCENARIOS}
    planner_modes = {scenario.planner_mode for scenario in SCENARIOS}
    faults = {scenario.fault for scenario in SCENARIOS}
    assert len(SCENARIOS) == 17
    assert {"success", "http_503", "malformed", "empty", "refusal"} <= report_modes
    assert {"ok", "timeout", "invalid_selection"} <= planner_modes
    assert any(scenario.restart_before_approval for scenario in SCENARIOS)
    # Phase 23 non-LLM fault coverage.
    assert {
        "retrieval",
        "tool",
        "checkpoint",
        "persistence",
        "input",
        "serialization",
        "approval",
        "graph",
    } <= faults


def test_reliability_benchmark_blocks_failed_reports() -> None:
    result = evaluate_reliability()
    metrics = result.metrics

    assert metrics["scenarios"] == 17
    assert metrics["failed"] == 0
    assert metrics["pass_rate"] == 1.0
    assert metrics["correct_classification_rate"] == 1.0
    assert metrics["failed_report_scenarios"] == 6
    assert metrics["failed_reports_completed_count"] == 0
    assert metrics["failed_report_blocking_rate"] == 1.0
    assert metrics["successful_investigation_rate"] == 1.0
    assert metrics["evidence_retention_rate"] == 1.0
    assert metrics["planner_degradation_observability_rate"] == 1.0
    assert metrics["recovery_failure_blocked"] is True
    assert metrics["non_llm_fault_scenarios"] == 9
    assert metrics["non_llm_fault_pass_rate"] == 1.0
    assert metrics["false_completion_count"] == 0
    assert metrics["evidence_unavailable_scenarios"] == 1


def test_planner_timeout_still_allows_a_valid_report() -> None:
    result = evaluate_reliability()
    case = next(case for case in result.cases if case.case_id == "REL-002")

    assert case.passed, case.failures
    assert case.metrics["planner_fallback_used"] is True
    assert case.metrics["planner_observable"] is True
    assert case.metrics["report_valid"] is True
    assert case.metrics["final_workflow_status"] == "completed"


def test_non_llm_faults_never_report_a_false_completion() -> None:
    result = evaluate_reliability()
    non_llm = [c for c in result.cases if int(c.case_id.split("-")[1]) >= 9]

    assert len(non_llm) == 9
    for case in non_llm:
        assert case.passed, (case.case_id, case.failures)
        # No scenario may claim completion when its failure was injected.
        assert case.metrics.get("final_workflow_status") != "completed" or case.metrics.get(
            "expected_completion"
        ), case.case_id
        assert case.metrics.get("false_completion") is False


def test_retrieval_failure_degrades_without_crashing() -> None:
    result = evaluate_reliability()
    case = next(case for case in result.cases if case.case_id == "REL-009")

    assert case.passed, case.failures
    assert case.metrics["evidence_retained"] is False
    assert case.metrics["report_valid"] is True
    # An insufficient-evidence report is still a report: it is not a success story.
    assert case.metrics["final_status"] == "completed"


def test_diagnostic_tool_failure_is_recorded() -> None:
    result = evaluate_reliability()
    case = next(case for case in result.cases if case.case_id == "REL-010")

    assert case.passed, case.failures
    assert case.metrics["tool_errors"] >= 1


def test_malformed_input_is_rejected_before_persistence() -> None:
    result = evaluate_reliability()
    case = next(case for case in result.cases if case.case_id == "REL-013")

    assert case.passed, case.failures
    assert case.metrics["raised"] == "ValidationError"


def test_missing_checkpoint_binding_never_completes() -> None:
    result = evaluate_reliability()
    case = next(case for case in result.cases if case.case_id == "REL-011")

    assert case.passed, case.failures
    assert case.metrics["final_workflow_status"] == "resume_failed"
    assert case.metrics["final_status"] != "completed"


def test_invalid_planner_selection_is_observable_and_blocked() -> None:
    result = evaluate_reliability()
    case = next(case for case in result.cases if case.case_id == "REL-017")

    assert case.passed, case.failures
    assert case.metrics["planner_status"] == "fallback_invalid_selection"
    assert case.metrics["planner_observable"] is True


def test_approval_after_failure_preserves_decision_but_fails() -> None:
    result = evaluate_reliability()
    case = next(case for case in result.cases if case.case_id == "REL-004")

    assert case.passed, case.failures
    assert case.metrics["approval_status"] == "approved"
    assert case.metrics["final_workflow_status"] == "failed"
    assert case.metrics["final_status"] == "failed"
