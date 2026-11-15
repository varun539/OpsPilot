"""High-level service interface for the OpsPilot agent."""


from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import Command

from backend.app.agent.checkpointer import SQLiteCheckpointer
from backend.app.agent.graph import build_investigation_graph
from backend.app.agent.models import InvestigationRequest
from backend.app.agent.state import DEFAULT_STATE


class OpsPilotAgent:
    """Bounded, stateful OpsPilot investigation agent."""

    def __init__(
        self,
        *,
        llm_provider: Any = None,
        retrieval_engine: Any = None,
        rag_service: Any = None,
        checkpointer: Any = None,
        db_path: str | None = None,
        in_memory: bool = False,
    ) -> None:
        self.llm_provider = llm_provider
        self.retrieval_engine = retrieval_engine
        self.rag_service = rag_service
        self._owned_sqlite: SQLiteCheckpointer | None = None

        if checkpointer is not None:
            self.checkpointer = checkpointer
        elif db_path is not None:
            self._owned_sqlite = SQLiteCheckpointer(db_path)
            self.checkpointer = self._owned_sqlite.open()
        else:
            from langgraph.checkpoint.memory import InMemorySaver

            self.checkpointer = InMemorySaver()

        self.graph = build_investigation_graph(
            llm_provider=llm_provider,
            retrieval_engine=retrieval_engine,
            rag_service=rag_service,
            checkpointer=self.checkpointer,
        )

    def investigate(
        self,
        request: Any,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(request, InvestigationRequest):
            req = request
        else:
            req = InvestigationRequest.model_validate(request)
        tid = thread_id or f"thread-{uuid.uuid4().hex}"
        state = dict(DEFAULT_STATE)
        state.update(
            {
                "request": req.model_dump(mode="json"),
                "thread_id": tid,
                "investigation_id": f"inv-{uuid.uuid4().hex[:12]}",
            }
        )
        config = {"configurable": {"thread_id": tid}}
        return self.graph.invoke(state, config=config)

    def resume(
        self,
        *,
        thread_id: str,
        decision: str,
        reviewer_comment: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"decision": decision}
        if reviewer_comment is not None:
            payload["reviewer_comment"] = reviewer_comment
        config = {"configurable": {"thread_id": thread_id}}
        return self.graph.invoke(Command(resume=payload), config=config)

    def close(self) -> None:
        if self._owned_sqlite is not None:
            self._owned_sqlite.close()
            self._owned_sqlite = None