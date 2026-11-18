"""Opt-in integration test: real embeddings inside the LangGraph workflow.

Skipped unless ``OPSPILOT_LOCAL_EMBEDDING_TESTS=1``. Unlike the fast unit tests,
this test loads the pinned CPU sentence-transformer from the local cache, indexes
the eight synthetic knowledge-base documents into a temporary persistent ChromaDB
collection, and drives the real investigation graph, approval interrupt, resume
and SQLite persistence against temporary databases.

Report generation is scripted: no provider API is contacted. The scripted
provider reads the evidence chunk IDs out of the prompt produced by the real
``RAGService``, so the report is grounded in whatever retrieval actually
returned instead of a hardcoded answer.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.agent.models import InvestigationPlan
from backend.app.evaluation.dataset import load_incidents
from backend.app.incidents import IncidentService
from backend.app.incidents.exceptions import InvalidStateTransitionError
from backend.app.orchestration.schemas import InvestigationStartRequest
from backend.app.orchestration.service import InvestigationOrchestrationService
from backend.app.rag.citation_validator import CitationValidator
from backend.app.rag.generator import RAGService
from backend.app.rag.models import EvidenceReference, InvestigationReport

pytestmark = pytest.mark.skipif(
    os.getenv("OPSPILOT_LOCAL_EMBEDDING_TESTS") != "1",
    reason=(
        "set OPSPILOT_LOCAL_EMBEDDING_TESTS=1 to run the real local embedding "
        "integration test (loads the pinned model from the local cache)"
    ),
)

#: The representative multi-symptom scenario requested for this integration.
CASE_ID = "INC-011"

#: Chunk ID shape that no knowledge-base document can produce.
INVENTED_CHUNK_ID = "KB-999::invented::token-000"

#: Deterministic input → tool mapping used to script the planner.
INPUT_TO_TOOL = {
    "logs": "log_analyzer",
    "stack_trace": "stack_trace_analyzer",
    "http_status": "http_error_classifier",
    "configuration": "config_validator",
    "database_error": "database_diagnostics",
    "events": "incident_correlator",
}


class Principal:
    """Minimal authenticated principal."""

    def __init__(self, subject: str) -> None:
        self.subject = subject


def _provider_result(output: Any) -> Any:
    return SimpleNamespace(
        output=output,
        usage=None,
        model=ScriptedEvidenceProvider.model,
        metadata={"provider": ScriptedEvidenceProvider.provider_name},
    )


def _evidence_blocks(prompt: str) -> list[dict[str, str]]:
    """Parse the ``[EVIDENCE n]`` blocks out of a real RAG user prompt."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_body = False
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("[EVIDENCE "):
            current = {}
            blocks.append(current)
            in_body = False
            continue
        if stripped == "<untrusted_content>":
            in_body = True
            continue
        if stripped == "</untrusted_content>":
            in_body = False
            continue
        if current is None or in_body:
            continue
        key, separator, value = stripped.partition(": ")
        if separator and key in {"chunk_id", "doc_id", "title", "source_path"} and value:
            current[key] = value
    return blocks


def _tools_from_available_inputs(prompt: str) -> list[str]:
    lines = [line.strip() for line in prompt.splitlines()]
    try:
        index = lines.index("Available diagnostic inputs:")
    except ValueError:
        return []
    if index + 1 >= len(lines):
        return []
    names = [name.strip() for name in lines[index + 1].split(",")]
    return [INPUT_TO_TOOL[name] for name in names if name in INPUT_TO_TOOL]


