from __future__ import annotations

from unittest.mock import MagicMock

from backend.app.agent.models import InvestigationPlan, InvestigationRequest
from backend.app.agent.planner import InvestigationPlanner
from backend.app.llm.base import LLMResult
from backend.app.llm.config import DEFAULT_TIMEOUT, LLMConfig
from backend.app.llm.exceptions import LLMTimeoutError


class FakeProvider:
    """Provider double that records the per-call timeout it receives."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, error: Exception | None = None) -> None:
        self.config = LLMConfig(
            provider="gemini", model="gemini-free-test", api_key="key", timeout=timeout
        )
        self.error = error
        self.calls: list[dict] = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return LLMResult(
            output=InvestigationPlan(selected_tools=["log_analyzer"], rationale="logs"),
            model="gemini-free-test",
        )


def test_fallback_without_provider():
    req = InvestigationRequest(question="q", logs="ERROR boom")
    planner = InvestigationPlanner(llm_provider=None)
    plan, used = planner.plan(req, ["logs"], [])
    assert used is False
    assert plan.fallback is True
    assert "log_analyzer" in plan.selected_tools


def test_unknown_tool_rejected():
    planner = InvestigationPlanner(llm_provider=None)
    plan = InvestigationPlan(selected_tools=["bad_tool"], rationale="r")
    try:
        planner._validate_plan(plan, ["logs"])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_max_tool_count():
    planner = InvestigationPlanner(llm_provider=None)
    plan = InvestigationPlan(selected_tools=["a", "b", "c", "d", "e", "f", "g"], rationale="r")
    try:
        planner._validate_plan(plan, [])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_provider_failure_falls_back():
    provider = MagicMock()
    provider.generate_structured.side_effect = RuntimeError("boom")
    req = InvestigationRequest(question="q", logs="ERROR boom")
    planner = InvestigationPlanner(llm_provider=provider)
    plan, used = planner.plan(req, ["logs"], [])
    assert used is False
    assert plan.fallback is True


def test_planner_uses_provider_default_timeout():
    provider = FakeProvider()
    planner = InvestigationPlanner(llm_provider=provider)
    assert planner.timeout == DEFAULT_TIMEOUT


def test_planner_without_provider_config_uses_default_timeout():
    planner = InvestigationPlanner(llm_provider=MagicMock())
    assert planner.timeout == DEFAULT_TIMEOUT


def test_planner_uses_configured_timeout():
    provider = FakeProvider(timeout=75.0)
    planner = InvestigationPlanner(llm_provider=provider)
    assert planner.timeout == 75.0

    req = InvestigationRequest(question="q", logs="ERROR boom")
    plan, used = planner.plan(req, ["logs"], [])
    assert used is True
    assert plan.selected_tools == ["log_analyzer"]
    assert provider.calls[0]["timeout"] == 75.0


def test_planner_rejects_invalid_timeout_override():
    provider = FakeProvider(timeout=45.0)
    assert InvestigationPlanner(llm_provider=provider, timeout=0).timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout=-5).timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout=500).timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout="abc").timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout=12.5).timeout == 12.5


def test_planner_timeout_falls_back_deterministically_and_observably():
    provider = FakeProvider(timeout=60.0, error=LLMTimeoutError("Gemini request timed out"))
    planner = InvestigationPlanner(llm_provider=provider)
    req = InvestigationRequest(question="q", logs="ERROR boom")

    plan, used = planner.plan(req, ["logs"], [])

    assert used is False
    assert plan.fallback is True
    assert plan.selected_tools == ["log_analyzer"]
    assert provider.calls[0]["timeout"] == 60.0
    assert planner.last_error is not None
    assert planner.last_error.startswith("LLMTimeoutError:")
    assert "api_key" not in planner.last_error.lower()