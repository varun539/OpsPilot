"""Evaluation of the six deterministic investigation tools.

The tools are executed directly with labeled synthetic inputs. No tool behavior
is modified by this module. Heuristic findings are measured as findings, never
as confirmed root causes.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.app.agent.tool_registry import get_tool_callable

from .dataset import load_diagnostics
from .metrics import finding_counts, precision_recall_f1
from .models import CaseResult, DiagnosticCase, SuiteResult


def _sanitize(text: str, secrets: list[str]) -> str:
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "<redacted>")
    return result


def _as_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    return {"value": result}


def _codes(payload: dict[str, Any]) -> set[str]:
    codes = {
        str(finding.get("code"))
        for finding in payload.get("findings", [])
        if isinstance(finding, dict) and finding.get("code")
    }
    return codes


def _evaluate_case(case: DiagnosticCase) -> CaseResult:
    started = time.perf_counter()
    failures: list[str] = []
    metrics: dict[str, Any] = {}
    func = get_tool_callable(case.tool)
    if func is None:
        failures.append(f"unknown tool {case.tool}")
        return CaseResult(
            case_id=case.case_id,
            suite="diagnostics",
            passed=False,
            failures=failures,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    payload: dict[str, Any] = {}
    error: Exception | None = None
    try:
        result = func(**case.inputs, **case.options)
        payload = _as_dict(result)
    except Exception as exc:  # expected for bound-enforcement cases
        error = exc

    metrics["expected_exception"] = case.expect_exception
    metrics["exception_raised"] = error is not None
    if case.expect_exception:
        if error is None:
            failures.append("expected an input error but the tool returned a result")
        else:
            metrics["exception_type"] = type(error).__name__
    elif error is not None:
        failures.append(f"unexpected {type(error).__name__}")

    if error is None:
        actual_codes = _codes(payload)
        groups = payload.get("correlation_groups") or payload.get("groups") or []
        status = str(payload.get("status", ""))
        metrics.update(
            {
                "status": status,
                "actual_finding_codes": sorted(actual_codes),
                "finding_count": len(actual_codes),
                "correlation_group_count": len(groups),
            }
        )
        if case.expected_status is not None and status != case.expected_status:
            failures.append(
                f"status {status!r} != expected {case.expected_status!r}"
            )
        missing = set(case.expected_finding_codes) - actual_codes
        if missing:
            failures.append(f"missing findings: {sorted(missing)}")
        forbidden = set(case.forbidden_finding_codes) & actual_codes
        if forbidden:
            failures.append(f"forbidden findings: {sorted(forbidden)}")
        if case.expect_no_findings and (actual_codes or groups):
            failures.append("expected no findings/groups")
        metrics["status_correct"] = (
            case.expected_status is None or status == case.expected_status
        )
        metrics["expected_findings_present"] = not missing
        metrics["no_false_findings"] = not forbidden and not (
            case.expect_no_findings and (actual_codes or groups)
        )
        serialized = json.dumps(payload, default=str, sort_keys=True)
        leaked = [secret for secret in case.secret_values if secret in serialized]
        if leaked:
            failures.append(f"{len(leaked)} secret value(s) were not redacted")
        metrics["secret_redaction"] = not leaked
        truncated = bool((payload.get("metadata") or {}).get("truncated")) or any(
            "truncat" in str(warning).lower() for warning in payload.get("warnings", [])
        )
        metrics["truncated_reported"] = truncated
        if case.expect_truncated and not truncated:
            failures.append("truncation was not reported")
        metrics["classification_correct"] = metrics["status_correct"]
    else:
        metrics["secret_redaction"] = True
        metrics["truncated_reported"] = False

    sanitized_failures = [_sanitize(failure, case.secret_values) for failure in failures]
    return CaseResult(
        case_id=case.case_id,
        suite="diagnostics",
        passed=not sanitized_failures,
        metrics=metrics,
        failures=sanitized_failures,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def evaluate_diagnostics(
    cases: list[DiagnosticCase] | None = None,
) -> SuiteResult:
    started = time.perf_counter()
    cases = cases if cases is not None else load_diagnostics()
    results = [_evaluate_case(case) for case in cases]

    expected_all: set[str] = set()
    actual_all: set[str] = set()
    for case, result in zip(cases, results, strict=True):
        expected_all.update(case.expected_finding_codes)
        actual_all.update(result.metrics.get("actual_finding_codes", []))
    counts = finding_counts(expected_all, actual_all)
    prf = precision_recall_f1(counts)
    status_rows = [
        result
        for result in results
        if "status_correct" in result.metrics
    ]
    secret_rows = [
        result for result in results if result.metrics.get("secret_redaction") is not None
    ]
    invalid_rows = [
        result
        for case, result in zip(cases, results, strict=True)
        if "invalid_input" in case.tags
    ]
    truncation_rows = [
        result for result in results if result.metrics.get("truncated_reported")
    ]
    negative_rows = [
        result
        for case, result in zip(cases, results, strict=True)
        if case.expect_no_findings
    ]
    metrics: dict[str, Any] = {
        "cases": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "pass_rate": (
            sum(1 for result in results if result.passed) / len(results)
            if results
            else 0.0
        ),
        "classification_accuracy": (
            sum(bool(row.metrics.get("status_correct")) for row in status_rows)
            / len(status_rows)
            if status_rows
            else 0.0
        ),
        "finding_true_positive": counts["true_positive"],
        "finding_false_positive": counts["false_positive"],
        "finding_false_negative": counts["false_negative"],
        "finding_precision": prf["precision"],
        "finding_recall": prf["recall"],
        "finding_f1": prf["f1"],
        "false_positive_rate": (
            sum(
                1
                for row in negative_rows
                if not row.metrics.get("no_false_findings", False)
            )
            / len(negative_rows)
            if negative_rows
            else 0.0
        ),
        "false_negative_rate": (
            counts["false_negative"]
            / (counts["false_negative"] + counts["true_positive"])
            if counts["false_negative"] + counts["true_positive"]
            else 0.0
        ),
        "secret_redaction_pass_rate": (
            sum(bool(row.metrics.get("secret_redaction")) for row in secret_rows)
            / len(secret_rows)
            if secret_rows
            else 1.0
        ),
        "invalid_input_pass_rate": (
            sum(1 for row in invalid_rows if row.passed) / len(invalid_rows)
            if invalid_rows
            else 1.0
        ),
        "truncation_reported": sum(
            1 for row in truncation_rows if row.metrics.get("truncated_reported")
        ),
        "tools_evaluated": sorted({case.tool for case in cases}),
    }
    return SuiteResult(
        suite="diagnostics",
        mode="offline",
        dataset_version=cases[0].dataset_version if cases else "",
        cases=results,
        metrics=metrics,
        limitations=[
            "Deterministic tool heuristics are measured against synthetic labels; "
            "no finding is treated as a confirmed root cause.",
            "False-positive/negative rates use labeled finding codes, not calibrated "
            "precision/recall over all possible real incidents.",
        ],
        duration_ms=(time.perf_counter() - started) * 1000,
    )
