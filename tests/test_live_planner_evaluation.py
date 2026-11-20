"""The live planner evaluation must be inert by default and strictly bounded."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.agent.models import InvestigationPlan
from backend.app.evaluation import live_planner_evaluator as live
from backend.app.evaluation.cli import main
from backend.app.evaluation.live_planner_evaluator import (
    HARD_MAX_REQUESTS,
    LIVE_EVAL_ENV,
    evaluate_live_planner,
    live_eval_enabled,
    resolve_request_limit,
)


class FakeProvider:
    """Deterministic provider double; records calls instead of making them."""

    model = "gemini-3.1-flash-lite"

    def __init__(self, plans):
        self.plans = list(plans)
        self.calls: list[dict] = []
        self.closed = False

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        plan = self.plans[(len(self.calls) - 1) % len(self.plans)]
        if isinstance(plan, Exception):
            raise plan
        return SimpleNamespace(output=plan)

    def close(self) -> None:
        self.closed = True


def _plan(*tools: str) -> InvestigationPlan:
    return InvestigationPlan(
        selected_tools=list(tools),
        rationale="Synthetic rationale for a synthetic incident.",
        available_evidence=["logs"],
        missing_information=["deployment history"],
        proposed_steps=["inspect logs"],
    )


@pytest.fixture(autouse=True)
def _no_ambient_opt_in(monkeypatch):
    """Every test starts from the zero-request default."""
    monkeypatch.delenv(LIVE_EVAL_ENV, raising=False)
    monkeypatch.delenv("OPSPILOT_LIVE_PLANNER_MAX_REQUESTS", raising=False)


def test_disabled_by_default_and_makes_no_request():
    assert live_eval_enabled() is False
    result = evaluate_live_planner()

    assert result.metrics["status"] == "skipped"
    assert result.metrics["requests_made"] == 0
    assert result.cases == []
    assert result.metrics["tools_executed"] == 0
    assert LIVE_EVAL_ENV in result.metrics["skip_reason"]


def test_env_flag_is_the_only_enable_switch(monkeypatch):
    monkeypatch.setenv(LIVE_EVAL_ENV, "1")
    assert live_eval_enabled() is True
    monkeypatch.setenv(LIVE_EVAL_ENV, "true")
    assert live_eval_enabled() is False


def test_request_limit_is_small_and_hard_capped(monkeypatch):
    assert resolve_request_limit() == 3
    assert resolve_request_limit(1) == 1
    assert resolve_request_limit(99) == HARD_MAX_REQUESTS
    assert resolve_request_limit(0) == 1
    monkeypatch.setenv("OPSPILOT_LIVE_PLANNER_MAX_REQUESTS", "not-a-number")
    assert resolve_request_limit() == 3
    monkeypatch.setenv("OPSPILOT_LIVE_PLANNER_MAX_REQUESTS", "50")
    assert resolve_request_limit() == HARD_MAX_REQUESTS


def test_budget_is_respected_and_calls_are_sequential():
    provider = FakeProvider([_plan("log_analyzer", "stack_trace_analyzer")])
    result = evaluate_live_planner(provider=provider, max_requests=2)

    assert result.metrics["status"] == "executed"
    assert len(provider.calls) == 2
    assert result.metrics["requests_made"] == 2
    assert result.metrics["sequential"] is True
    assert result.metrics["retries"] == 0
    assert result.metrics["reports_generated"] == 0
    assert result.metrics["tools_executed"] == 0
    assert provider.closed is True


def test_selected_tools_are_never_executed(monkeypatch):
    executed: list[str] = []

    def _fail(name, *args, **kwargs):
        executed.append(name)
        raise AssertionError("live planner evaluation must not execute tools")

    monkeypatch.setattr("backend.app.agent.nodes.execute_tool", _fail)
    provider = FakeProvider([_plan("log_analyzer")])

    result = evaluate_live_planner(provider=provider, max_requests=1)

    assert executed == []
    assert result.metrics["tools_executed"] == 0
    assert result.cases[0].metrics["selected_tools"] == ["log_analyzer"]


def test_valid_and_invalid_selections_are_distinguished():
    # The planner validates against the allow-list and falls back on a bad tool.
    provider = FakeProvider([_plan("rm -rf /")])
    result = evaluate_live_planner(provider=provider, max_requests=1)

    case = result.cases[0]
    assert case.passed is False
    assert case.metrics["planner_used"] is False
    assert case.metrics["error_kind"] == "invalid_selection"
    assert result.metrics["fallback_activation_rate"] == 1.0


def test_expected_tool_match_and_consistency_are_measured():
    provider = FakeProvider([_plan("http_error_classifier")])
    # Three scenarios plus one consistency repeat fit in the hard ceiling of 5.
    result = evaluate_live_planner(provider=provider, max_requests=4)

    assert result.metrics["consistency_samples"] == 1
    assert result.metrics["consistency_rate"] == 1.0
    assert result.metrics["valid_selection_rate"] == 1.0
    assert result.metrics["latency_ms_avg"] >= 0.0


def test_provider_timeout_is_classified_without_retry():
    provider = FakeProvider([TimeoutError("request timed out")])
    result = evaluate_live_planner(provider=provider, max_requests=1)

    assert len(provider.calls) == 1
    assert result.cases[0].metrics["error_kind"] == "timeout"
    assert result.metrics["error_classifications"] == {"timeout": 1}


def test_cli_refuses_live_suite_without_explicit_flag(capsys):
    code = main(["run", "--suite", "live-planner", "--output", "/tmp/live-eval-test"])

    assert code == 2
    assert "requires --live-planner-opt-in" in capsys.readouterr().err


def test_cli_skips_when_opt_in_flag_is_set_but_env_is_absent(tmp_path):
    code = main(
        [
            "run",
            "--suite",
            "live-planner",
            "--live-planner-opt-in",
            "--output",
            str(tmp_path),
        ]
    )

    assert code == 0
    report = (Path(tmp_path) / "evaluation.json").read_text(encoding="utf-8")
    assert '"requests_made": 0' in report
    assert '"status": "skipped"' in report


def test_cli_does_not_include_live_suite_in_all(tmp_path):
    code = main(["run", "--suite", "reliability", "--output", str(tmp_path)])

    assert code == 0
    report = (Path(tmp_path) / "evaluation.json").read_text(encoding="utf-8")
    assert "live-planner" not in report


def test_suite_all_never_touches_the_live_module(monkeypatch, tmp_path):
    """A regression guard: 'all' must not import or call the live evaluator."""

    def _boom(*args, **kwargs):
        raise AssertionError("live planner evaluation ran during --suite all")

    monkeypatch.setattr(live, "evaluate_live_planner", _boom)
    code = main(["run", "--suite", "diagnostics", "--output", str(tmp_path)])

    assert code == 0


def test_skip_reason_never_contains_a_secret(monkeypatch):
    monkeypatch.setenv(LIVE_EVAL_ENV, "1")
    monkeypatch.setenv("OPSPILOT_LLM_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("OPSPILOT_LLM_MODEL", "")
    monkeypatch.setenv("OPSPILOT_AI_MODE", "live")

    result = evaluate_live_planner()

    assert result.metrics["status"] == "skipped"
    assert result.metrics["requests_made"] == 0
    assert "sk-super-secret-value" not in str(result.metrics)
    assert "sk-super-secret-value" not in str(result.limitations)
    assert "sk-super-secret-value" not in sys.version


def test_report_records_zero_requests_and_no_report_generation(tmp_path):
    code = main(
        [
            "run",
            "--suite",
            "live-planner",
            "--live-planner-opt-in",
            "--live-planner-max-requests",
            "1",
            "--output",
            str(tmp_path),
        ]
    )

    assert code == 0
    report = (Path(tmp_path) / "evaluation.json").read_text(encoding="utf-8")
    assert '"tools_executed": 0' in report
    assert '"reports_generated": 0' in report