class ScriptedEvidenceProvider:
    """Deterministic provider that reports only on the evidenced chunks it is given."""

    provider_name = "scripted-local-retrieval"
    model = "scripted-local-retrieval-v1"

    def __init__(self, *, invented_chunk_id: str | None = None) -> None:
        self.invented_chunk_id = invented_chunk_id
        self.report_prompts: list[str] = []
        self.plan_prompts: list[str] = []

    def generate_structured(
        self,
        *,
        system_instructions: str,
        user_input: str,
        output_schema: Any,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any:
        if output_schema is InvestigationPlan:
            self.plan_prompts.append(user_input)
            return _provider_result(
                InvestigationPlan(
                    selected_tools=_tools_from_available_inputs(user_input),
                    rationale="Deterministic scripted plan from the available diagnostic inputs.",
                    fallback=True,
                )
            )
        self.report_prompts.append(user_input)
        return _provider_result(self._report(user_input))

    def _report(self, prompt: str) -> InvestigationReport:
        blocks = _evidence_blocks(prompt)
        chunk_ids = [block["chunk_id"] for block in blocks if block.get("chunk_id")]
        cited_ids = list(chunk_ids)
        if self.invented_chunk_id is not None:
            cited_ids = cited_ids[:1] + [self.invented_chunk_id]
        doc_ids = sorted({block.get("doc_id", "") for block in blocks if block.get("doc_id")})
        return InvestigationReport(
            summary=(
                f"Scripted report grounded in {len(blocks)} retrieved chunk(s) "
                f"from {len(doc_ids)} document(s)."
            ),
            incident_category="retrieval_grounded",
            affected_components=doc_ids,
            evidence_status="sufficient",
            root_cause_hypotheses=[
                {
                    "title": "Retrieved knowledge-base documents describe the reported symptoms",
                    "explanation": (
                        "The hypothesis is limited to what the retrieved chunks state; no "
                        "external knowledge is used."
                    ),
                    "supporting_chunk_ids": cited_ids,
                    "evidence_strength": "medium",
                }
            ],
            supporting_evidence=[
                EvidenceReference(
                    chunk_id=block["chunk_id"],
                    doc_id=block.get("doc_id", ""),
                    title=block.get("title", ""),
                    reason="Returned by hybrid retrieval for this incident question.",
                )
                for block in blocks
                if block.get("chunk_id")
            ],
            recommended_actions=[
                {
                    "description": "Have an operator verify the cited documents against telemetry.",
                    "rationale": "Scripted reports never execute remediation.",
                    "supporting_chunk_ids": cited_ids,
                    "risk_level": "low",
                    "requires_human_review": True,
                }
            ],
            missing_information=[],
            limitations=[
                "Scripted generation: wording is deterministic and does not measure "
                "live model language quality."
            ],
        )


def _incident_case() -> Any:
    for case in load_incidents():
        if case.case_id == CASE_ID:
            return case
    raise AssertionError(f"dataset case {CASE_ID} is missing")


def _local_runtime() -> dict[str, Any]:
    """Index the synthetic knowledge base with the real pinned model, offline."""
    from backend.app.embeddings import (
        EXPECTED_DIMENSION,
        MODEL_ID,
        MODEL_REVISION,
        load_embedding_model,
    )
    from backend.app.indexing import index_knowledge_base
    from backend.app.retrieval import BM25SearchEngine, HybridSearchEngine, SemanticSearchEngine
    from backend.app.vectorstore import ChromaVectorStore

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    tempdir = Path(tempfile.mkdtemp(prefix="opspilot-agent-real-retrieval-"))
    store = ChromaVectorStore(persist_directory=tempdir, expected_dimension=EXPECTED_DIMENSION)
    try:
        model = load_embedding_model(
            device="cpu",
            local_files_only=True,
            model_id=MODEL_ID,
            revision=MODEL_REVISION,
        )
        indexed = index_knowledge_base(store=store, model=model, local_files_only=True)
        semantic = SemanticSearchEngine(
            store, model=model, expected_dimension=EXPECTED_DIMENSION
        )
        bm25 = BM25SearchEngine(store)
        bm25.build_index()
        hybrid = HybridSearchEngine(semantic_engine=semantic, bm25_engine=bm25)
    except Exception:
        store.close()
        shutil.rmtree(tempdir, ignore_errors=True)
        raise

    runtime = {
        "store": store,
        "model": model,
        "semantic": semantic,
        "bm25": bm25,
        "hybrid": hybrid,
        "indexed": indexed,
        "tempdir": tempdir,
    }
    runtime["close"] = _close_runtime(runtime)
    return runtime


def _close_runtime(runtime: dict[str, Any]) -> Any:
    def close() -> None:
        runtime["store"].close()
        shutil.rmtree(runtime["tempdir"], ignore_errors=True)

    return close


@pytest.fixture(scope="module")
def local_retrieval() -> Any:
    runtime = _local_runtime()
    try:
        yield runtime
    finally:
        runtime["close"]()


def _orchestration(
    *, app_db: Path, checkpoint_db: Path, runtime: dict[str, Any], provider: Any
) -> tuple[IncidentService, InvestigationOrchestrationService]:
    incident_service = IncidentService(db_path=str(app_db))
    orchestration = InvestigationOrchestrationService(
        incident_service=incident_service,
        checkpoint_db_path=str(checkpoint_db),
        llm_provider=provider,
        retrieval_engine=runtime["hybrid"],
        rag_service=RAGService(runtime["hybrid"], provider),
    )
    return incident_service, orchestration


def _graph_state(orchestration: InvestigationOrchestrationService, thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = orchestration.agent.graph.get_state(config)
    return dict(getattr(snapshot, "values", None) or {})


def _start(
    incident_service: IncidentService,
    orchestration: InvestigationOrchestrationService,
    case: Any,
) -> tuple[Any, Any]:
    incident = incident_service.create_incident(title=case.title)
    started = orchestration.start_investigation(
        incident.id,
        InvestigationStartRequest(**case.request.model_dump(exclude_none=True)),
        principal=Principal("phase20-start"),
    )
    return incident, started


def test_real_embedding_index_is_populated(local_retrieval: dict[str, Any]) -> None:
    indexed = local_retrieval["indexed"]
    assert indexed.dry_run is False
    assert indexed.documents > 0
    assert indexed.token_chunks > 0
    assert indexed.collection_count == indexed.token_chunks
    assert int(local_retrieval["store"].count()) == indexed.collection_count


def test_inc011_runs_through_graph_with_real_retrieval(
    tmp_path: Path, local_retrieval: dict[str, Any]
) -> None:
    case = _incident_case()
    expected_docs = set(case.expected.relevant_doc_ids)
    provider = ScriptedEvidenceProvider()
    incident_service, orchestration = _orchestration(
        app_db=tmp_path / "app.sqlite",
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        runtime=local_retrieval,
        provider=provider,
    )
    try:
        incident, started = _start(incident_service, orchestration, case)

        # The real graph stopped at the human-review interrupt.
        assert started.workflow_status == "awaiting_approval"
        assert started.approval_status == "pending"

        investigation = incident_service.get_investigation(started.investigation_id)
        thread_id = investigation.thread_id
        state = _graph_state(orchestration, thread_id)

        # Deterministic diagnostics ran for the available inputs.
        expected_tools = {
            INPUT_TO_TOOL[name] for name in state["available_inputs"] if name in INPUT_TO_TOOL
        }
        assert set(state["selected_tools"]) == expected_tools
        assert len(state["selected_tools"]) == len(expected_tools)
        assert set(state["diagnostic_results"]) >= {
            "log_analyzer",
            "http_error_classifier",
            "database_diagnostics",
        }

        # Hybrid retrieval returned real ChromaDB matches with the Phase 18 cap.
        results = state["retrieval_results"]
        assert len(results) == 5
        chunk_ids = [item["chunk_id"] for item in results]
        doc_ids = [item["doc_id"] for item in results]
        assert set(doc_ids) >= expected_docs
        assert max(Counter(doc_ids).values()) <= 2
        assert len(set(doc_ids)) >= 4
        assert all(item["chunk_text"] for item in results)
        assert all(item["source_path"] for item in results)
        assert state["evidence_status"] == "sufficient"

        # Same engine, query and window as standalone local retrieval.
        direct = local_retrieval["hybrid"].search(case.request.question, top_k=5)
        assert [item.chunk_id for item in direct] == chunk_ids

        # The generator saw exactly the retrieved evidence, in rank order.
        assert len(provider.report_prompts) == 1
        assert [
            block["chunk_id"] for block in _evidence_blocks(provider.report_prompts[0])
        ] == chunk_ids

        # The report cites only retrieved IDs and covers both expected documents.
        response = state["investigation_report"]
        assert response["generation_status"] == "success"
        assert response["citation_valid"] is True
        assert response["validation_errors"] == []
        assert response["provider"] == provider.provider_name
        cited = response["report"]["root_cause_hypotheses"][0]["supporting_chunk_ids"]
        cited_docs = {
            item["doc_id"] for item in results if item["chunk_id"] in set(cited)
        }
        assert set(cited) <= set(chunk_ids)
        assert cited_docs >= expected_docs

        # Pending approval is required before the result is persisted as complete.
        approval = orchestration.get_approval(started.investigation_id)
        assert approval.status == "pending"
        assert incident_service.get_investigation(started.investigation_id).status != "completed"

        decided = orchestration.approve_investigation(
            started.investigation_id,
            "approved",
            reason="reviewed the retrieved evidence",
            principal=Principal("phase20-reviewer"),
        )
        assert decided.workflow_status == "completed"
        assert decided.approval_status == "approved"

        final_state = _graph_state(orchestration, thread_id)
        assert final_state["execution_status"] == "completed"
        assert final_state["approval_status"] == "approved"
        assert final_state["retrieval_results"] == results

        # Report, evidence and event history are persisted in the temp app DB.
        final_investigation = incident_service.get_investigation(started.investigation_id)
        assert final_investigation.status == "completed"
        assert final_investigation.thread_id == thread_id
        assert final_investigation.report["summary"]
        assert final_investigation.report["citation_valid"] is True

        evidence = incident_service.list_evidence(started.investigation_id)
        knowledge_sources = {
            item.source for item in evidence if item.evidence_type == "knowledge_base"
        }
        assert set(chunk_ids) <= knowledge_sources
        assert "log_analyzer" in {
            item.source for item in evidence if item.evidence_type == "diagnostic"
        }

        event_types = [
            event.event_type for event in incident_service.list_incident_events(incident.id)
        ]
        assert event_types == [
            "investigation_started",
            "human_review_requested",
            "approval_approved",
            "workflow_resumed",
            "investigation_completed",
        ]

        # A decided investigation cannot be resumed again.
        with pytest.raises(InvalidStateTransitionError):
            orchestration.approve_investigation(
                started.investigation_id, "approved", principal=Principal("phase20-reviewer")
            )
    finally:
        orchestration.close()


def test_invented_citations_are_rejected_against_real_evidence(
    local_retrieval: dict[str, Any],
) -> None:
    case = _incident_case()
    evidence = local_retrieval["hybrid"].search(case.request.question, top_k=5)
    assert evidence

    honest = ScriptedEvidenceProvider()
    accepted = RAGService(local_retrieval["hybrid"], honest).investigate_with_evidence(
        case.request.question, evidence
    )
    assert accepted.generation_status == "success"
    assert accepted.citation_valid is True
    assert CitationValidator().validate(accepted.report, accepted.retrieved_evidence).valid

    inventing = ScriptedEvidenceProvider(invented_chunk_id=INVENTED_CHUNK_ID)
    rejected = RAGService(local_retrieval["hybrid"], inventing).investigate_with_evidence(
        case.request.question, evidence
    )
    assert rejected.generation_status == "citation_validation_failed"
    assert rejected.citation_valid is False
    assert rejected.valid is False
    assert any(INVENTED_CHUNK_ID in error for error in rejected.validation_errors)


def test_approval_survives_restart_and_cannot_be_bypassed(
    tmp_path: Path, local_retrieval: dict[str, Any]
) -> None:
    case = _incident_case()
    app_db = tmp_path / "app.sqlite"
    checkpoint_db = tmp_path / "checkpoints.sqlite"

    provider = ScriptedEvidenceProvider()
    incident_service, orchestration = _orchestration(
        app_db=app_db, checkpoint_db=checkpoint_db, runtime=local_retrieval, provider=provider
    )
    incident, started = _start(incident_service, orchestration, case)
    thread_id = incident_service.get_investigation(started.investigation_id).thread_id
    assert started.workflow_status == "awaiting_approval"

    # A not-yet-approved investigation has no completion event.
    types_before = [
        event.event_type for event in incident_service.list_incident_events(incident.id)
    ]
    assert "investigation_completed" not in types_before
    orchestration.close()

    # Simulate a process restart: fresh service objects over the same temp DBs.
    restarted_provider = ScriptedEvidenceProvider()
    restarted_service, restarted = _orchestration(
        app_db=app_db,
        checkpoint_db=checkpoint_db,
        runtime=local_retrieval,
        provider=restarted_provider,
    )
    try:
        restored = restarted_service.get_investigation(started.investigation_id)
        assert restored.workflow_status == "awaiting_approval"
        assert restored.thread_id == thread_id
        assert restarted.get_approval(started.investigation_id).status == "pending"
        assert _graph_state(restarted, thread_id)["retrieval_results"]

        decided = restarted.approve_investigation(
            started.investigation_id,
            "approved",
            reason="resumed after restart",
            principal=Principal("phase20-reviewer"),
        )
        assert decided.workflow_status == "completed"

        final_state = _graph_state(restarted, thread_id)
        assert final_state["execution_status"] == "completed"
        assert final_state["approval_status"] == "approved"
        assert len(final_state["retrieval_results"]) == 5
        assert restarted_service.get_investigation(started.investigation_id).status == "completed"

        types_after = [
            event.event_type for event in restarted_service.list_incident_events(incident.id)
        ]
        assert types_after == [
            "investigation_started",
            "human_review_requested",
            "approval_approved",
            "workflow_resumed",
            "investigation_completed",
        ]
    finally:
        restarted.close()
