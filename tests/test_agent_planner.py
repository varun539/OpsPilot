from __future__ import annotations

from unittest.mock import MagicMock

from backend.app.agent.models import InvestigationPlan, InvestigationRequest
from backend.app.agent.planner import InvestigationPlanner


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