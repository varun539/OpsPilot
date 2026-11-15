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
                rationale="none",
                available_evidence=[],
                missing_information=[],
                proposed_steps=[],
            )
        else:
            out = InvestigationReport(
                summary="done",
                incident_category="test",
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


class FakeRetrieval:
    def search(self, query, top_k=5, filters=None):
        return [
            {
                "chunk_id": "c1",
                "chunk_text": "evidence",
                "title": "t",
                "doc_id": "d1",
            }
        ]


def test_graph_end_to_end():
    agent = OpsPilotAgent(
        llm_provider=FakeProvider(),
        retrieval_engine=FakeRetrieval(),
        in_memory=True,
    )
    req = InvestigationRequest(question="q", logs="ERROR boom")
    state = agent.investigate(req, thread_id="g1")
    assert state["execution_status"] == "validated_report"
    assert "__interrupt__" in state


def test_insufficient_evidence_path():
    agent = OpsPilotAgent(llm_provider=FakeProvider(), in_memory=True)
    req = InvestigationRequest(question="q")
    state = agent.investigate(req, thread_id="g2")
    assert state["evidence_status"] == "insufficient"