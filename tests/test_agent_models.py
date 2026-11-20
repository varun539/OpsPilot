from __future__ import annotations

import json

from pydantic import ValidationError

from backend.app.agent.models import InvestigationPlan, InvestigationRequest


def test_valid_request():
    req = InvestigationRequest(question="What happened?", http_status=500)
    assert req.question == "What happened?"
    assert req.http_status == 500


def test_invalid_http_status():
    try:
        InvestigationRequest(question="q", http_status=999)
    except ValidationError:
        pass
    else:
        raise AssertionError("expected validation error")


def test_request_serialization():
    req = InvestigationRequest(question="q", logs="ERROR boom")
    data = req.model_dump(mode="json")
    assert json.dumps(data)


def test_plan_defaults():
    plan = InvestigationPlan(rationale="r")
    assert plan.selected_tools == []
    assert plan.fallback is False