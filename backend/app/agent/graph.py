"""LangGraph construction for OpsPilot investigations."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.app.agent.nodes import (
    classify_incident,
    correlate_findings,
    execute_diagnostics,
    finalize,
    human_review,
    plan_investigation,
    retrieve_knowledge,
    synthesize_report,
    validate_report,
    validate_request,
)
from backend.app.agent.state import InvestigationState


def _bind(node, **deps: Any):
    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        return node(state, **deps)

    return wrapped


def _route_after_validate(state: dict[str, Any]) -> str:
    if (state or {}).get("execution_status") == "failed":
        return "finalize"
    return "classify_incident"


def _route_after_plan(state: dict[str, Any]) -> str:
    if (state or {}).get("execution_status") == "failed":
        return "finalize"
    return "execute_diagnostics"


def build_investigation_graph(
    *,
    llm_provider: Any = None,
    retrieval_engine: Any = None,
    rag_service: Any = None,
    checkpointer: Any = None,
) -> Any:
    graph = StateGraph(InvestigationState)
    graph.add_node("validate_request", _bind(validate_request))
    graph.add_node("classify_incident", _bind(classify_incident))
    graph.add_node(
        "retrieve_knowledge",
        _bind(retrieve_knowledge, retrieval_engine=retrieval_engine),
    )
    graph.add_node("plan_investigation", _bind(plan_investigation, llm_provider=llm_provider))
    graph.add_node("execute_diagnostics", _bind(execute_diagnostics))
    graph.add_node("correlate_findings", _bind(correlate_findings))
    graph.add_node(
        "synthesize_report",
        _bind(
            synthesize_report,
            llm_provider=llm_provider,
            rag_service=rag_service,
        ),
    )
    graph.add_node("validate_report", _bind(validate_report))
    graph.add_node("human_review", _bind(human_review))
    graph.add_node("finalize", _bind(finalize))

    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges(
        "validate_request",
        _route_after_validate,
        {"classify_incident": "classify_incident", "finalize": "finalize"},
    )
    graph.add_edge("classify_incident", "retrieve_knowledge")
    graph.add_edge("retrieve_knowledge", "plan_investigation")
    graph.add_conditional_edges(
        "plan_investigation",
        _route_after_plan,
        {"execute_diagnostics": "execute_diagnostics", "finalize": "finalize"},
    )
    graph.add_edge("execute_diagnostics", "correlate_findings")
    graph.add_edge("correlate_findings", "synthesize_report")
    graph.add_edge("synthesize_report", "validate_report")
    graph.add_edge("validate_report", "human_review")
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)

    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    return graph.compile(**compile_kwargs)


def create_investigation_graph(*args: Any, **kwargs: Any) -> Any:
    return build_investigation_graph(*args, **kwargs)
