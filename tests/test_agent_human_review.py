from __future__ import annotations

from unittest.mock import MagicMock

from backend.app.agent import InvestigationRequest, OpsPilotAgent


class FakeProvider:
    model = "fake"

    def generate_structured(
        self,
        *,
        system_instructions,
        user_input,
        output_schema,
        timeout=None,
        max_output_tokens=None,
    ):
        from backend.app.agent.models import InvestigationPlan
        from backend.app.rag.models import InvestigationReport

        if output_schema.__name__ == "InvestigationPlan":
            out = InvestigationPlan(
                selected_tools=[],
                rationale="r",
                available_evidence=[],
                missing_information=[],
                proposed_steps=[],
            )
        else:
            out = InvestigationReport(
                summary="s",
                incident_category="x",
                evidence_status="insufficient",
                root_cause_hypotheses=[],
                supporting_evidence=[],
                recommended_actions=[],
                missing_information=[],
                limitations=[],
            )
        res = MagicMock()
        res.output = out
        res.usage = None
        res.model = "fake"
        return res


def test_resume_approved():
    agent = OpsPilotAgent(llm_provider=FakeProvider(), in_memory=True)
    req = InvestigationRequest(question="q")
    agent.investigate(req, thread_id="h1")
    state = agent.resume(thread_id="h1", decision="approved")
    assert state["approval_status"] == "approved"
    assert state["execution_status"] == "completed"


def test_resume_rejected():
    agent = OpsPilotAgent(llm_provider=FakeProvider(), in_memory=True)
    req = InvestigationRequest(question="q")
    agent.investigate(req, thread_id="h2")
    state = agent.resume(thread_id="h2", decision="rejected")
    assert state["approval_status"] == "rejected"
    assert state["execution_status"] == "rejected"


def test_invalid_decision():
    agent = OpsPilotAgent(llm_provider=FakeProvider(), in_memory=True)
    req = InvestigationRequest(question="q")
    agent.investigate(req, thread_id="h3")
    state = agent.resume(thread_id="h3", decision="maybe")
    assert state["approval_status"] == "invalid"
    assert state["execution_status"] == "failed"