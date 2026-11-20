"""Planner evaluation: allow-list boundary and observable deterministic fallback."""

from __future__ import annotations

from backend.app.evaluation.dataset import load_planner
from backend.app.evaluation.planner_evaluator import evaluate_planner


def test_planner_dataset_is_versioned_and_covers_failure_modes() -> None:
    scenarios = load_planner()
    assert len(scenarios) == 13
    assert {scenario.dataset_version for scenario in scenarios} == {"planner_v1"}
    kinds = {scenario.response_kind for scenario in scenarios}
    assert {"plan", "timeout", "refusal", "empty", "raw"} <= kinds
    assert any(scenario.expected_used for scenario in scenarios)
    assert any(scenario.expected_fallback for scenario in scenarios)


def test_planner_evaluation_meets_boundary_and_reliability_metrics() -> None:
    result = evaluate_planner()
    metrics = result.metrics

    assert metrics["scenarios"] == 13
    assert metrics["failed"] == 0
    assert metrics["pass_rate"] == 1.0
    assert metrics["valid_selection_rate"] == 1.0
    assert metrics["invalid_selection_rejection_rate"] == 1.0
    assert metrics["fallback_observability_rate"] == 1.0
    assert metrics["determinism_rate"] == 1.0
    assert metrics["interrupt_rate"] == 1.0
    assert metrics["unauthorized_tool_request_count"] == 2
    assert metrics["unauthorized_tool_execution_count"] == 0
    assert metrics["unauthorized_tool_in_state_count"] == 0
    assert metrics["planner_call_count"] == 13


def test_unknown_tool_never_executes_and_falls_back_observably() -> None:
    result = evaluate_planner()
    case = next(case for case in result.cases if case.case_id == "PLN-004")

    assert case.passed, case.failures
    assert case.metrics["planner_used"] is False
    assert case.metrics["fallback_used"] is True
    assert case.metrics["selected_tools"] == ["log_analyzer"]
    assert "shell_executor" not in case.metrics["executed_tools"]
    assert "unknown tool selected" in case.metrics["planner_error"]
    assert case.metrics["observable"] is True


def test_planner_timeout_is_recorded_and_bounded() -> None:
    result = evaluate_planner()
    case = next(case for case in result.cases if case.case_id == "PLN-012")

    assert case.passed, case.failures
    assert case.metrics["error_kind"] == "timeout"
    assert case.metrics["selected_tools"] == ["log_analyzer"]
    assert case.metrics["deterministic"] is True
