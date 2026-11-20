"""Opt-in, strictly bounded live Gemini *planner* evaluation.

Safety contract
---------------
* **Default is zero network calls.** Nothing here runs unless
  ``OPSPILOT_LIVE_PLANNER_EVAL=1`` is set *and* a provider key is configured *and*
  the caller explicitly asks for the live suite. The CLI never includes it in
  ``--suite all``.
* Requests are **sequential** with no parallelism, **no automatic retries** and a
  hard, small request ceiling (default 3, hard maximum 5).
* Only the *planner* is exercised. No investigation report is generated, no
  diagnostic tool is executed, and the planner's chosen tools are never run.
* Only synthetic incident text is sent. No customer data, no attachments.
* Credentials are never printed, logged or returned; the summary records only
  whether a key was present.

Call ``evaluate_live_planner()`` without enabling it and the result is an
explicit ``skipped`` suite with ``requests_made == 0`` - that is the behaviour
tests assert, so a CI run can never spend money by accident.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from backend.app.agent.models import ALLOWED_TOOLS, InvestigationRequest
from backend.app.agent.planner import InvestigationPlanner, classify_planner_error
from backend.app.llm.config import API_KEY_ENV, PROVIDER_KEY_ENVS

from .metrics import average
from .models import CaseResult, SuiteResult

LIVE_EVAL_ENV = "OPSPILOT_LIVE_PLANNER_EVAL"
MAX_REQUESTS_ENV = "OPSPILOT_LIVE_PLANNER_MAX_REQUESTS"
MODEL_ENV = "OPSPILOT_LIVE_PLANNER_MODEL"

DEFAULT_MAX_REQUESTS = 3
HARD_MAX_REQUESTS = 5
# The model this evaluation was designed for. The configured provider model is
# used instead when it is set, and the difference is recorded.
DESIGNED_MODEL = "gemini-3.1-flash-lite"


@dataclass(frozen=True)
class LiveScenario:
    case_id: str
    description: str
    request: InvestigationRequest
    available_inputs: list[str]
    expected_tools: list[str]


# Synthetic incidents only. Every scenario is small, self-contained text with no
# customer identifiers, hostnames or credentials.
SCENARIOS: list[LiveScenario] = [
    LiveScenario(
        case_id="LIVE-PLAN-001",
        description="Logs and a stack trace are available; expect log and trace tools.",
        request=InvestigationRequest(
            question="Synthetic: requests fail with a connection pool timeout after deploy.",
            incident_context="Synthetic incident. Service returns 500 under load.",
            logs=(
                "ERROR QueuePool limit of size 5 overflow 10 reached\n"
                "ERROR TimeoutError: QueuePool limit reached"
            ),
            stack_trace="TimeoutError: QueuePool limit reached\n  at pool.acquire()",
        ),
        available_inputs=["logs", "stack_trace"],
        expected_tools=["log_analyzer", "stack_trace_analyzer"],
    ),
    LiveScenario(
        case_id="LIVE-PLAN-002",
        description="Only an HTTP status is available; expect the HTTP classifier.",
        request=InvestigationRequest(
            question="Synthetic: the API returns 503 for every request.",
            incident_context="Synthetic incident. Upstream dependency unavailable.",
            http_status=503,
            response_text="Service Unavailable",
        ),
        available_inputs=["http_status"],
        expected_tools=["http_error_classifier"],
    ),
    LiveScenario(
        case_id="LIVE-PLAN-003",
        description="Only configuration is available; expect the config validator.",
        request=InvestigationRequest(
            question="Synthetic: the service starts but is unreachable.",
            incident_context="Synthetic incident. Deployment configuration suspected.",
            configuration={"PORT": "not-a-number", "HEALTH_PATH": ""},
        ),
        available_inputs=["configuration"],
        expected_tools=["config_validator"],
    ),
]


def _provider_key_present() -> bool:
    provider = os.getenv("OPSPILOT_LLM_PROVIDER", "gemini").strip().lower()
    names = [API_KEY_ENV, PROVIDER_KEY_ENVS.get(provider, "")]
    return any(os.getenv(name) for name in names if name)


def live_eval_enabled() -> bool:
    """True only when the operator has explicitly opted in."""
    return os.getenv(LIVE_EVAL_ENV, "").strip() == "1"


def resolve_request_limit(value: int | None = None) -> int:
    """Return the effective request ceiling, never above the hard maximum."""
    if value is None:
        raw = os.getenv(MAX_REQUESTS_ENV, "").strip()
        try:
            value = int(raw) if raw else DEFAULT_MAX_REQUESTS
        except ValueError:
            value = DEFAULT_MAX_REQUESTS
    return max(1, min(int(value), HARD_MAX_REQUESTS))


def _skipped(reason: str, *, limit: int) -> SuiteResult:
    return SuiteResult(
        suite="live-planner",
        mode="live",
        dataset_version="live_planner_v1",
        cases=[],
        metrics={
            "status": "skipped",
            "skip_reason": reason,
            "requests_made": 0,
            "requests_attempted": 0,
            "request_limit": limit,
            "tools_executed": 0,
            "reports_generated": 0,
        },
        limitations=[reason],
        duration_ms=0.0,
    )


def _plan_metrics(plan: Any, used: bool, latency_ms: float) -> dict[str, Any]:
    selected = list(getattr(plan, "selected_tools", []) or [])
    allowed = set(ALLOWED_TOOLS)
    return {
        "planner_used": used,
        "selected_tools": selected,
        "valid_selection": set(selected).issubset(allowed) and len(selected) > 0,
        "invalid_selection": bool(set(selected) - allowed),
        "rationale_present": bool(getattr(plan, "rationale", "") or ""),
        "missing_information_present": isinstance(
            getattr(plan, "missing_information", None), list
        ),
        "latency_ms": latency_ms,
        "tools_executed": 0,
    }


def evaluate_live_planner(
    *,
    provider: Any = None,
    scenarios: list[LiveScenario] | None = None,
    max_requests: int | None = None,
    include_consistency: bool = True,
) -> SuiteResult:
    """Run the bounded live planner evaluation, or report why it was skipped.

    ``provider`` exists so tests can inject a double; when it is ``None`` the
    real provider is only constructed if the opt-in environment flag is set.
    """
    started = time.perf_counter()
    limit = resolve_request_limit(max_requests)
    scenarios = scenarios or SCENARIOS

    if provider is None:
        if not live_eval_enabled():
            return _skipped(
                f"{LIVE_EVAL_ENV}=1 is not set; no external request was made", limit=limit
            )
        if not _provider_key_present():
            return _skipped(
                "no provider credential is configured; live planner evaluation "
                "stays disabled",
                limit=limit,
            )
        from backend.app.investigation_tools.sanitization import redact_text
        from backend.app.llm.config import LLMConfig
        from backend.app.llm.factory import create_llm_provider

        try:
            provider = create_llm_provider(LLMConfig.from_env())
        except Exception as exc:  # noqa: BLE001 - misconfiguration is expected here
            # Never surface the raw exception text unredacted; it can echo env
            # values. The message names the variable, not its content.
            reason = (
                "provider configuration is incomplete, so the live planner "
                f"evaluation stays disabled ({redact_text(str(exc))[:200]})"
            )
            return _skipped(reason, limit=limit)

    # Build the call plan deterministically: each scenario once, then (budget
    # permitting) one repeat of the first scenario to measure consistency.
    calls: list[tuple[str, LiveScenario]] = [
        ("single", scenario) for scenario in scenarios
    ]
    if include_consistency and calls:
        calls.append(("repeat", scenarios[0]))

    results: list[CaseResult] = []
    requests_made = 0
    latencies: list[float] = []
    first_selection: list[str] | None = None

    try:
        for kind, scenario in calls:
            if requests_made >= limit:
                break
            planner = InvestigationPlanner(llm_provider=provider)
            call_started = time.perf_counter()
            requests_made += 1
            try:
                plan, used = planner.plan(
                    scenario.request,
                    list(scenario.available_inputs),
                    [],
                )
                error = planner.last_error
            except Exception as exc:  # noqa: BLE001 - classification is the point
                # The planner swallows provider errors itself, so reaching this
                # branch means a defect; record and stop rather than retry.
                results.append(
                    CaseResult(
                        case_id=f"{scenario.case_id}:{kind}",
                        suite="live-planner",
                        passed=False,
                        metrics={
                            "planner_used": False,
                            "error_kind": "unexpected",
                            "error_type": type(exc).__name__,
                            "tools_executed": 0,
                        },
                        failures=["planner raised unexpectedly"],
                        duration_ms=(time.perf_counter() - call_started) * 1000,
                        mode="live",
                    )
                )
                break
            latency = (time.perf_counter() - call_started) * 1000
            latencies.append(latency)
            metrics = _plan_metrics(plan, used, latency)
            metrics["kind"] = kind
            metrics["error_kind"] = classify_planner_error(error)
            metrics["expected_tools"] = list(scenario.expected_tools)
            metrics["tools_match_expected"] = (
                set(metrics["selected_tools"]) == set(scenario.expected_tools)
            )

            if kind == "repeat":
                metrics["consistent_with_first"] = (
                    set(metrics["selected_tools"]) == set(first_selection or [])
                )

            failures: list[str] = []
            if not metrics["valid_selection"]:
                failures.append("planner selected a tool outside the allow-list")
            if metrics["error_kind"] != "none" and scenario.expected_tools:
                failures.append(f"planner fell back: {metrics['error_kind']}")
            results.append(
                CaseResult(
                    case_id=f"{scenario.case_id}:{kind}",
                    suite="live-planner",
                    passed=not failures,
                    metrics=metrics,
                    failures=failures,
                    duration_ms=latency,
                    mode="live",
                )
            )

            if kind == "single" and first_selection is None:
                first_selection = list(metrics["selected_tools"])
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if requests_made == 0:
        return _skipped("request budget exhausted before any call", limit=limit)

    single_rows = [r for r in results if r.metrics.get("kind") == "single"]
    repeat_rows = [r for r in results if r.metrics.get("kind") == "repeat"]
    fallback_rows = [r for r in results if r.metrics.get("error_kind") not in (None, "none")]

    metrics = {
        "status": "executed",
        "requests_made": requests_made,
        "requests_attempted": len(results),
        "request_limit": limit,
        "sequential": True,
        "retries": 0,
        "reports_generated": 0,
        "tools_executed": 0,
        "model": getattr(provider, "model", None),
        "designed_model": DESIGNED_MODEL,
        "valid_selection_rate": average(
            1.0 if r.metrics.get("valid_selection") else 0.0 for r in results
        ),
        "invalid_selection_count": sum(
            1 for r in results if r.metrics.get("invalid_selection")
        ),
        "expected_tool_match_rate": average(
            1.0 if r.metrics.get("tools_match_expected") else 0.0 for r in single_rows
        ),
        "fallback_activation_rate": (
            len(fallback_rows) / len(results) if results else 0.0
        ),
        "error_classifications": _count_by(results, "error_kind"),
        "consistency_rate": average(
            1.0 if r.metrics.get("consistent_with_first") else 0.0 for r in repeat_rows
        )
        if repeat_rows
        else None,
        "consistency_samples": len(repeat_rows),
        "latency_ms_avg": average(latencies),
        "latency_ms_min": min(latencies) if latencies else 0.0,
        "latency_ms_max": max(latencies) if latencies else 0.0,
    }
    return SuiteResult(
        suite="live-planner",
        mode="live",
        dataset_version="live_planner_v1",
        cases=results,
        metrics=metrics,
        limitations=[
            "Live measurement of one small model on three synthetic prompts; it does "
            "not establish report quality or generalization.",
            "The planner schema has no per-tool argument surface, so argument "
            "correctness cannot be scored; only selection validity and rationale "
            "presence are measured.",
            "The planner's chosen tools are never executed, so downstream tool "
            "behaviour is out of scope.",
            "No report generation is performed, so this says nothing about report "
            "quality (see the human-rated rubric).",
        ],
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def _count_by(rows: list[CaseResult], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.metrics.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
