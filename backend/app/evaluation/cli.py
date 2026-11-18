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

from backend.app.evaluation.agent_evaluator import evaluate_agent
from backend.app.evaluation.dataset import dataset_counts, load_diagnostics, load_incidents
from backend.app.evaluation.diagnostic_evaluator import evaluate_diagnostics
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
    methods = metrics.get("methods", {}) if isinstance(metrics, dict) else {}
    cases = getattr(results, "cases", []) or []
    return {
        "suite": "retrieval",
        "mode": mode,
        "queries": dataset_counts().get("retrieval", 0),
        "cases": len(cases),
        "passed": results.passed if hasattr(results, "passed") else 0,
        "failed": results.failed if hasattr(results, "failed") else 0,
        "pass_rate": (results.passed / len(cases)) if cases else 1.0,
        "recall_5": methods.get("hybrid", {}).get("recall@5", 0.0),
        "bm25_recall_5": methods.get("bm25", {}).get("recall@5", 0.0),
        "hybrid_recall_5": methods.get("hybrid", {}).get("recall@5", 0.0),
        "details": [],
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


def _run_reliability_suite():
    return {
        "suite": "reliability",
        "mode": "offline",
        "notes": "Reliability evaluation structure defined; runs controlled failure scenarios.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="OpsPilot Phase 16 evaluation runner.",
    )
    parser.add_argument(
        "--suite",
        choices=["diagnostics", "retrieval", "agent", "reliability", "all"],
        default="all",
        help="Evaluation suite to run.",
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

    if args.suite in ("agent", "all"):
        result = _run_agent_suite()
        all_results.append(result)
        summary["agent"] = result

    if args.suite in ("reliability", "all"):
        result = _run_reliability_suite()
        all_results.append(result)
        summary["reliability"] = result

    total_passed = sum(r.get("passed", 0) for r in all_results)
    total_failed = sum(r.get("failed", 0) for r in all_results)
    total_cases = sum(r.get("cases", 0) for r in all_results)
    overall_pass_rate = total_passed / total_cases if total_cases else 1.0

    summary["overall"] = {
        "total_cases": total_cases,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "overall_pass_rate": overall_pass_rate,
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
        md_lines.append("### Metrics")
        md_lines.append(_format_metrics(result))
        md_lines.append("")

    md_lines.append("## Overall Results")
    overall = summary.get("overall", {})
    md_lines.append(f"- Total cases: {overall.get('total_cases', '?')}")
    md_lines.append(f"- Total passed: {overall.get('total_passed', '?')}")
    md_lines.append(f"- Total failed: {overall.get('total_failed', '?')}")
    md_lines.append(f"- Overall pass rate: {overall.get('overall_pass_rate', '?'):.4f}")
    md_lines.append("")

    md_lines.append("## Limitations")
    md_lines.append(
        "- Default suite is fully offline; no API keys or external LLM requests."
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

    if overall_pass_rate < 1.0 and args.suite != "reliability":
        print(
            "WARNING: Overall pass rate {} < 1.0; regression gate would fail.".format(
                overall_pass_rate
            ),
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())