"""Planner quality evaluation with controlled provider responses.

Each scenario scripts one planner behavior (valid plan, unknown tool, malformed
output, timeout, refusal, ...) and runs the real LangGraph node path so the
allow-list boundary is measured where it actually matters: state and executed
tools, not just the provider's return value.

Labels in ``planner_v1.jsonl`` are control-flow expectations for these fixtures.
They are not judgments of a live model's language quality.
"""

from __future__ import annotations

import time
from typing import Any

from backend.app.agent import InvestigationRequest, OpsPilotAgent
from backend.app.agent.models import ALLOWED_TOOLS, InvestigationPlan
from backend.app.agent.planner import InvestigationPlanner
from backend.app.llm.base import LLMResult, LLMUsage
from backend.app.llm.exceptions import LLMRefusalError, LLMTimeoutError
from backend.app.rag.models import InvestigationReport, RAGResponse

from .dataset import load_planner
from .metrics import average
from .models import CaseResult, PlannerScenario, SuiteResult
from .retrieval_evaluator import build_retrieval_runtime


class ScriptedPlannerProvider:
    """Return or raise exactly what a planner scenario describes."""

    model = "scripted-planner-v1"
    provider_name = "scripted-planner"

    def __init__(self, scenario: PlannerScenario) -> None:
        self.scenario = scenario
        self.calls = 0

    def generate_structured(
        self,
        *,
        system_instructions: str,
        user_input: str,
        output_schema: Any,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        self.calls += 1
        if output_schema.__name__ != "InvestigationPlan":
            raise AssertionError("planner evaluator must not synthesize reports")
        kind = self.scenario.response_kind
        if kind == "timeout":
            raise LLMTimeoutError("Gemini request timed out")
        if kind == "refusal":
            raise LLMRefusalError("Gemini response was blocked")
        if kind == "empty":
            output: Any = None
        elif kind == "raw":
            output = self.scenario.raw_output
        else:
            output = InvestigationPlan(
                selected_tools=list(self.scenario.selected_tools),
                rationale="Scripted planner fixture.",
            )
        return LLMResult(output=output, model=self.model, usage=LLMUsage(total_tokens=1))


class _FallbackRAG:
    """Valid insufficient-evidence report so the graph reaches human review."""

    provider_name = "scripted-planner"
    model = "scripted-planner-v1"

    def investigate_with_evidence(self, question: str, evidence: Any, **_: Any) -> RAGResponse:
        return RAGResponse(
            question=question,
            report=InvestigationReport(
                summary="Planner evaluation does not score report content.",
                incident_category="insufficient_evidence",
                evidence_status="insufficient",
            ),
            generation_status="insufficient_evidence",
            valid=True,
            citation_valid=True,
        )


def _error_kind(message: str) -> str:
    text = message.lower()
    if "unknown tool" in text:
        return "unknown_tool"
    if "too many selected tools" in text:
        return "too_many"
    if "duplicate tools" in text:
        return "duplicate"
    if "llmtimeouterror" in text:
        return "timeout"
    if "llmrefusalerror" in text:
        return "refusal"
    if message:
        return "validation"
    return "none"


def _run_scenario(scenario: PlannerScenario, runtime: Any) -> CaseResult:
    started = time.perf_counter()
    failures: list[str] = []
    provider = ScriptedPlannerProvider(scenario)
    agent = OpsPilotAgent(
        llm_provider=provider,
        retrieval_engine=runtime.hybrid,
        rag_service=_FallbackRAG(),
        in_memory=True,
    )
    try:
        state = agent.investigate(
            InvestigationRequest.model_validate(scenario.request.model_dump()),
            thread_id=f"planner-{scenario.case_id}",
            investigation_id=f"planner-{scenario.case_id}",
        )
    finally:
        agent.close()

    selected = list(state.get("selected_tools") or [])
    executed = sorted((state.get("diagnostic_results") or {}).keys())
    planner_error = str(state.get("planner_error") or "")
    warnings = list(state.get("warnings") or [])
    used = bool(state.get("planner_used"))
    fallback = bool(state.get("fallback_used"))
    interrupted = "__interrupt__" in state or state.get("execution_status") == "awaiting_approval"

    unauthorized_state = sorted(set(selected) - set(ALLOWED_TOOLS))
    unauthorized_executed = sorted(set(executed) - set(ALLOWED_TOOLS))
    observable = bool(planner_error) and any("deterministic fallback" in w for w in warnings)
    error_kind = _error_kind(planner_error)

    if used != scenario.expected_used:
        failures.append(f"planner_used {used} != {scenario.expected_used}")
    if fallback != scenario.expected_fallback:
        failures.append(f"fallback_used {fallback} != {scenario.expected_fallback}")
    if selected != scenario.expected_tools:
        failures.append(f"selected_tools {selected} != {scenario.expected_tools}")
    if scenario.expected_fallback and not observable:
        failures.append("fallback was not observable (planner_error/warning missing)")
    if error_kind != scenario.expected_error_kind:
        failures.append(f"error kind {error_kind!r} != {scenario.expected_error_kind!r}")
    if scenario.expected_error_contains and scenario.expected_error_contains not in planner_error:
        failures.append(
            f"planner_error missing {scenario.expected_error_contains!r}"
        )
    if unauthorized_state:
        failures.append(f"unauthorized tools entered state: {unauthorized_state}")
    if unauthorized_executed:
        failures.append(f"unauthorized tools executed: {unauthorized_executed}")
    if not interrupted:
        failures.append("workflow did not pause for human review")

    # The same failing input must produce the same plan every time.
    def _deterministic_signature() -> tuple[dict[str, Any], bool]:
        planner = InvestigationPlanner(llm_provider=ScriptedPlannerProvider(scenario))
        plan, planned = planner.plan(
            InvestigationRequest.model_validate(scenario.request.model_dump()),
            list(scenario.available_inputs),
            [],
        )
        return plan.model_dump(), planned

    first = _deterministic_signature()
    second = _deterministic_signature()
    deterministic = first == second
    if not deterministic:
        failures.append("planner outcome is not deterministic for the same input")

    if scenario.expected_used:
        classification = "valid_selection" if not failures else "unexpected"
    else:
        classification = "rejected_invalid" if not failures else "unexpected"
    if classification == "unexpected" and not failures:
        classification = "invalid_selection"  # pragma: no cover - defensive

    metrics = {
        "expected_used": scenario.expected_used,
        "planner_used": used,
        "fallback_used": fallback,
        "selected_tools": selected,
        "expected_tools": list(scenario.expected_tools),
        "executed_tools": executed,
        "unauthorized_state_tools": unauthorized_state,
        "unauthorized_executed_tools": unauthorized_executed,
        "planner_error": planner_error,
        "error_kind": error_kind,
        "observable": observable,
        "deterministic": deterministic,
        "interrupted": interrupted,
        "provider_calls": provider.calls,
        "classification": classification,
    }
    return CaseResult(
        case_id=scenario.case_id,
        suite="planner",
        passed=not failures,
        metrics=metrics,
        failures=failures,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def evaluate_planner(
    scenarios: list[PlannerScenario] | None = None,
    *,
    runtime: Any = None,
) -> SuiteResult:
    started = time.perf_counter()
    scenarios = scenarios or load_planner()
    owns_runtime = runtime is None
    runtime = runtime or build_retrieval_runtime("offline")
    try:
        results = [_run_scenario(scenario, runtime) for scenario in scenarios]
    finally:
        if owns_runtime:
            runtime.close()

    valid_rows = [r for r in results if r.metrics["expected_used"]]
    invalid_rows = [r for r in results if not r.metrics["expected_used"]]
    fallback_rows = [r for r in results if r.metrics["fallback_used"]]
    unauthorized_attempts = sum(1 for s in scenarios if s.expect_unauthorized_attempt)
    metrics = {
        "scenarios": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "pass_rate": sum(1 for r in results if r.passed) / len(results) if results else 0.0,
        "valid_selection_cases": len(valid_rows),
        "valid_selection_rate": average(
            1.0 if r.metrics["classification"] == "valid_selection" else 0.0
            for r in valid_rows
        ),
        "invalid_selection_cases": len(invalid_rows),
        "invalid_selection_rejection_rate": average(
            1.0 if r.metrics["classification"] == "rejected_invalid" else 0.0
            for r in invalid_rows
        ),
        "fallback_activation_rate": (
            len(fallback_rows) / len(results) if results else 0.0
        ),
        "fallback_observability_rate": average(
            1.0 if r.metrics["observable"] else 0.0 for r in fallback_rows
        ),
        "determinism_rate": average(
            1.0 if r.metrics["deterministic"] else 0.0 for r in results
        ),
        "interrupt_rate": average(
            1.0 if r.metrics["interrupted"] else 0.0 for r in results
        ),
        "unauthorized_tool_request_count": unauthorized_attempts,
        "unauthorized_tool_in_state_count": sum(
            len(r.metrics["unauthorized_state_tools"]) for r in results
        ),
        "unauthorized_tool_execution_count": sum(
            len(r.metrics["unauthorized_executed_tools"]) for r in results
        ),
        "planner_call_count": sum(int(r.metrics["provider_calls"]) for r in results),
    }
    return SuiteResult(
        suite="planner",
        mode="offline",
        dataset_version=scenarios[0].dataset_version if scenarios else "",
        cases=results,
        metrics=metrics,
        limitations=[
            "Provider responses are scripted fixtures; this suite does not measure a live "
            "model's planning quality.",
            "Labels are control-flow expectations (plan used vs deterministic fallback), "
            "not independent judgments of tool appropriateness.",
            "The graph, planner validation, allow-list filter and tool execution are real.",
        ],
        duration_ms=(time.perf_counter() - started) * 1000,
    )
