"""Retrieval quality evaluation for semantic, BM25 and hybrid RRF.

Offline mode uses an in-memory fixture with the real retrieval classes and a
lexical hashing model. Local mode uses the actual sentence-transformer model and
a temporary persistent ChromaDB collection. There is no silent fallback.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.app.retrieval import BM25SearchEngine, HybridSearchEngine, SemanticSearchEngine
from backend.app.vectorstore import ChromaVectorStore

from .dataset import KNOWLEDGE_BASE_DIR, load_retrieval
from .metrics import (
    average,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
    unique_preserving_order,
)
from .models import CaseResult, RetrievalDataset, SuiteResult
from .offline import FixtureVectorStore, LexicalEmbeddingModel, build_offline_runtime

DEFAULT_K = 5


class RetrievalEvaluationError(RuntimeError):
    """Raised when a requested retrieval mode cannot be prepared safely."""


def _value(result: Any, name: str) -> Any:
    value = getattr(result, name, None)
    if value is None and isinstance(result, dict):
        value = result.get(name)
    return value


def _docs(results: list[Any]) -> list[str]:
    return unique_preserving_order(_value(result, "doc_id") for result in results)


def _chunks(results: list[Any]) -> list[str]:
    return unique_preserving_order(_value(result, "chunk_id") for result in results)


def _chunk_docs(results: list[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for result in results:
        chunk_id = str(_value(result, "chunk_id") or "")
        doc_id = str(_value(result, "doc_id") or "")
        if chunk_id:
            mapping[chunk_id] = doc_id
    return mapping


class LocalRetrievalRuntime:
    label = "local real embeddings + temporary persistent ChromaDB"

    def __init__(self) -> None:
        from backend.app.embeddings import (
            EXPECTED_DIMENSION,
            MODEL_ID,
            MODEL_REVISION,
            load_embedding_model,
        )
        from backend.app.indexing import index_knowledge_base

        self._tempdir = Path(tempfile.mkdtemp(prefix="opspilot-eval-chroma-"))
        self.store = ChromaVectorStore(
            persist_directory=self._tempdir, expected_dimension=EXPECTED_DIMENSION
        )
        try:
            self.model = load_embedding_model(
                device="cpu",
                local_files_only=True,
                model_id=MODEL_ID,
                revision=MODEL_REVISION,
            )
            index_knowledge_base(
                directory=KNOWLEDGE_BASE_DIR,
                store=self.store,
                model=self.model,
                local_files_only=True,
            )
        except Exception as exc:
            self.store.close()
            shutil.rmtree(self._tempdir, ignore_errors=True)
            raise RetrievalEvaluationError(
                "local retrieval requires the pinned embedding model in the local "
                "cache and an indexable knowledge base; no fixture fallback is "
                f"permitted ({type(exc).__name__})"
            ) from exc
        self.semantic = SemanticSearchEngine(
            self.store, model=self.model, expected_dimension=EXPECTED_DIMENSION
        )
        self.bm25 = BM25SearchEngine(self.store)
        self.bm25.build_index()
        self.hybrid = HybridSearchEngine(
            semantic_engine=self.semantic, bm25_engine=self.bm25
        )
        self.mode = "local"

    def close(self) -> None:
        self.store.close()
        shutil.rmtree(self._tempdir, ignore_errors=True)


def build_retrieval_runtime(mode: str) -> Any:
    if mode == "offline":
        return build_offline_runtime(KNOWLEDGE_BASE_DIR)
    if mode == "local":
        return LocalRetrievalRuntime()
    raise RetrievalEvaluationError(
        f"retrieval mode {mode!r} is not supported; use offline or local"
    )


def _run_query(
    method: str,
    search: Any,
    query: Any,
    *,
    k: int,
    filters: dict[str, Any] | None = None,
) -> CaseResult:
    started = time.perf_counter()
    failures: list[str] = []
    try:
        results = list(search(query.query, top_k=k, filters=filters) or [])
    except Exception as exc:
        return CaseResult(
            case_id=f"{query.id}:{method}",
            suite="retrieval",
            passed=False,
            failures=[f"retrieval raised {type(exc).__name__}"],
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    duration = (time.perf_counter() - started) * 1000

    doc_ids = _docs(results)
    chunk_ids = _chunks(results)
    relevant_docs = set(query.relevant_doc_ids)
    relevant_chunks = set(query.expected_chunk_ids)
    chunk_docs = _chunk_docs(results)
    relevant_chunk_ids = {
        chunk_id
        for chunk_id, doc_id in chunk_docs.items()
        if (relevant_chunks and chunk_id in relevant_chunks)
        or (not relevant_chunks and doc_id in relevant_docs)
    }

    if len(chunk_ids) != len(results):
        failures.append("duplicate chunk ids in results")
    if len(doc_ids) != len({str(_value(result, "doc_id") or "") for result in results}):
        failures.append("duplicate document ids in results")
    if len(results) > k:
        failures.append(f"returned {len(results)} results for top_k={k}")
    if filters:
        expected_doc = str(filters.get("doc_id") or "")
        if expected_doc and any(
            str(_value(result, "doc_id") or "") != expected_doc for result in results
        ):
            failures.append("metadata filter returned a non-matching document")
    provenance_ok = all(
        _value(result, "chunk_id")
        and _value(result, "doc_id")
        and _value(result, "source_path")
        for result in results
    )
    citation_available = all(bool(_value(result, "chunk_text")) for result in results)
    if not provenance_ok:
        failures.append("missing source provenance in one or more results")
    if results and not citation_available:
        failures.append("missing chunk text for citation")
    if relevant_docs and recall_at_k(doc_ids, relevant_docs, k) == 0:
        failures.append(f"no relevant document in top {k}")
    if not relevant_docs and results:
        failures.append("no-evidence query returned results")

    metrics = {
        "method": method,
        "has_relevant": bool(relevant_docs),
        "retrieved_doc_ids": doc_ids,
        "retrieved_chunk_ids": chunk_ids,
        "result_count": len(results),
        "recall@3": recall_at_k(doc_ids, relevant_docs, 3),
        "recall@5": recall_at_k(doc_ids, relevant_docs, 5),
        "precision@3": precision_at_k(doc_ids, relevant_docs, 3),
        "precision@5": precision_at_k(doc_ids, relevant_docs, 5),
        "mrr@5": reciprocal_rank_at_k(doc_ids, relevant_docs, 5),
        "ndcg@3": ndcg_at_k(doc_ids, relevant_docs, 3),
        "ndcg@5": ndcg_at_k(doc_ids, relevant_docs, 5),
        "chunk_recall@5": recall_at_k(chunk_ids, relevant_chunk_ids, 5),
        "chunk_precision@5": precision_at_k(chunk_ids, relevant_chunk_ids, 5),
        "no_evidence_returned": (not relevant_docs) and bool(results),
        "provenance_ok": provenance_ok,
        "citation_available": citation_available,
        "latency_ms": duration,
    }
    return CaseResult(
        case_id=f"{query.id}:{method}",
        suite="retrieval",
        passed=not failures,
        metrics=metrics,
        failures=failures,
        duration_ms=duration,
    )


def _empty_collection_ok() -> bool:
    store = FixtureVectorStore()
    model = LexicalEmbeddingModel()
    semantic = SemanticSearchEngine(store, model=model, expected_dimension=384)
    bm25 = BM25SearchEngine(store)
    bm25.build_index()
    hybrid = HybridSearchEngine(semantic_engine=semantic, bm25_engine=bm25)
    return all(
        engine.search("empty collection", top_k=3) == []
        for engine in (semantic, bm25, hybrid)
    )


def evaluate_retrieval(
    *,
    mode: str = "offline",
    dataset: RetrievalDataset | None = None,
    runtime: Any = None,
) -> SuiteResult:
    started = time.perf_counter()
    dataset = dataset or load_retrieval()
    owns_runtime = runtime is None
    runtime = runtime or build_retrieval_runtime(mode)
    try:
        methods = {
            "semantic": runtime.semantic.search,
            "bm25": runtime.bm25.search,
            "hybrid": runtime.hybrid.search,
        }
        cases: list[CaseResult] = []
        aggregates: dict[str, Any] = {}
        for name, search in methods.items():
            rows = [
                _run_query(name, search, query, k=DEFAULT_K)
                for query in dataset.queries
            ]
            cases.extend(rows)
            labeled = [row for row in rows if row.metrics.get("has_relevant")]
            no_evidence = [
                row for row in rows if not row.metrics.get("has_relevant")
            ]
            aggregates[name] = {
                "queries": len(rows),
                "labeled_queries": len(labeled),
                "no_evidence_queries": len(no_evidence),
                "retrieval_pass_rate": (
                    sum(1 for row in rows if row.passed) / len(rows) if rows else 0.0
                ),
                "recall@3": average(row.metrics["recall@3"] for row in labeled),
                "recall@5": average(row.metrics["recall@5"] for row in labeled),
                "precision@3": average(row.metrics["precision@3"] for row in labeled),
                "precision@5": average(row.metrics["precision@5"] for row in labeled),
                "mrr@5": average(row.metrics["mrr@5"] for row in labeled),
                "ndcg@3": average(row.metrics["ndcg@3"] for row in labeled),
                "ndcg@5": average(row.metrics["ndcg@5"] for row in labeled),
                "chunk_recall@5": average(
                    row.metrics["chunk_recall@5"] for row in labeled
                ),
                "chunk_precision@5": average(
                    row.metrics["chunk_precision@5"] for row in labeled
                ),
                "avg_latency_ms": average(row.metrics["latency_ms"] for row in rows),
                "no_evidence_query_returned_results": sum(
                    1 for row in no_evidence if row.metrics["no_evidence_returned"]
                ),
                "provenance_ok_rate": (
                    sum(1 for row in rows if row.metrics["provenance_ok"]) / len(rows)
                    if rows
                    else 0.0
                ),
                "citation_available_rate": (
                    sum(1 for row in rows if row.metrics["citation_available"]) / len(rows)
                    if rows
                    else 0.0
                ),
            }

        filter_checks = 0
        filter_passes = 0
        for name, search in methods.items():
            for query in [q for q in dataset.queries if q.relevant_doc_ids][:3]:
                result = _run_query(
                    name,
                    search,
                    query,
                    k=DEFAULT_K,
                    filters={"doc_id": query.relevant_doc_ids[0]},
                )
                filter_checks += 1
                if result.passed and not any(
                    "filter" in failure for failure in result.failures
                ):
                    filter_passes += 1

        metrics = {
            "mode_label": getattr(runtime, "label", mode),
            "methods": aggregates,
            "empty_collection_ok": _empty_collection_ok(),
            "metadata_filter_checks": filter_checks,
            "metadata_filter_passes": filter_passes,
        }
        return SuiteResult(
            suite="retrieval",
            mode=mode,
            dataset_version=str(dataset.version),
            cases=cases,
            metrics=metrics,
            limitations=[
                "Offline mode uses lexical hashing embeddings, not the pinned "
                "sentence-transformer; semantic quality is only meaningful in local mode.",
                "Similarity and RRF scores are ranking values, not calibrated probabilities.",
                "No-evidence queries are expected to expose approximate-search false "
                "positives; that count is reported, not hidden.",
            ],
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    finally:
        if owns_runtime:
            runtime.close()
