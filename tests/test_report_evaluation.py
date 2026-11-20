"""Deterministic report-validation matrix: schema, citation and support metrics."""

from __future__ import annotations

from backend.app.evaluation.dataset import load_reports
from backend.app.evaluation.report_evaluator import evaluate_reports


def test_report_dataset_covers_validation_outcomes() -> None:
    cases = load_reports()
    assert len(cases) == 9
    assert {case.dataset_version for case in cases} == {"reports_v1"}
    assert {case.expected_outcome for case in cases} == {
        "valid_grounded",
        "schema_failure",
        "citation_failure",
        "insufficient_evidence",
        "label_mismatch",
    }


def test_report_evaluation_separates_schema_citation_and_support() -> None:
    result = evaluate_reports()
    metrics = result.metrics

    assert metrics["cases"] == 9
    assert metrics["pass_rate"] == 1.0
    assert round(metrics["schema_valid_rate"], 4) == round(6 / 9, 4)
    assert round(metrics["citation_valid_rate"], 4) == round(4 / 9, 4)
    assert metrics["outcome_accuracy"] == 1.0
    assert metrics["evidence_supported_rate"] == 1.0
    assert metrics["outcome_counts"] == {
        "valid_grounded": 2,
        "citation_failure": 2,
        "schema_failure": 3,
        "insufficient_evidence": 1,
        "label_mismatch": 1,
    }
    assert metrics["citation_precision"] == 1.0
    assert metrics["citation_recall"] == 1.0
    assert metrics["recommendation_grounding_rate"] == 0.5
    assert metrics["malformed_payload_cases"] == 1


def test_invented_citation_is_rejected_while_schema_stays_valid() -> None:
    result = evaluate_reports()
    case = next(case for case in result.cases if case.case_id == "RPT-002")

    assert case.passed, case.failures
    assert case.metrics["schema_valid"] is True
    assert case.metrics["citation_valid"] is False
    assert case.metrics["outcome"] == "citation_failure"
    assert case.metrics["unknown_citations"]


def test_schema_valid_report_can_still_be_unsupported() -> None:
    result = evaluate_reports()
    case = next(case for case in result.cases if case.case_id == "RPT-006")

    assert case.passed, case.failures
    assert case.metrics["schema_valid"] is True
    assert case.metrics["citation_valid"] is True
    assert case.metrics["outcome"] == "label_mismatch"
    assert case.metrics["category"] == "network_failure"
