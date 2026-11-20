"""Full-stack AI investigation test with real ChromaDB and hybrid retrieval.

The LLM is a deterministic fake (no network); embeddings are produced by a
deterministic stub model. The HTTP layer, dependency assembly, persistent
ChromaDB collection, BM25 index, RRF fusion, RAG service, LangGraph graph,
approval interrupt and SQLite persistence are all real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.agent.models import InvestigationPlan
from backend.app.ai import AIRuntimeConfig
from backend.app.ai.runtime import assemble_ai_runtime
from backend.app.auth.config import AuthSettings
from backend.app.indexing import index_knowledge_base
from backend.app.llm.config import LLMConfig
from backend.app.main import create_app
from backend.app.rag.models import InvestigationReport
from backend.app.vectorstore import ChromaVectorStore
from tests.ai_fakes import StubEmbeddingModel

DOC = """---
id: KB-001
title: Connection pool exhaustion
category: database
component: api
severity: high
tags: [database, pool, latency]
source_type: synthetic
---

Checkout latency and HTTP 500 responses correlate with exhausted database
connection pools. Inspect pool saturation and slow queries before raising the
connection limit.
"""


class RecordingProvider:
    model = "stub-model"

    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id
        self.user_inputs: list[str] = []

    def generate_structured(
        self,
        *,
        system_instructions: str,
        user_input: str,
        output_schema: type[Any],
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any:
        self.user_inputs.append(user_input)
        if output_schema.__name__ == "InvestigationPlan":
            output: Any = InvestigationPlan(
                selected_tools=["log_analyzer"], rationale="Pool symptoms in logs"
            )
        else:
            output = InvestigationReport(
                summary=(
                    "Checkout failures are consistent with database connection "
                    "pool exhaustion."
                ),
                incident_category="database",
                affected_components=["checkout-api", "postgres"],
                evidence_status="sufficient",
                root_cause_hypotheses=[
                    {
                        "title": "Connection pool exhaustion",
                        "explanation": (
                            "The retrieved runbook links latency and 500s to "
                            "exhausted pooled connections."
                        ),
                        "supporting_chunk_ids": [self.chunk_id],
                        "evidence_strength": "high",
                    }
                ],
                supporting_evidence=[
                    {
                        "chunk_id": self.chunk_id,
                        "doc_id": "KB-001",
                        "title": "Connection pool exhaustion",
                        "reason": "Documents the matching failure mode.",
                    }
                ],
                recommended_actions=[
                    {
                        "description": "Verify pool saturation and slow queries.",
                        "rationale": "Confirms the leading hypothesis.",
                        "supporting_chunk_ids": [self.chunk_id],
                        "risk_level": "low",
                        "requires_human_review": True,
                    }
                ],
            )

        class _Result:
            def __init__(self, value: Any) -> None:
                self.output = value
                self.usage = None
                self.model = "stub-model"

        return _Result(output)


def test_grounded_investigation_end_to_end(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "KB-001.md").write_text(DOC, encoding="utf-8")

    model = StubEmbeddingModel()
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    indexed = index_knowledge_base(directory=kb, store=store, model=model)
    assert indexed.collection_count == indexed.token_chunks
    chunk_id = store.get(limit=1)["ids"][0]

    provider = RecordingProvider(chunk_id)
    runtime = assemble_ai_runtime(
        AIRuntimeConfig(mode="live", llm=LLMConfig(api_key="unused")),
        provider=provider,
        model=model,
        store=store,
    )
    assert runtime.ready

    app = create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=AuthSettings(
            auth_mode="development", dev_auth_enabled=True, host="127.0.0.1"
        ),
        ai_runtime=runtime,
    )

    with TestClient(app) as client:
        readiness = client.get("/health/ai").json()
        assert readiness["ready"] is True
        assert readiness["knowledge_chunks"] == indexed.token_chunks

        incident = client.post(
            "/api/v1/incidents",
            json={
                "title": "Checkout latency and 500s",
                "description": "Database connection pool exhaustion suspected.",
                "severity": "high",
            },
        ).json()

        started = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations",
            json={"logs": "ERROR pool exhausted: timeout acquiring connection"},
        )
        assert started.status_code == 202, started.text
        body = started.json()
        assert body["workflow_status"] == "awaiting_approval"
        investigation_id = body["investigation_id"]
        thread_id = client.get(
            f"/api/v1/investigations/{investigation_id}"
        ).json()["thread_id"]

        detail = client.get(f"/api/v1/investigations/{investigation_id}").json()
        assert detail["report"] is not None
        assert "pool exhaustion" in detail["report"]["summary"].lower()

        evidence = client.get(
            f"/api/v1/investigations/{investigation_id}/evidence"
        ).json()
        knowledge = [item for item in evidence if item["evidence_type"] == "knowledge_base"]
        assert knowledge, evidence
        assert knowledge[0]["source"] == chunk_id

        # The report prompt must contain the actual persisted chunk text.
        report_prompts = [text for text in provider.user_inputs if "pool saturation" in text]
        assert report_prompts, "retrieved knowledge was not passed to the model"

        approval = client.post(
            f"/api/v1/investigations/{investigation_id}/approval",
            json={"decision": "approved", "reason": "grounded report"},
        )
        assert approval.status_code == 200, approval.text
        assert approval.json()["workflow_status"] == "completed"

        final = client.get(f"/api/v1/investigations/{investigation_id}").json()
        assert final["workflow_status"] == "completed"
        assert final["thread_id"] == thread_id
        approval_record = client.get(
            f"/api/v1/investigations/{investigation_id}/approval"
        ).json()
        assert approval_record["status"] == "approved"

    runtime.close()
    store.close()
