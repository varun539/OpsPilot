"""Deterministic report-validation metrics.

Scores candidate report payloads against a fixed evidence set and explicit
labels. This separates four different questions that are easy to conflate:

* schema validity (does the payload match ``InvestigationReport``),
* citation validity (does every cited chunk exist in the supplied evidence),
* evidence support (does the labeled category/abstention match the scenario),
* recommendation grounding (do recommended actions cite evidence).

It is not a semantic fact-checker: a report can be schema- and citation-valid
while still being wrong, which is exactly what ``label_mismatch`` captures.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.app.rag.models import RetrievedEvidence

from .dataset import load_reports
from .grounding import evaluate_grounding
from .metrics import average
from .models import CaseResult, ReportCase, SuiteResult

DEFAULT_EVIDENCE: list[RetrievedEvidence] = [
    RetrievedEvidence(
        chunk_id="KB-008::chunk::0000::tok::0000",
        doc_id="KB-008",
        title="Database Connection Pool Exhaustion Under Load",
        source_path="data/knowledge_base/KB-008.md",
        chunk_text="Pool exhaustion occurs when demand exceeds the configured limit.",
    ),
    RetrievedEvidence(
        chunk_id="KB-008::chunk::0001::tok::0000",
        doc_id="KB-008",
        title="Database Connection Pool Exhaustion Under Load",
        source_path="data/knowledge_base/KB-008.md",
        chunk_text="Causes include concurrency, connection retention and leaks.",
    ),
    RetrievedEvidence(
        chunk_id="KB-005::chunk::0000::tok::0001",
        doc_id="KB-005",
        title="API Request Timeouts and 504 Gateway Timeout Diagnosis",
        source_path="data/knowledge_base/KB-005.md",
        chunk_text="Pool checkout failures are often mapped to 500 errors.",
    ),
]


def _evidence(case: ReportCase) -> list[RetrievedEvidence]:
    return [] if case.evidence == "empty" else list(DEFAULT_EVIDENCE)


def _payload(case: ReportCase) -> tuple[dict[str, Any] | None, str | None]:
    if case.raw_report is not None:
        try:
            parsed = json.loads(case.raw_report)
        except json.JSONDecodeError as exc:
            return None, f"JSONDecodeError: {exc.msg}"
        return (parsed if isinstance(parsed, dict) else None), None
    return case.report, None


def _recommendation_grounding(payload: dict[str, Any] | None) -> tuple[int, int]:
    actions = (payload or {}).get("recommended_actions") or []
    grounded = 0
    for action in actions:
        if isinstance(action, dict) and action.get("supporting_chunk_ids"):
            grounded += 1
    return grounded, len(actions)


def _run_case(case: ReportCase) -> CaseResult:
    started = time.perf_counter()
    failures: list[str] = []
    payload, parse_error = _payload(case)
    evidence = _evidence(case)
    expected_abstain = case.expected_outcome == "insufficient_evidence"
    grounding = evaluate_grounding(
        payload,
        evidence,
        expected_doc_ids=case.expected_doc_ids,
        expected_chunk_ids=[],
        expected_category=case.expected_category,
        expected_abstain=expected_abstain,
    )
    schema_valid = bool(grounding["schema_valid"])
    # A payload that cannot be parsed has no meaningful citation validation.
    citation_valid = bool(grounding["citation_valid"]) and schema_valid
    abstained = bool(grounding["abstained"])
    category = str(grounding["category"] or "")
    grounded_actions, total_actions = _recommendation_grounding(payload)

    if not schema_valid:
        outcome = "schema_failure"
    elif not citation_valid:
        outcome = "citation_failure"
    elif expected_abstain and not abstained:
        outcome = "label_mismatch"
    elif category != case.expected_category:
        outcome = "label_mismatch"
    elif expected_abstain:
        outcome = "insufficient_evidence"
    else:
        outcome = "valid_grounded"

    if outcome != case.expected_outcome:
        failures.append(f"outcome {outcome!r} != {case.expected_outcome!r}")
    if case.raw_report is not None and parse_error and case.expected_outcome != "schema_failure":
        failures.append("malformed raw report was not expected")

    metrics = {
        "schema_valid": schema_valid,
        "citation_valid": citation_valid,
        "abstained": abstained,
        "category": category,
        "expected_category": case.expected_category,
        "outcome": outcome,
        "expected_outcome": case.expected_outcome,
        "citation_precision": grounding["citation_precision"],
        "citation_recall": grounding["citation_recall"],
        "cited_chunk_ids": grounding["cited_chunk_ids"],
        "unknown_citations": grounding["unknown_citations"],
        "grounded_recommendations": grounded_actions,
        "total_recommendations": total_actions,
        "parse_error": parse_error,
    }
    return CaseResult(
        case_id=case.case_id,
        suite="report",
        passed=not failures,
        metrics=metrics,
        failures=failures,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def evaluate_reports(cases: list[ReportCase] | None = None) -> SuiteResult:
    started = time.perf_counter()
    cases = cases or load_reports()
    results = [_run_case(case) for case in cases]

    grounded_rows = [
        r
        for r in results
        if r.metrics["expected_outcome"] not in {"insufficient_evidence", "schema_failure"}
        and r.metrics["expected_outcome"] != "citation_failure"
    ]
    actionable = [r for r in results if int(r.metrics["total_recommendations"]) > 0]
    outcome_counts: dict[str, int] = {}
    for row in results:
        key = str(row.metrics["outcome"])
        outcome_counts[key] = outcome_counts.get(key, 0) + 1

    metrics = {
        "cases": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "pass_rate": sum(1 for r in results if r.passed) / len(results) if results else 0.0,
        "schema_valid_rate": average(
            1.0 if r.metrics["schema_valid"] else 0.0 for r in results
        ),
        "citation_valid_rate": average(
            1.0 if r.metrics["citation_valid"] else 0.0 for r in results
        ),
        "evidence_supported_rate": average(
            1.0 if r.metrics["outcome"] == r.metrics["expected_outcome"] else 0.0
            for r in results
        ),
        "outcome_accuracy": average(
            1.0 if r.metrics["outcome"] == r.metrics["expected_outcome"] else 0.0
            for r in results
        ),
        "outcome_counts": outcome_counts,
        "citation_precision": average(
            float(r.metrics["citation_precision"]) for r in grounded_rows
        ),
        "citation_recall": average(
            float(r.metrics["citation_recall"]) for r in grounded_rows
        ),
        "recommendation_grounding_rate": average(
            1.0
            if int(r.metrics["grounded_recommendations"]) == int(r.metrics["total_recommendations"])
            else 0.0
            for r in actionable
        ),
        "malformed_payload_cases": sum(1 for r in results if r.metrics["parse_error"]),
    }
    return SuiteResult(
        suite="report",
        mode="offline",
        dataset_version=cases[0].dataset_version if cases else "",
        cases=results,
        metrics=metrics,
        limitations=[
            "Labels describe this fixed fixture set; they are not a general ground "
            "truth for model-written reports.",
            "Schema/citation validity is deterministic; factual correctness beyond the "
            "labeled category is not machine-scored.",
            "Semantic contradictions inside otherwise valid citations are not detected "
            "by this suite.",
        ],
        duration_ms=(time.perf_counter() - started) * 1000,
    )
