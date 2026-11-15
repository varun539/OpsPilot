"""Investigation planning for the OpsPilot agent."""


from __future__ import annotations

from typing import Any

from backend.app.agent.models import (
    ALLOWED_TOOLS,
    MAX_TOOLS,
    InvestigationPlan,
    InvestigationRequest,
)

PLANNER_SYSTEM_PROMPT = (
    "You are OpsPilot's investigation planner. Select only diagnostic tools from "
    "the fixed allow-list. Never propose code execution, shell commands, file "
    "paths, or arbitrary functions. Return a concise structured plan. Mark missing "
    "diagnostic information explicitly. Tool selection must be justified by "
    "available evidence. Do not claim a root cause."
)


class InvestigationPlanner:
    """Plan diagnostic tool use using an LLM provider or deterministic fallback."""

    def __init__(self, llm_provider: Any = None, *, max_tools: int = MAX_TOOLS) -> None:
        self.llm_provider = llm_provider
        self.max_tools = min(int(max_tools), MAX_TOOLS)

    def plan(
        self,
        request: InvestigationRequest,
        available_inputs: list[str],
        retrieved_evidence: list[dict[str, Any]] | None = None,
    ) -> tuple[InvestigationPlan, bool]:
        """Return a plan and whether the LLM provider was used."""
        fallback = self._fallback_plan(request, available_inputs)
        if self.llm_provider is None:
            return fallback, False

        evidence_summary = self._summarize_evidence(retrieved_evidence or [])
        user_input = self._build_prompt(request, available_inputs, evidence_summary)
        try:
            result = self.llm_provider.generate_structured(
                system_instructions=PLANNER_SYSTEM_PROMPT,
                user_input=user_input,
                output_schema=InvestigationPlan,
                timeout=30.0,
                max_output_tokens=1200,
            )
            plan = result.output
            if not isinstance(plan, InvestigationPlan):
                plan = InvestigationPlan.model_validate(plan)
            self._validate_plan(plan, available_inputs)
            if not plan.fallback:
                plan = plan.model_copy(update={"fallback": False})
            return plan, True
        except Exception:
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