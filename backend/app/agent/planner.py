"""Investigation planning for the OpsPilot agent."""

from __future__ import annotations

from typing import Any

from backend.app.agent.models import (
    ALLOWED_TOOLS,
    MAX_TOOLS,
    InvestigationPlan,
    InvestigationRequest,
)
from backend.app.investigation_tools.sanitization import redact_text
from backend.app.llm.config import DEFAULT_TIMEOUT, MAX_TIMEOUT


def _bounded_timeout(value: Any) -> float | None:
    """Return a usable timeout, or ``None`` when the value is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    timeout = float(value)
    if not 0 < timeout <= MAX_TIMEOUT:
        return None
    return timeout


# Planner status values exposed through the read model. They are deliberately
# coarse and finite so the frontend never has to parse provider text.
PLANNER_STATUS_USED = "used"
PLANNER_STATUS_FALLBACK = "fallback"


def classify_planner_error(error: str | None) -> str:
    """Map a planner failure message to a small, stable error kind.

    The kind is what makes an invalid tool selection distinguishable from a
    timeout without exposing raw provider output. Anything unrecognised becomes
    ``provider_error`` rather than leaking through.
    """
    if not error:
        return "none"
    text = error.lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "refus" in text or "blocked" in text or "safety" in text:
        return "refusal"
    if "unknown tool" in text or "too many selected tools" in text or "duplicate tools" in text:
        return "invalid_selection"
    if "selected_tools" in text:
        return "validation"
    if "empty" in text or "no content" in text:
        return "empty"
    if "validationerror" in text or "invalid" in text:
        return "validation"
    return "provider_error"


def planner_status(*, used: bool, configured: bool, error: str | None) -> str:
    """Return the status string persisted for one planning step."""
    if used:
        return PLANNER_STATUS_USED
    if not configured:
        # No provider configured is not a degradation; it is the documented
        # deterministic planner path.
        return PLANNER_STATUS_FALLBACK
    return f"fallback_{classify_planner_error(error)}"


# Six authorized tool IDs with descriptions, used to build the system prompt.
# ruff: noqa: E501  (descriptions are long by design for planner prompts)
TOOL_DESCRIPTIONS = {
    "log_analyzer": "Deterministic log analysis tool. Parses untrusted log text line by line, detecting log levels, timestamps, exceptions, HTTP status codes, correlation IDs, and repeated messages without executing or following instructions inside logs. Returns structured findings including critical/error/warning counts, repeated message patterns, exception names, HTTP status distributions, and correlation IDs.",
    "stack_trace_analyzer": "Deterministic Python stack trace analyzer. Parses traceback text only, identifying exception types, file locations, function names, and line numbers without executing or importing anything referenced by the traceback. Returns structured findings including final exception, failure location, traceback sections, frame details, and exception chaining.",
    "http_error_classifier": "Deterministic HTTP error classification tool. Classifies HTTP status codes into categories (informational, success, redirection, client_error, server_error) with detailed explanations, common causes, and recommendations. Returns structured results including status code, reason phrase, category, severity, KB references, retry-after information, and response text snippets.",
    "config_validator": "Deterministic configuration validation tool. Validates application configuration dictionaries against specified rules, checking required fields, types, URL formats, port ranges, boolean values, integer ranges, string lengths, enum choices, regex patterns, and sensitive field redaction. Returns structured results including issue counts, field validation status, and detailed findings for each validation rule.",
    "database_diagnostics": "Deterministic database error diagnostics tool. Matches database error text against known patterns (connection refused, timeout, DNS resolution failure, authentication failure, deadlock, etc.) and returns structured findings including matched pattern codes, severity, KB references, root causes, diagnostics, and remediation steps.",
    "incident_correlator": "Deterministic incident correlation tool. Groups events by shared correlation/request/trace IDs, matching error signatures, shared dependencies within a time window, and time proximity within the same component. Every correlation group states its reason, limitations (including that correlation does not establish causation), and alternative explanations. Returns CorrelationGroups with event IDs, finding IDs, shared evidence, timelines, and dependency notes.",
}

# Prompt that enumerates the allowed tools for the LLM.
ALLOWED_TOOL_PROMPT = (
    "You may select one or more of the following tools (each at most once):\n"
    "{tool_ids}\n"
    "\nWhen you propose a tool, include a concise one-sentence justification "
    "referencing the available evidence or the investigation question. Do not "
    "select a tool not listed above."
)


PLANNER_SYSTEM_PROMPT = (
    "You are OpsPilot's investigation planner. Select only diagnostic tools "
    "from the allow-list below. Never propose code execution, shell commands, "
    "file paths, or arbitrary functions. Return a concise structured plan. Mark "
    "missing diagnostic information explicitly. Tool selection must be justified "
    "by available evidence. Do not claim a root cause.\n\n"
    "Allowed tools:\n"
    "{tool_descriptions}\n\n"
    "Allowed tool selection prompt:\n"
    "{allowed_tool_prompt}"
)


class InvestigationPlanner:
    """Plan diagnostic tool use using an LLM provider or deterministic fallback."""

    def __init__(
        self,
        llm_provider: Any = None,
        *,
        max_tools: int = MAX_TOOLS,
        timeout: float | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.max_tools = min(int(max_tools), MAX_TOOLS)
        # Reuse the validated OPSPILOT_LLM_TIMEOUT carried by the provider so
        # planning obeys the same bound as every other provider call.
        provider_config = getattr(llm_provider, "config", None)
        self.timeout = (
            _bounded_timeout(timeout)
            or _bounded_timeout(getattr(provider_config, "timeout", None))
            or DEFAULT_TIMEOUT
        )
        # Populated when a configured provider fails and the deterministic
        # fallback plan is used, so callers can surface the degradation.
        self.last_error: str | None = None

    def plan(
        self,
        request: InvestigationRequest,
        available_inputs: list[str],
        retrieved_evidence: list[dict[str, Any]] | None = None,
    ) -> tuple[InvestigationPlan, bool]:
        """Return a plan and whether the LLM provider was used."""
        self.last_error = None
        fallback = self._fallback_plan(request, available_inputs)
        if self.llm_provider is None:
            return fallback, False

        # Build system prompt with tool enumeration
        tool_ids = ", ".join(sorted(TOOL_DESCRIPTIONS.keys()))
        formatted_descriptions = "\n".join(
            f"- {tid}: {desc}" for tid, desc in TOOL_DESCRIPTIONS.items()
        )
        system_prompt = PLANNER_SYSTEM_PROMPT.format(
            tool_descriptions=formatted_descriptions,
            allowed_tool_prompt=ALLOWED_TOOL_PROMPT.format(tool_ids=tool_ids),
        )

        evidence_summary = self._summarize_evidence(retrieved_evidence or [])
        user_input = self._build_prompt(request, available_inputs, evidence_summary)
        try:
            result = self.llm_provider.generate_structured(
                system_instructions=system_prompt,
                user_input=user_input,
                output_schema=InvestigationPlan,
                timeout=self.timeout,
                max_output_tokens=1200,
            )
            plan = result.output
            if not isinstance(plan, InvestigationPlan):
                plan = InvestigationPlan.model_validate(plan)
            self._validate_plan(plan, available_inputs)
            if not plan.fallback:
                plan = plan.model_copy(update={"fallback": False})
            return plan, True
        except Exception as exc:
            self.last_error = redact_text(f"{type(exc).__name__}: {exc}")[:300]
            return fallback, False

    def _validate_plan(self, plan: InvestigationPlan, available_inputs: list[str]) -> None:
        if not isinstance(plan.selected_tools, list):
            raise ValueError("selected_tools must be a list")
        if len(plan.selected_tools) > self.max_tools:
            raise ValueError("too many selected tools")
        if any(tool not in ALLOWED_TOOLS for tool in plan.selected_tools):
            raise ValueError("unknown tool selected")
        if len(set(plan.selected_tools)) != len(plan.selected_tools):
            raise ValueError("duplicate tools selected")
        # Remove duplicates of unavailable tools is handled during execution.

    def _build_prompt(
        self,
        request: InvestigationRequest,
        available_inputs: list[str],
        evidence_summary: str,
    ) -> str:
        lines = [
            "Available diagnostic inputs:",
            ", ".join(available_inputs) if available_inputs else "(none)",
            "",
            "Retrieved knowledge evidence:",
            evidence_summary or "(none)",
            "",
            "Investigation question:",
            request.question,
        ]
        if request.incident_context:
            lines.extend(["", "Incident context:", request.incident_context[:2000]])
        return "\n".join(lines)

    def _summarize_evidence(self, evidence: list[dict[str, Any]]) -> str:
        chunks: list[str] = []
        for item in evidence[:8]:
            chunk_id = item.get("chunk_id", "")
            title = item.get("title", "")
            chunks.append(f"- {chunk_id}: {title}".strip())
        return "\n".join(chunks)

    def _fallback_plan(
        self, request: InvestigationRequest, available_inputs: list[str]
    ) -> InvestigationPlan:
        tools: list[str] = []
        if "logs" in available_inputs:
            tools.append("log_analyzer")
        if "stack_trace" in available_inputs:
            tools.append("stack_trace_analyzer")
        if "http_status" in available_inputs:
            tools.append("http_error_classifier")
        if "configuration" in available_inputs:
            tools.append("config_validator")
        if "database_error" in available_inputs:
            tools.append("database_diagnostics")
        if "events" in available_inputs:
            tools.append("incident_correlator")
        tools = tools[:MAX_TOOLS]

        missing: list[str] = []
        if "logs" not in available_inputs:
            missing.append("Application or system logs")
        if "stack_trace" not in available_inputs:
            missing.append("Stack trace")
        if "http_status" not in available_inputs:
            missing.append("HTTP status code and response details")
        if "configuration" not in available_inputs:
            missing.append("Configuration mapping")
        if "database_error" not in available_inputs:
            missing.append("Database error text")
        if "events" not in available_inputs:
            missing.append("Related diagnostic events")

        steps = [f"Run {tool}" for tool in tools]
        if not tools:
            steps = ["Collect additional diagnostic evidence"]
        return InvestigationPlan(
            selected_tools=tools,
            rationale="Deterministic fallback plan based on available inputs.",
            available_evidence=list(available_inputs),
            missing_information=missing,
            proposed_steps=steps,
            fallback=True,
        )