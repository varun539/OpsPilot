from __future__ import annotations

import tempfile
from pathlib import Path
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


def test_sqlite_resume():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "cp.sqlite"
        agent = OpsPilotAgent(llm_provider=FakeProvider(), db_path=db)
        req = InvestigationRequest(question="q")
        agent.investigate(req, thread_id="c1")
        agent.close()
        agent2 = OpsPilotAgent(llm_provider=FakeProvider(), db_path=db)
        state = agent2.resume(thread_id="c1", decision="approved")
        assert state["execution_status"] == "completed"
        agent2.close()


def test_thread_isolation():
    agent = OpsPilotAgent(llm_provider=FakeProvider(), in_memory=True)
    req = InvestigationRequest(question="q")
    agent.investigate(req, thread_id="a")
    agent.investigate(req, thread_id="b")
    st = agent.resume(thread_id="a", decision="approved")
    assert st["thread_id"] == "a"