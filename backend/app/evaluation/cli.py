"""Standalone evaluation CLI for OpsPilot Phase 16.

Provides `python -m backend.app.evaluation.cli run` with suites/modes/output options.
Defaults to offline mode. Exits nonzero when regression gates fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from backend.app.evaluation.abstention_evaluator import evaluate_abstention
from backend.app.evaluation.agent_evaluator import evaluate_agent
from backend.app.evaluation.dataset import dataset_counts, load_diagnostics, load_incidents
from backend.app.evaluation.diagnostic_evaluator import evaluate_diagnostics
from backend.app.evaluation.planner_evaluator import evaluate_planner
from backend.app.evaluation.reliability_evaluator import evaluate_reliability
from backend.app.evaluation.report_evaluator import evaluate_reports
from backend.app.evaluation.retrieval_evaluator import build_retrieval_runtime, evaluate_retrieval


def _format_metrics(metrics):
    lines = []
    for key, value in sorted(metrics.items()):
        if isinstance(value, float):
            lines.append(f"  {key}: {value:.4f}")
        elif isinstance(value, int):
            lines.append(f"  {key}: {value}")
        elif isinstance(value, bool):
            lines.append(f"  {key}: {value}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _run_diagnostic_suite():
    from backend.app.evaluation.dataset import load_diagnostics
    cases = load_diagnostics()
    results = evaluate_diagnostics(cases)
    return {
        "suite": "diagnostics",
        "mode": "offline",
        "cases": dataset_counts().get("diagnostics", 0),
        "passed": results.metrics["passed"],
        "failed": results.metrics["failed"],
        "pass_rate": results.metrics["pass_rate"],
        "finding_precision": results.metrics["finding_precision"],
        "finding_recall": results.metrics["finding_recall"],
        "finding_f1": results.metrics["finding_f1"],
        "false_positive_rate": results.metrics["false_positive_rate"],
        "false_negative_rate": results.metrics["false_negative_rate"],
        "secret_redaction_pass_rate": results.metrics["secret_redaction_pass_rate"],
        "invalid_input_pass_rate": results.metrics["invalid_input_pass_rate"],
        "details": [_format_metrics(r.metrics) for r in results.cases],
    }


def _run_retrieval_suite(mode):
    runtime = build_retrieval_runtime(mode)
    try:
        results = evaluate_retrieval(mode=mode, runtime=runtime)
    finally:
        runtime.close()
    if isinstance(results, dict):
        return results
    metrics = getattr(results, "metrics", {}) or {}
    metrics = metrics if isinstance(metrics, dict) else {}
    methods = metrics.get("methods", {})
    cases = getattr(results, "cases", []) or []
    return {
        "suite": "retrieval",
        "mode": mode,
        "queries": dataset_counts().get("retrieval", 0),
        "cases": len(cases),
        "passed": results.passed if hasattr(results, "passed") else 0,
        "failed": results.failed if hasattr(results, "failed") else 0,
        "pass_rate": (results.passed / len(cases)) if cases else 1.0,
        # Per-method metrics. Every number the retrieval requirements ask for is
        # kept here so the generated report does not silently drop precision,
        # nDCG, MRR, diversity or the abstention group.
        "methods": methods,
        "gates": metrics.get("gates", {}),
        "mode_label": metrics.get("mode_label", mode),
        "empty_collection_ok": metrics.get("empty_collection_ok"),
        "metadata_filter_checks": metrics.get("metadata_filter_checks", 0),
        "metadata_filter_passes": metrics.get("metadata_filter_passes", 0),
        "recall_5": methods.get("hybrid", {}).get("recall@5", 0.0),
        "bm25_recall_5": methods.get("bm25", {}).get("recall@5", 0.0),
        "hybrid_recall_5": methods.get("hybrid", {}).get("recall@5", 0.0),
        "details": [],
    }


def _run_abstention_suite(mode):
    runtime = build_retrieval_runtime(mode)
    try:
        results = evaluate_abstention(mode=mode, runtime=runtime)
    finally:
        runtime.close()
    metrics = results.metrics
    hybrid = (metrics.get("methods") or {}).get("hybrid", {})
    return {
        "suite": "abstention",
        "mode": mode,
        "cases": len(results.cases),
        "passed": results.passed,
        "failed": results.failed,
        "pass_rate": (
            results.passed / len(results.cases) if results.cases else 1.0
        ),
        "stage": metrics.get("stage"),
        "labeled_queries": metrics.get("labeled_queries"),
        "no_evidence_queries": metrics.get("no_evidence_queries"),
        "category_counts": metrics.get("category_counts"),
        "methods": metrics.get("methods"),
        "threshold_sweeps": metrics.get("threshold_sweeps"),
        "threshold_sweep_interpretable": metrics.get("threshold_sweep_interpretable"),
        "recommended_threshold": metrics.get("recommended_threshold"),
        "empty_corpus_abstains": metrics.get("empty_corpus_abstains"),
        "hybrid_false_answer_rate": hybrid.get("false_answer_rate"),
        "hybrid_false_abstention_rate": hybrid.get("false_abstention_rate"),
        "details": [_format_metrics(r.metrics) for r in results.cases],
    }


def _run_agent_suite():
    from backend.app.evaluation.dataset import load_incidents
    cases = load_incidents()
    runtime = None
    try:
        results = evaluate_agent(cases, mode="offline", runtime=runtime)
    except Exception as e:
        print(f"Agent evaluation error: {e}", file=sys.stderr)
        results = type(
            "obj",
            (object,),
            {"passed": 0, "failed": len(cases), "metrics": {}, "cases": [], "dataset_version": ""},
        )()
    finally:
        pass
    if isinstance(results, dict):
        return results
    metrics = results.metrics
    return {
        "suite": "agent",
        "mode": "offline",
        "cases": len(results.cases) if hasattr(results, "cases") else 0,
        "passed": results.passed if hasattr(results, "passed") else 0,
        "failed": results.failed if hasattr(results, "failed") else 0,
        "pass_rate": metrics.get("pass_rate", 0.0),
        "tool_selection_accuracy": metrics.get("tool_selection_accuracy", 0.0),
        "completion_rate": metrics.get("completion_rate", 0.0),
        "report_validity_rate": metrics.get("report_validity_rate", 0.0),
        "root_cause_category_accuracy": metrics.get("root_cause_category_accuracy", 0.0),
        "finding_coverage": metrics.get("finding_coverage", 0.0),
        "citation_precision": metrics.get("citation_precision", 0.0),
        "citation_recall": metrics.get("citation_recall", 0.0),
        "citation_integrity_rate": metrics.get("citation_integrity_rate", 0.0),
        "abstention_accuracy": metrics.get("abstention_accuracy", 0.0),
        "abstention_precision": metrics.get("abstention_precision", 0.0),
        "abstention_recall": metrics.get("abstention_recall", 0.0),
        "workflow_success_rate": metrics.get("workflow_success_rate", 0.0),
        "interrupt_success_rate": metrics.get("interrupt_success_rate", 0.0),
        "resume_success_rate": metrics.get("resume_success_rate", 0.0),
        "unnecessary_tool_call_rate": metrics.get("unnecessary_tool_call_rate", 0.0),
        "security_violations": metrics.get("security_violations", 0),
        "details": [_format_metrics(r.metrics) for r in getattr(results, "cases", [])],
    }


def _run_planner_suite():
    results = evaluate_planner()
    metrics = results.metrics
    return {
        "suite": "planner",
        "mode": results.mode,
        "cases": metrics["scenarios"],
        "passed": metrics["passed"],
        "failed": metrics["failed"],
        "pass_rate": metrics["pass_rate"],
        "valid_selection_rate": metrics["valid_selection_rate"],
        "invalid_selection_rejection_rate": metrics[
            "invalid_selection_rejection_rate"
        ],
        "fallback_activation_rate": metrics["fallback_activation_rate"],
        "fallback_observability_rate": metrics["fallback_observability_rate"],
        "determinism_rate": metrics["determinism_rate"],
        "unauthorized_tool_request_count": metrics[
            "unauthorized_tool_request_count"
        ],
        "unauthorized_tool_execution_count": metrics[
            "unauthorized_tool_execution_count"
        ],
        "details": [_format_metrics(r.metrics) for r in results.cases],
    }


def _run_report_suite():
    results = evaluate_reports()
    metrics = results.metrics
    return {
        "suite": "report",
        "mode": results.mode,
        "cases": metrics["cases"],
        "passed": metrics["passed"],
        "failed": metrics["failed"],
        "pass_rate": metrics["pass_rate"],
        "schema_valid_rate": metrics["schema_valid_rate"],
        "citation_valid_rate": metrics["citation_valid_rate"],
        "evidence_supported_rate": metrics["evidence_supported_rate"],
        "outcome_accuracy": metrics["outcome_accuracy"],
        "citation_precision": metrics["citation_precision"],
        "citation_recall": metrics["citation_recall"],
        "recommendation_grounding_rate": metrics[
            "recommendation_grounding_rate"
        ],
        "details": [_format_metrics(r.metrics) for r in results.cases],
    }


def _run_reliability_suite():
    results = evaluate_reliability()
    metrics = results.metrics
    return {
        "suite": "reliability",
        "mode": results.mode,
        "cases": metrics["scenarios"],
        "passed": metrics["passed"],
        "failed": metrics["failed"],
        "pass_rate": metrics["pass_rate"],
        "correct_classification_rate": metrics["correct_classification_rate"],
        "failed_report_scenarios": metrics["failed_report_scenarios"],
        "failed_reports_completed_count": metrics[
            "failed_reports_completed_count"
        ],
        "non_llm_fault_scenarios": metrics["non_llm_fault_scenarios"],
        "non_llm_fault_pass_rate": metrics["non_llm_fault_pass_rate"],
        "false_completion_count": metrics["false_completion_count"],
        "failed_report_blocking_rate": metrics["failed_report_blocking_rate"],
        "successful_investigation_rate": metrics[
            "successful_investigation_rate"
        ],
        "evidence_retention_rate": metrics["evidence_retention_rate"],
        "planner_degradation_observability_rate": metrics[
            "planner_degradation_observability_rate"
        ],
        "details": [_format_metrics(r.metrics) for r in results.cases],
    }


def _run_live_planner_suite(max_requests: int | None = None):
    """Bounded live planner evaluation. Makes no request unless opted in."""
    from backend.app.evaluation.live_planner_evaluator import (
        evaluate_live_planner,
    )

    results = evaluate_live_planner(max_requests=max_requests)
    metrics = results.metrics
    return {
        "suite": "live-planner",
        "mode": "live",
        "status": metrics.get("status", "skipped"),
        "skip_reason": metrics.get("skip_reason"),
        "cases": len(results.cases),
        "passed": results.passed,
        "failed": results.failed,
        "pass_rate": (results.passed / len(results.cases)) if results.cases else 0.0,
        "requests_made": metrics.get("requests_made", 0),
        "request_limit": metrics.get("request_limit"),
        "tools_executed": metrics.get("tools_executed", 0),
        "reports_generated": metrics.get("reports_generated", 0),
        "model": metrics.get("model"),
        "designed_model": metrics.get("designed_model"),
        "valid_selection_rate": metrics.get("valid_selection_rate"),
        "invalid_selection_count": metrics.get("invalid_selection_count"),
        "expected_tool_match_rate": metrics.get("expected_tool_match_rate"),
        "fallback_activation_rate": metrics.get("fallback_activation_rate"),
        "error_classifications": metrics.get("error_classifications"),
        "consistency_rate": metrics.get("consistency_rate"),
        "latency_ms_avg": metrics.get("latency_ms_avg"),
        "limitations": results.limitations,
        "details": [_format_metrics(r.metrics) for r in results.cases],
    }


def _suite_gate(result: dict, *, require_abstention: bool) -> dict:
    """Decide whether one suite blocks the run, and why.

    Retrieval is gated by its explicit gate objects rather than by raw case
    count, because a no-evidence query returning candidates is an advisory
    abstention finding, not a ranking regression. Every other suite blocks on
    any failed case.
    """
    suite = result.get("suite")
    if suite == "live-planner" and result.get("status") != "executed":
        # A skipped live run is not a pass and not a failure: it made no request.
        return {
            "suite": suite,
            "status": "skipped",
            "blocking_failures": [],
            "advisory_failures": [],
        }
    if suite == "retrieval":
        blocking: list[str] = []
        advisory: list[str] = []
        for name, gate in (result.get("gates") or {}).items():
            if gate.get("status") != "fail":
                continue
            if gate.get("blocking") or (name == "abstention" and require_abstention):
                blocking.append(name)
            else:
                advisory.append(name)
        return {
            "suite": suite,
            "status": "fail" if blocking else "pass",
            "blocking_failures": blocking,
            "advisory_failures": advisory,
        }
    failed = int(result.get("failed", 0) or 0)
    if not failed:
        return {
            "suite": suite,
            "status": "pass",
            "blocking_failures": [],
            "advisory_failures": [],
        }
    # The abstention suite measures candidate presence, not a calibrated
    # threshold, so it is advisory unless the caller opts into blocking.
    if suite == "abstention" and not require_abstention:
        return {
            "suite": suite,
            "status": "pass",
            "blocking_failures": [],
            "advisory_failures": [f"{failed} unanswerable queries returned candidates"],
        }
    return {
        "suite": suite,
        "status": "fail",
        "blocking_failures": [f"{failed} failed case(s)"],
        "advisory_failures": [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="OpsPilot Phase 16 evaluation runner.",
    )
    # Documented commands (README, docs, module docstring) include a ``run``
    # subcommand. It is accepted here so the documented invocations work; the
    # bare form without ``run`` stays supported.
    parser.add_argument(
        "command",
        nargs="?",
        choices=["run"],
        default=None,
        metavar="run",
        help="Optional compatibility subcommand (documented commands use 'run').",
    )
    parser.add_argument(
        "--suite",
        choices=[
            "diagnostics",
            "retrieval",
            "abstention",
            "agent",
            "planner",
            "report",
            "reliability",
            "live-planner",
            "all",
        ],
        default="all",
        help=(
            "Evaluation suite to run. 'live-planner' is excluded from 'all' and "
            "requires --live-planner-opt-in plus "
            "OPSPILOT_LIVE_PLANNER_EVAL=1."
        ),
    )
    parser.add_argument(
        "--live-planner-opt-in",
        action="store_true",
        help=(
            "Acknowledge that the live planner suite sends real requests to the "
            "configured provider. Required for --suite live-planner."
        ),
    )
    parser.add_argument(
        "--live-planner-max-requests",
        type=int,
        default=None,
        help=(
            "Hard cap on live planner calls (default 3, maximum 5). No retries are "
            "made."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "local", "live"],
        default="offline",
        help="Retrieval/LLM execution mode (default: offline).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Directory for report output (default: evaluation-results/).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Path to baseline JSON file for regression comparison.",
    )
    parser.add_argument(
        "--require-abstention",
        action="store_true",
        help=(
            "Make the retrieval abstention gate blocking. Off by default because "
            "no-evidence candidate presence is measured, not thresholded."
        ),
    )
    args = parser.parse_args(argv)

    output_dir = args.output or Path("evaluation-results")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.output:
        print(
            "Note: reports written to ./evaluation-results/; add --output PATH to specify "
            "a different directory. Do not commit generated reports containing sensitive data.",
            file=sys.stderr,
        )

    incidents = load_incidents()
    load_diagnostics()

    all_results = []
    summary = {
        "evaluation_timestamp": datetime.now().isoformat(),
        "dataset_version": incidents[0].dataset_version if incidents else "",
        "mode": args.mode,
    }

    if args.suite in ("diagnostics", "all"):
        result = _run_diagnostic_suite()
        all_results.append(result)
        summary["diagnostics"] = result

    if args.suite in ("retrieval", "all"):
        result = _run_retrieval_suite(args.mode)
        all_results.append(result)
        summary["retrieval"] = result

    if args.suite in ("abstention", "all"):
        result = _run_abstention_suite(args.mode)
        all_results.append(result)
        summary["abstention"] = result

    if args.suite in ("planner", "all"):
        result = _run_planner_suite()
        all_results.append(result)
        summary["planner"] = result

    if args.suite in ("report", "all"):
        result = _run_report_suite()
        all_results.append(result)
        summary["report"] = result

    if args.suite in ("agent", "all"):
        result = _run_agent_suite()
        all_results.append(result)
        summary["agent"] = result

    if args.suite in ("reliability", "all"):
        result = _run_reliability_suite()
        all_results.append(result)
        summary["reliability"] = result

    # Deliberately not part of ``all``: the live suite is the only path that may
    # spend provider quota, so it needs its own explicit selection plus the
    # acknowledgement flag below.
    if args.suite == "live-planner":
        if not args.live_planner_opt_in:
            print(
                "ERROR: --suite live-planner requires --live-planner-opt-in. "
                "No request was made.",
                file=sys.stderr,
            )
            return 2
        result = _run_live_planner_suite(args.live_planner_max_requests)
        all_results.append(result)
        summary["live-planner"] = result

    total_passed = sum(r.get("passed", 0) for r in all_results)
    total_failed = sum(r.get("failed", 0) for r in all_results)
    total_cases = sum(r.get("cases", 0) for r in all_results)
    strict_pass_rate = total_passed / total_cases if total_cases else 1.0

    suite_gates = [
        _suite_gate(result, require_abstention=args.require_abstention)
        for result in all_results
    ]
    failing = [gate["suite"] for gate in suite_gates if gate["status"] == "fail"]
    advisory = [
        (gate["suite"], name)
        for gate in suite_gates
        for name in gate["advisory_failures"]
    ]

    summary["overall"] = {
        "total_cases": total_cases,
        "total_passed": total_passed,
        "total_failed": total_failed,
        # Strict case success counts every advisory finding as a miss. It is a
        # description of the corpus, not the CI decision.
        "strict_case_pass_rate": strict_pass_rate,
        "gate_status": "fail" if failing else "pass",
        "failing_suites": failing,
        "advisory_findings": [f"{suite}:{name}" for suite, name in advisory],
        "abstention_blocking": bool(args.require_abstention),
        "suite_gates": suite_gates,
    }

    json_path = output_dir / "evaluation.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": all_results}, f, indent=2, default=str)

    md_path = output_dir / "evaluation.md"
    md_lines = []
    md_lines.append("# OpsPilot Phase 16 Evaluation Report")
    md_lines.append(f"*Generated: {summary['evaluation_timestamp']}*")
    md_lines.append(f"*Mode: {summary['mode']}*")
    md_lines.append("")

    for result in all_results:
        md_lines.append(f"## Suite: {result['suite']} (mode={result['mode']})")
        md_lines.append(f"- Cases: {result.get('cases', '?')}")
        md_lines.append(f"- Passed: {result.get('passed', '?')}")
        md_lines.append(f"- Failed: {result.get('failed', '?')}")
        md_lines.append(f"- Pass rate: {result.get('pass_rate', '?')}")
        if "recall_5" in result:
            md_lines.append(f"- Recall@5: {result['recall_5']:.4f}")
            md_lines.append(f"- BM25 Recall@5: {result['bm25_recall_5']:.4f}")
            md_lines.append(f"- Hybrid Recall@5: {result['hybrid_recall_5']:.4f}")
        md_lines.append("")
        if result.get("methods"):
            md_lines.append("### Retrieval metrics by method")
            md_lines.append(
                "| Method | Recall@5 | Precision@5 | nDCG@5 | MRR@5 | "
                "Doc coverage@5 | Distinct docs@5 | Max chunks/doc | Chunk concentration |"
            )
            md_lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for name, row in result["methods"].items():
                md_lines.append(
                    "| {name} | {r5:.4f} | {p5:.4f} | {n5:.4f} | {mrr:.4f} | "
                    "{cov:.4f} | {docs:.4f} | {chunks:.4f} | {conc:.4f} |".format(
                        name=name,
                        r5=float(row.get("recall@5", 0.0)),
                        p5=float(row.get("precision@5", 0.0)),
                        n5=float(row.get("ndcg@5", 0.0)),
                        mrr=float(row.get("mrr@5", 0.0)),
                        cov=float(row.get("relevant_doc_coverage@5", 0.0)),
                        docs=float(row.get("distinct_documents@5", 0.0)),
                        chunks=float(row.get("max_chunks_per_document@5", 0.0)),
                        conc=float(row.get("chunk_concentration@5", 0.0)),
                    )
                )
            md_lines.append("")
            md_lines.append("### Abstention group (no-evidence queries, kept separate)")
            md_lines.append(
                "| Method | No-evidence queries | Returned results | Clean rate |"
            )
            md_lines.append("| --- | --- | --- | --- |")
            for name, row in result["methods"].items():
                md_lines.append(
                    "| {name} | {total} | {hit} | {rate:.4f} |".format(
                        name=name,
                        total=row.get("abstention_queries", 0),
                        hit=row.get("abstention_returned_results", 0),
                        rate=float(row.get("abstention_clean_rate", 0.0)),
                    )
                )
            md_lines.append("")
        if result.get("suite") == "abstention" and result.get("methods"):
            md_lines.append(f"### Abstention stage: `{result.get('stage')}`")
            md_lines.append(
                f"- Answerable queries: {result.get('labeled_queries')}"
                f" · unanswerable queries: {result.get('no_evidence_queries')}"
            )
            md_lines.append(
                "- Predicted abstention = the engine returned zero candidates; "
                "no score threshold is applied at this operating point."
            )
            md_lines.append("")
            md_lines.append(
                "| Method | Abstention precision | Abstention recall | F1 | "
                "False-answer rate | False-abstention rate | Accuracy |"
            )
            md_lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for name, row in result["methods"].items():
                md_lines.append(
                    "| {name} | {p:.4f} | {r:.4f} | {f1:.4f} | "
                    "{fa:.4f} | {fb:.4f} | {acc:.4f} |".format(
                        name=name,
                        p=float(row.get("abstention_precision", 0.0)),
                        r=float(row.get("abstention_recall", 0.0)),
                        f1=float(row.get("abstention_f1", 0.0)),
                        fa=float(row.get("false_answer_rate", 0.0)),
                        fb=float(row.get("false_abstention_rate", 0.0)),
                        acc=float(row.get("abstention_accuracy", 0.0)),
                    )
                )
            md_lines.append("")
            sweep = (result.get("threshold_sweeps") or {}).get("semantic") or []
            if sweep:
                md_lines.append("### Semantic similarity threshold sweep")
                md_lines.append(
                    "Abstain when `top similarity < threshold`. "
                    f"Interpretable in this mode: {result.get('threshold_sweep_interpretable')}."
                )
                md_lines.append(
                    "| Threshold | Precision | Recall | False-answer | "
                    "False-abstention | Accuracy |"
                )
                md_lines.append("| --- | --- | --- | --- | --- | --- |")
                for row in sweep:
                    md_lines.append(
                        "| {t:.2f} | {p:.3f} | {r:.3f} | {fa:.3f} | {fb:.3f} | {acc:.3f} |".format(
                            t=float(row["threshold"]),
                            p=float(row["abstention_precision"]),
                            r=float(row["abstention_recall"]),
                            fa=float(row["false_answer_rate"]),
                            fb=float(row["false_abstention_rate"]),
                            acc=float(row["abstention_accuracy"]),
                        )
                    )
                md_lines.append("")
                recommended = result.get("recommended_threshold")
                if recommended:
                    md_lines.append(
                        "Best-F1 point: threshold {t:.2f} (precision {p:.3f}, recall "
                        "{r:.3f}, false-answer {fa:.3f}, false-abstention {fb:.3f}). "
                        "**Experimental only - not applied to the product.**".format(
                            t=float(recommended["threshold"]),
                            p=float(recommended["abstention_precision"]),
                            r=float(recommended["abstention_recall"]),
                            fa=float(recommended["false_answer_rate"]),
                            fb=float(recommended["false_abstention_rate"]),
                        )
                    )
                    md_lines.append("")
        if result.get("suite") == "live-planner":
            md_lines.append("### Live planner evaluation (opt-in)")
            md_lines.append(f"- Status: **{result.get('status')}**")
            if result.get("skip_reason"):
                md_lines.append(f"- Skipped because: {result['skip_reason']}")
            md_lines.append(
                f"- Requests made: {result.get('requests_made')} "
                f"(limit {result.get('request_limit')}, sequential, no retries)"
            )
            md_lines.append(
                f"- Tools executed: {result.get('tools_executed')} · "
                f"reports generated: {result.get('reports_generated')}"
            )
            if result.get("status") == "executed":
                md_lines.append(
                    f"- Model: `{result.get('model')}` "
                    f"(designed for `{result.get('designed_model')}`)"
                )
                md_lines.append(
                    "- Valid selection rate: {v} · invalid selections: {i} · "
                    "expected-tool match: {m} · fallback activation: {f} · "
                    "consistency: {c} · mean latency: {l} ms".format(
                        v=result.get("valid_selection_rate"),
                        i=result.get("invalid_selection_count"),
                        m=result.get("expected_tool_match_rate"),
                        f=result.get("fallback_activation_rate"),
                        c=result.get("consistency_rate"),
                        l=(
                            round(float(result["latency_ms_avg"]), 1)
                            if result.get("latency_ms_avg") is not None
                            else None
                        ),
                    )
                )
                md_lines.append(
                    f"- Error classifications: {result.get('error_classifications')}"
                )
            for note in result.get("limitations") or []:
                md_lines.append(f"- Limitation: {note}")
            md_lines.append("")
        md_lines.append("### Metrics")
        md_lines.append(_format_metrics(result))
        md_lines.append("")

    md_lines.append("## Gates")
    overall = summary.get("overall", {})
    md_lines.append(f"- Gate status: **{overall.get('gate_status', '?')}**")
    md_lines.append(
        f"- Abstention gate blocking: {overall.get('abstention_blocking', False)}"
    )
    for gate in overall.get("suite_gates", []):
        md_lines.append(
            f"- {gate['suite']}: {gate['status']}"
            + (
                f" (blocking: {', '.join(gate['blocking_failures'])})"
                if gate["blocking_failures"]
                else ""
            )
            + (
                f" (advisory: {', '.join(gate['advisory_failures'])})"
                if gate["advisory_failures"]
                else ""
            )
        )
    md_lines.append("")

    md_lines.append("## Overall Results")
    md_lines.append(f"- Total cases: {overall.get('total_cases', '?')}")
    md_lines.append(f"- Total passed: {overall.get('total_passed', '?')}")
    md_lines.append(f"- Total failed: {overall.get('total_failed', '?')}")
    md_lines.append(
        f"- Strict case pass rate: {overall.get('strict_case_pass_rate', 0.0):.4f}"
        " (counts advisory findings as misses; not the CI decision)"
    )
    md_lines.append("")

    if result_gates := summary.get("retrieval", {}).get("gates"):
        md_lines.append("## Retrieval gate detail")
        md_lines.append("| Gate | Status | Passed | Total | Blocking |")
        md_lines.append("| --- | --- | --- | --- | --- |")
        for name, gate in result_gates.items():
            md_lines.append(
                "| {name} | {status} | {passed} | {total} | {blocking} |".format(
                    name=name,
                    status=gate.get("status"),
                    passed=gate.get("passed"),
                    total=gate.get("total"),
                    blocking=gate.get("blocking"),
                )
            )
        md_lines.append("")

    md_lines.append("## Limitations")
    md_lines.append(
        "- Default suite is fully offline; no API keys or external LLM requests."
    )
    md_lines.append(
        "- The live planner suite is excluded from --suite all and requires both "
        "--live-planner-opt-in and OPSPILOT_LIVE_PLANNER_EVAL=1; it makes at most "
        "5 sequential calls with no retries and executes no tools."
    )
    md_lines.append(
        "- The live planner suite has not been run in this environment: Gemini "
        "free-tier/billing status could not be established, so it stays disabled."
    )
    md_lines.append(
        "- Live LLM evaluation requires explicit --mode live and valid API keys."
    )
    md_lines.append(
        "- Agent evaluator uses scripted RAG; does not measure real model generation quality."
    )
    md_lines.append(
        "- Diagnostic evaluation measures deterministic tool correctness against synthetic labels."
    )
    md_lines.append(
        "- Retrieval metrics measured over the existing knowledge base; "
        "not a comprehensive benchmark."
    )
    md_lines.append("")

    md_lines.append("## Reproducible Commands")
    md_lines.append("```bash")
    md_lines.append(
        "python -m backend.app.evaluation.cli run --suite all --mode offline "
        "--output evaluation-results/"
    )
    md_lines.append("```")
    md_lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    output_data = {"summary": summary, "results": all_results}
    print(json.dumps(output_data, indent=2, default=str))

    # Advisory findings are printed loudly and never silently swallowed, but
    # they do not decide the exit code unless explicitly made blocking.
    for suite, name in advisory:
        print(
            f"WARNING: {suite} {name} gate failed (advisory); "
            "pass --require-abstention to make it blocking."
            if name == "abstention"
            else f"WARNING: {suite} {name} gate failed (advisory).",
            file=sys.stderr,
        )

    if failing:
        print(
            "FAIL: blocking gate failure in {}.".format(", ".join(failing)),
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())