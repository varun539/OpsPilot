"""End-to-end evaluation of the real LangGraph investigation graph.

The graph, tools, retrieval, checkpointer, interrupt and resume path are real.
The LLM is a controlled case provider so the suite can run offline and score
workflow, tool selection, schema validity and grounding without pretending to
measure natural-language generation quality.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.app.agent import InvestigationRequest, OpsPilotAgent
from backend.app.agent.models import ALLOWED_TOOLS, MAX_TOOLS
from backend.app.rag.citation_validator import CitationValidator
from backend.app.rag.models import (
    EvidenceReference,
    InvestigationReport,
    RAGResponse,
    RetrievedEvidence,
)

from .dataset import load_incidents
from .grounding import evaluate_grounding, finding_coverage
from .metrics import abstention_counts, average, precision_recall_f1
from .models import CaseResult, IncidentCase, SuiteResult
from .retrieval_evaluator import build_retrieval_runtime


def _retrieved_evidence(value: Any) -> list[RetrievedEvidence]:
    items: list[RetrievedEvidence] = []
    for raw in value or []:
        if isinstance(raw, RetrievedEvidence):
            items.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        try:
            items.append(
                RetrievedEvidence(
                    chunk_id=str(raw.get("chunk_id") or ""),
                    doc_id=str(raw.get("doc_id") or ""),
                    title=str(raw.get("title") or ""),
                    source_path=str(raw.get("source_path") or ""),
                    chunk_text=str(raw.get("chunk_text") or raw.get("text") or ""),
                    metadata=dict(raw.get("metadata") or {}),
                )
            )
        except Exception:
            continue
    return items


class ScriptedCaseRAG:
    """Produce the case's labeled report using only retrieved evidence IDs."""

    provider_name = "scripted-evaluation"
    model = "scripted-evaluation-v1"

    def __init__(self, case: IncidentCase) -> None:
        self.case = case

    def investigate_with_evidence(
        self,
        question: str,
        evidence: Any,
        **_: Any,
    ) -> RAGResponse:
        items = _retrieved_evidence(evidence)
        expected_docs = set(self.case.expected.relevant_doc_ids)
        cited: list[RetrievedEvidence] = []
        seen_docs: set[str] = set()
        for item in items:
            if item.doc_id in expected_docs and item.doc_id not in seen_docs:
                cited.append(item)
                seen_docs.add(item.doc_id)

        if self.case.expected.abstain or not cited:
            report = InvestigationReport(
                summary="The available evidence is insufficient for a confirmed root cause.",
                incident_category="insufficient_evidence",
                affected_components=[],
                evidence_status="insufficient",
                root_cause_hypotheses=[],
                supporting_evidence=[],
                recommended_actions=[],
                missing_information=[
                    "Relevant knowledge-base evidence for this incident.",
                    "Additional logs, metrics, traces or configuration.",
                ],
                limitations=[
                    "No supported root cause can be concluded from the current evidence."
                ],
            )
            status = "insufficient_evidence"
        else:
            chunk_ids = [item.chunk_id for item in cited]
            report = InvestigationReport(
                summary=f"Evidence is consistent with {self.case.expected.root_cause_category}.",
                incident_category=self.case.expected.root_cause_category,
                affected_components=[],
                evidence_status="sufficient",
                root_cause_hypotheses=[
                    {
                        "title": self.case.expected.root_cause_category.replace("_", " "),
                        "explanation": "Synthetic evaluation label grounded in",
                        "supporting_chunk_ids": chunk_ids,
                        "evidence_strength": "medium",
                    }
                ],
                supporting_evidence=[
                    EvidenceReference(
                        chunk_id=item.chunk_id,
                        doc_id=item.doc_id,
                        title=item.title,
                        reason="Expected relevant evaluation document.",
                    )
                    for item in cited
                ],
                recommended_actions=[
                    {
                        "description": "Verify the finding with a human operator.",
                        "rationale": "Evaluation reports never execute remediation.",
                        "supporting_chunk_ids": chunk_ids,
                        "risk_level": "low",
                        "requires_human_review": True,
                    }
                ],
                missing_information=[],
                limitations=self.case.expected.report_characteristics,
            )
            status = "success"

        validation = CitationValidator().validate(report, items)
        sources = [
            {
                "chunk_id": item.chunk_id,
                "doc_id": item.doc_id,
                "title": item.title,
                "source_path": item.source_path,
            }
            for item in items
        ]
        return RAGResponse(
            question=question,
            report=report,
            retrieved_evidence=items,
            source_references=sources,
            retrieval_metadata={"retrieved_count": len(items)},
            provider=self.provider_name,
            model=self.model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            generation_status=status,
            valid=validation.valid,
            citation_valid=validation.valid,
            validation_errors=validation.errors,
        )


