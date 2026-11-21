"""Investigation planner tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.app.agent.models import InvestigationPlan, InvestigationRequest
from backend.app.agent.planner import TOOL_DESCRIPTIONS, InvestigationPlanner
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


def test_fallback_without_provider() -> None:
    req = InvestigationRequest(question="q", logs="ERROR boom")
    planner = InvestigationPlanner(llm_provider=None)
    plan, used = planner.plan(req, ["logs"], [])
    assert used is False
    assert plan.fallback is True
    assert "log_analyzer" in plan.selected_tools


def test_unknown_tool_rejected() -> None:
    planner = InvestigationPlanner(llm_provider=None)
    plan = InvestigationPlan(selected_tools=["bad_tool"], rationale="r")
    try:
        planner._validate_plan(plan, ["logs"])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_max_tool_count() -> None:
    planner = InvestigationPlanner(llm_provider=None)
    plan = InvestigationPlan(selected_tools=["a", "b", "c", "d", "e", "f", "g"], rationale="r")
    try:
        planner._validate_plan(plan, [])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_provider_failure_falls_back() -> None:
    provider = MagicMock()
    provider.generate_structured.side_effect = RuntimeError("boom")
    req = InvestigationRequest(question="q", logs="ERROR boom")
    planner = InvestigationPlanner(llm_provider=provider)
    plan, used = planner.plan(req, ["logs"], [])
    assert used is False
    assert plan.fallback is True


def test_planner_uses_provider_default_timeout() -> None:
    provider = FakeProvider()
    planner = InvestigationPlanner(llm_provider=provider)
    assert planner.timeout == DEFAULT_TIMEOUT


def test_planner_without_provider_config_uses_default_timeout() -> None:
    planner = InvestigationPlanner(llm_provider=MagicMock())
    assert planner.timeout == DEFAULT_TIMEOUT


def test_planner_uses_configured_timeout() -> None:
    provider = FakeProvider(timeout=75.0)
    planner = InvestigationPlanner(llm_provider=provider)
    assert planner.timeout == 75.0

    req = InvestigationRequest(question="q", logs="ERROR boom")
    plan, used = planner.plan(req, ["logs"], [])
    assert used is True
    assert plan.selected_tools == ["log_analyzer"]
    assert provider.calls[0]["timeout"] == 75.0


def test_planner_rejects_invalid_timeout_override() -> None:
    provider = FakeProvider(timeout=45.0)
    assert InvestigationPlanner(llm_provider=provider, timeout=0).timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout=-5).timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout=500).timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout="abc").timeout == 45.0
    assert InvestigationPlanner(llm_provider=provider, timeout=12.5).timeout == 12.5


def test_planner_timeout_falls_back_deterministically_and_observably() -> None:
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


# Planner prompt enumeration tests (Phase 24)

def test_plan_receives_all_six_tool_ids_in_system_instructions() -> None:
    """Verify generate_structured() receives all 6 authorized tool IDs in system_instructions."""
    from unittest.mock import MagicMock

    provider = MagicMock()
    planner = InvestigationPlanner(llm_provider=provider)
    req = InvestigationRequest(question="q", logs="ERROR boom", incident_context="test incident")
    plan, used = planner.plan(req, ["logs"], [])

    calls = provider.generate_structured.call_args
    assert calls is not None, "generate_structured was not called"
    system_instructions = calls[1].get("system_instructions", "")

    # All 6 tool IDs should appear in system_instructions
    expected_ids = ["log_analyzer", "stack_trace_analyzer", "http_error_classifier",
                    "config_validator", "database_diagnostics", "incident_correlator"]
    for tid in expected_ids:
        assert tid in system_instructions, f"Tool ID {tid!r} not found in system_instructions"


def test_plan_receives_all_six_tool_descriptions_in_system_instructions() -> None:
    """Verify generate_structured() receives all 6 tool descriptions in system_instructions."""
    provider = MagicMock()
    planner = InvestigationPlanner(llm_provider=provider)
    req = InvestigationRequest(question="q", logs="ERROR boom", incident_context="test incident")
    plan, used = planner.plan(req, ["logs"], [])

    calls = provider.generate_structured.call_args
    assert calls is not None, "generate_structured was not called"
    system_instructions = calls[1].get("system_instructions", "")

    # All 6 descriptions should appear in system_instructions
    for tid in TOOL_DESCRIPTIONS.keys():
        assert tid in system_instructions, f"Tool {tid!r} not found in system_instructions"
        desc = TOOL_DESCRIPTIONS[tid]
        assert desc in system_instructions, f"Missing desc for {tid}"


def test_plan_passes_formatted_prompt_with_tool_enumeration() -> None:
    """Verify the actual plan() method passes the formatted prompt with tool enumeration."""
    provider = MagicMock()
    planner = InvestigationPlanner(llm_provider=provider)
    req = InvestigationRequest(question="q", logs="ERROR boom", incident_context="test incident")
    plan, used = planner.plan(req, ["logs"], [])

    calls = provider.generate_structured.call_args
    assert calls is not None, "generate_structured was not called"
    system_instructions = calls[1].get("system_instructions", "")

    # System prompt should contain the formatted tool descriptions (not the raw template)
    assert "{tool_descriptions}" not in system_instructions, "template not substituted"
    assert "{allowed_tool_prompt}" not in system_instructions, "prompt not substituted"

    # Should contain actual tool IDs and descriptions
    for tid in TOOL_DESCRIPTIONS.keys():
        assert tid in system_instructions, f"Missing tool {tid}"

    # Should contain the ALLOWED_TOOL_PROMPT structure with tool IDs
    assert "You may select one or more of the following tools" in system_instructions


def test_unknown_tool_rejected_by_validate_plan() -> None:
    """Verify _validate_plan rejects unknown tools."""
    planner = InvestigationPlanner(llm_provider=None)
    plan = InvestigationPlan(selected_tools=["nonexistent_tool"], rationale="r")
    try:
        planner._validate_plan(plan, ["logs"])
        assert False, "Expected ValueError for unknown tool"
    except ValueError:
        pass  # Expected


def test_duplicate_tool_rejected_by_validate_plan() -> None:
    """Verify _validate_plan rejects duplicate tool selections."""
    planner = InvestigationPlanner(llm_provider=None)
    plan = InvestigationPlan(selected_tools=["log_analyzer", "log_analyzer"], rationale="r")
    try:
        planner._validate_plan(plan, ["logs"])
        assert False, "Expected ValueError for duplicate tool"
    except ValueError:
        pass  # Expected


def test_fallback_continues_working() -> None:
    """Verify deterministic fallback still works with new planner prompt."""
    planner = InvestigationPlanner(llm_provider=None)
    req = InvestigationRequest(question="q", logs="ERROR boom")
    plan, used = planner.plan(req, ["logs"], [])

    assert used is False
    assert plan.fallback is True
    assert "log_analyzer" in plan.selected_tools


def _normalize_query_text(text: str) -> str:
    """Normalize query text for overlap detection.

    Lowercases, drops the 'internal server error' qualifier and the
    'after (an) unhandled exception' suffix, then collapses whitespace.
    This makes hq16b ("...HTTP 500 after unhandled exception") and
    q01 ("...HTTP 500 Internal Server Error after an unhandled exception")
    compare equal while keeping other queries distinct.
    """
    normalized = text.lower()
    normalized = normalized.replace("internal server error", " ")
    normalized = normalized.replace("after an unhandled exception", " ")
    normalized = normalized.replace("after unhandled exception", " ")
    return " ".join(normalized.split())


def test_heldout_dataset_structure() -> None:
    """Verify held-out dataset structure: 19 queries, 16 unanswerable + 3 answerable.

    Also verifies data leakage between held-out and eval datasets: 3 answerable
    held-out queries (hq16b, hq17, hq18) have overlapping text and KB labels
    with queries in retrieval_eval.json.
    """
    import json

    with open("tests/data/retrieval_heldout_v1.json") as f:
        heldout = json.load(f)
    with open("tests/data/retrieval_eval.json") as f:
        eval_data = json.load(f)

    queries = heldout["queries"]
    assert len(queries) == 19, f"Expected 19 held-out queries, got {len(queries)}"

    unanswerable = [q for q in queries if not q["relevant_doc_ids"]]
    answerable = [q for q in queries if q["relevant_doc_ids"]]
    assert len(unanswerable) == 16, f"Expected 16 unanswerable queries, got {len(unanswerable)}"
    assert len(answerable) == 3, f"Expected 3 answerable queries, got {len(answerable)}"

    # Verify overlap with eval dataset (data leakage check)
    # Match by normalized query text since IDs differ (hq16b vs q01, etc.)
    eval_queries = {_normalize_query_text(q["query"]): q for q in eval_data["queries"]}

    overlaps = []
    for hq in answerable:
        matched_eval = eval_queries.get(_normalize_query_text(hq["query"]))
        if matched_eval is not None:
            overlaps.append({
                "heldout_id": hq["id"],
                "eval_id": matched_eval["id"],
                "heldout_query": hq["query"],
                "eval_query": matched_eval["query"],
                "heldout_docs": list(hq["relevant_doc_ids"]),
                "eval_docs": list(matched_eval["relevant_doc_ids"]),
            })

    assert len(overlaps) == 3, (
        f"Expected exactly 3 overlapping answerable queries, got {len(overlaps)}"
    )
    # Verify the three expected overlaps: hq16b, hq17, hq18
    overlap_ids = {o["heldout_id"] for o in overlaps}
    expected_ids = {"hq16b", "hq17", "hq18"}
    assert overlap_ids == expected_ids, f"Expected overlaps {expected_ids}, got {overlap_ids}"
    # Verify the expected held-out -> eval id pairing
    expected_pairs = {"hq16b": "q01", "hq17": "q03", "hq18": "q04"}
    actual_pairs = {o["heldout_id"]: o["eval_id"] for o in overlaps}
    assert actual_pairs == expected_pairs, f"Expected pairs {expected_pairs}, got {actual_pairs}"
    # Verify normalized text and relevant document labels match
    for o in overlaps:
        heldout_norm = _normalize_query_text(o["heldout_query"])
        eval_norm = _normalize_query_text(o["eval_query"])
        assert heldout_norm == eval_norm, (
            f"Normalized query text mismatch for {o['heldout_id']} -> {o['eval_id']}"
        )
        assert o["heldout_docs"] == o["eval_docs"], (
            f"Relevant doc label mismatch for {o['heldout_id']} -> {o['eval_id']}: "
            f"{o['heldout_docs']} vs {o['eval_docs']}"
        )
        assert o["heldout_docs"], f"No relevant doc labels for {o['heldout_id']}"