def _diagnostic_codes(state: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for payload in (state.get("diagnostic_results") or {}).values():
        if not isinstance(payload, dict):
            continue
        for finding in payload.get("findings") or []:
            if isinstance(finding, dict) and finding.get("code"):
                codes.add(str(finding["code"]))
    return codes


def _run_case(case: IncidentCase, runtime: Any) -> CaseResult:
    started = time.perf_counter()
    failures: list[str] = []
    agent = OpsPilotAgent(
        retrieval_engine=runtime.hybrid,
        rag_service=ScriptedCaseRAG(case),
        in_memory=True,
    )
    thread_id = f"eval-{case.case_id}"
    state = agent.investigate(
        InvestigationRequest.model_validate(case.request.model_dump()),
        thread_id=thread_id,
        investigation_id=f"eval-{case.case_id}",
    )
    interrupted = "__interrupt__" in state
    pre_resume_status = state.get("execution_status")
    final: dict[str, Any] = {}
    resume_error = ""
    try:
        final = agent.resume(
            thread_id=thread_id,
            decision=case.expected.workflow.resume_decision,
        )
    except Exception as exc:
        resume_error = type(exc).__name__
    finally:
        agent.close()

    selected = list(final.get("selected_tools") or state.get("selected_tools") or [])
    expected_tools = list(case.expected.diagnostic_tools)
    exact_tools = set(selected) == set(expected_tools)
    unnecessary = len(set(selected) - set(expected_tools))
    missing_tools = len(set(expected_tools) - set(selected))
    allowlist_ok = set(selected).issubset(ALLOWED_TOOLS)
    max_tools_ok = len(selected) <= MAX_TOOLS
    if not exact_tools:
        failures.append(
            f"tools selected {selected} != expected {expected_tools}"
        )
    if not allowlist_ok:
        failures.append("tool allowlist violated")
    if not max_tools_ok:
        failures.append("maximum tool count exceeded")

    diagnostic = final.get("diagnostic_results") or state.get("diagnostic_results") or {}
    codes = _diagnostic_codes(final or state)
    coverage = finding_coverage(diagnostic, case.expected.diagnostic_finding_codes)
    if coverage["coverage"] < 1.0:
        failures.append(f"missing diagnostic findings: {coverage['missing']}")

    response = final.get("investigation_report") or state.get("investigation_report")
    evidence = final.get("retrieval_results") or state.get("retrieval_results") or []
    grounding = evaluate_grounding(
        response,
        evidence,
        expected_doc_ids=case.expected.relevant_doc_ids,
        expected_chunk_ids=case.expected.expected_chunk_ids,
        expected_category=case.expected.root_cause_category,
        expected_abstain=case.expected.abstain,
    )
    if not grounding["report_valid"]:
        failures.append("report schema/validity check failed")
    if grounding["category_correct"] is not True:
        failures.append(
            f"root-cause category {grounding['category']!r} != "
            f"{case.expected.root_cause_category!r}"
        )
    if not grounding["abstention_correct"]:
        failures.append("abstention decision incorrect")
    if not grounding["citation_valid"] or not grounding["source_integrity"]:
        failures.append("citation/source integrity failed")
    if not case.expected.abstain:
        if grounding["citation_recall"] < 1.0:
            failures.append(
                f"citation recall {grounding['citation_recall']:.2f} < 1.0"
            )
        if grounding["citation_precision"] < 1.0:
            failures.append(
                f"citation precision {grounding['citation_precision']:.2f} < 1.0"
            )
        if not grounding["cited_chunk_ids"]:
            failures.append("no citations were emitted")

    workflow_success = (
        interrupted
        and not resume_error
        and final.get("execution_status") == "completed"
        and final.get("approval_status")
        == case.expected.workflow.resume_decision
    )
    if not interrupted:
        failures.append("human-review interrupt was not reached")
    if resume_error:
        failures.append(f"resume raised {resume_error}")
    if final.get("execution_status") != "completed":
        failures.append(
            f"final workflow status {final.get('execution_status')!r} != completed"
        )

    serialized_diagnostics = json.dumps(diagnostic, default=str, sort_keys=True)
    serialized_report = json.dumps(response, default=str, sort_keys=True)
    leaked = [
        secret
        for secret in case.security.secret_values
        if secret in serialized_diagnostics or secret in serialized_report
    ]
    system_prompt_leak = "You are OpsPilot, a technical incident investigation" in (
        serialized_report + serialized_diagnostics
    )
    security_violations = len(leaked) + (1 if system_prompt_leak else 0)
    if leaked:
        failures.append("secret value leaked into diagnostics or report")
    if system_prompt_leak:
        failures.append("system prompt leaked into output")
    if not interrupted and final.get("execution_status") == "completed":
        security_violations += 1
        failures.append("approval boundary bypassed")

    metrics = {
        "selected_tools": selected,
        "expected_tools": expected_tools,
        "tool_selection_correct": exact_tools,
        "unnecessary_tool_calls": unnecessary,
        "missing_tool_calls": missing_tools,
        "allowlist_ok": allowlist_ok,
        "max_tools_ok": max_tools_ok,
        "diagnostic_codes": sorted(codes),
        "finding_coverage": coverage["coverage"],
        "interrupted": interrupted,
        "pre_resume_status": pre_resume_status,
        "resume_success": bool(
            not resume_error and final.get("execution_status") == "completed"
        ),
        "workflow_success": workflow_success,
        "retrieved_count": len(evidence),
        "retrieved_doc_ids": grounding["retrieved_document_ids"],
        "security_violations": security_violations,
        **{key: grounding[key] for key in (
            "report_valid",
            "citation_valid",
            "source_integrity",
            "citation_precision",
            "citation_recall",
            "category",
            "category_correct",
            "abstained",
            "abstention_correct",
            "unsupported_conclusion",
        )},
    }
    return CaseResult(
        case_id=case.case_id,
        suite="agent",
        passed=not failures,
        metrics=metrics,
        failures=failures,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def evaluate_agent(
    cases: list[IncidentCase] | None = None,
    *,
    mode: str = "offline",
    runtime: Any = None,
) -> SuiteResult:
    started = time.perf_counter()
    cases = cases or load_incidents()
    owns_runtime = runtime is None
    runtime = runtime or build_retrieval_runtime(mode)
    try:
        results = [_run_case(case, runtime) for case in cases]
    finally:
        if owns_runtime:
            runtime.close()

    expected_abstain = [case.expected.abstain for case in cases]
    actual_abstain = [bool(result.metrics.get("abstained")) for result in results]
    abstention = abstention_counts(expected_abstain, actual_abstain)
    abstention_prf = precision_recall_f1(abstention)
    category_rows = [
        result for result in results if result.metrics.get("category_correct") is not None
    ]
    no_abstain_rows = [
        result for result in results if not result.metrics.get("abstained")
    ]
    metrics = {
        "cases": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "pass_rate": (
            sum(1 for result in results if result.passed) / len(results)
            if results
            else 0.0
        ),
        "tool_selection_accuracy": average(
            1.0 if result.metrics["tool_selection_correct"] else 0.0
            for result in results
        ),
        "completion_rate": average(
            1.0 if result.metrics["workflow_success"] else 0.0 for result in results
        ),
        "report_validity_rate": average(
            1.0 if result.metrics["report_valid"] else 0.0 for result in results
        ),
        "root_cause_category_accuracy": average(
            1.0 if result.metrics.get("category_correct") else 0.0
            for result in category_rows
        ),
        "finding_coverage": average(
            float(result.metrics.get("finding_coverage", 0.0)) for result in results
        ),
        "citation_precision": average(
            float(result.metrics.get("citation_precision", 0.0))
            for result in no_abstain_rows
        ),
        "citation_recall": average(
            float(result.metrics.get("citation_recall", 0.0))
            for result in no_abstain_rows
        ),
        "citation_integrity_rate": average(
            1.0
            if result.metrics.get("citation_valid")
            and result.metrics.get("source_integrity")
            else 0.0
            for result in results
        ),
        "abstention_accuracy": (
            (
                abstention["true_positive"] + abstention["true_negative"]
            )
            / abstention["total"]
            if abstention["total"]
            else 0.0
        ),
        "abstention_precision": abstention_prf["precision"],
        "abstention_recall": abstention_prf["recall"],
        "abstention_counts": abstention,
        "workflow_success_rate": average(
            1.0 if result.metrics["workflow_success"] else 0.0
            for result in results
        ),
        "interrupt_success_rate": average(
            1.0 if result.metrics["interrupted"] else 0.0 for result in results
        ),
        "resume_success_rate": average(
            1.0 if result.metrics["resume_success"] else 0.0 for result in results
        ),
        "unnecessary_tool_call_rate": (
            sum(int(result.metrics["unnecessary_tool_calls"]) for result in results)
            / max(1, sum(len(result.metrics["selected_tools"]) for result in results))
        ),
        "security_violations": sum(
            int(result.metrics["security_violations"]) for result in results
        ),
        "adversarial_cases": sum(1 for case in cases if case.security.adversarial),
    }
    return SuiteResult(
        suite="agent",
        mode=mode,
        dataset_version=cases[0].dataset_version if cases else "",
        cases=results,
        metrics=metrics,
        limitations=[
            "The graph, tools, retrieval, checkpointer, interrupt and resume are real.",
            "The LLM is a controlled case provider; this suite does NOT measure real "
            "model language quality or generalization.",
            "Tool selection in this suite comes from the existing deterministic planner "
            "fallback because no live LLM is used.",
            "Reports are case-labeled; grounding metrics measure workflow/citation "
            "correctness, not independent semantic truth.",
        ],
        duration_ms=(time.perf_counter() - started) * 1000,
    )
