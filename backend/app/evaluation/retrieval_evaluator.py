"""Retrieval quality evaluation for semantic, BM25 and hybrid RRF.

Offline mode uses an in-memory fixture with the real retrieval classes and a
lexical hashing model. Local mode uses the actual sentence-transformer model and
a temporary persistent ChromaDB collection. There is no silent fallback.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections import Counter
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

# Ranking metrics are only defined for queries with labeled relevant documents.
# No-evidence queries are scored by the abstention gate instead, so their ranking
# metrics are recorded as ``None`` and can never be averaged into a recall figure
# by accident.
RANKING_METRIC_KEYS = (
    "recall@3",
    "recall@5",
    "precision@3",
    "precision@5",
    "mrr@5",
    "ndcg@3",
    "ndcg@5",
    "chunk_recall@5",
    "chunk_precision@5",
)

def _mean_defined(rows: list[CaseResult], key: str) -> float:
    """Mean over rows where the metric is actually defined.

    No-evidence rows carry ``None`` for ranking metrics so they can never be
    folded into a recall figure, and engine failures carry no value at all.
    """
    values = [
        row.metrics.get(key)
        for row in rows
        if isinstance(row.metrics.get(key), (int, float))
        and not isinstance(row.metrics.get(key), bool)
    ]
    return average(values)


def retrieval_gates(
    metrics: dict[str, Any], cases: list[CaseResult], *, mode: str = "offline"
) -> dict[str, Any]:
    """Split retrieval quality into three independent, explicit gates.

    * ``ranking`` - did labeled queries retrieve a relevant document?
    * ``integrity`` - are the measurements themselves sound (no duplicates,
      provenance present, filters honoured, empty corpus tolerated)?
    * ``abstention`` - did no-evidence queries avoid returning candidates?

    Keeping abstention out of the ranking gate is the whole point: an
    approximate vector index returns *something* for any query, so folding
    abstention into recall would permanently gate CI on a property the retrieval
    layer cannot satisfy without an explicit threshold.

    Blocking policy, stated here rather than implied by an exit code:

    * ``integrity`` is always blocking. A duplicate, a missing source path or a
      broken metadata filter means the numbers cannot be trusted at all.
    * ``ranking`` is blocking for every method whose score is meaningful in the
      active mode. In ``offline`` mode the semantic engine runs on a lexical
      hashing stand-in rather than the pinned sentence-transformer, so semantic
      ranking is advisory there; BM25 and hybrid remain blocking because they
      exercise the production code paths. In ``local`` mode all three block.
    * ``abstention`` is advisory. The evaluator measures candidate presence, not
      a calibrated threshold, and the CLI only makes it blocking on request.
    """
    labeled = [case for case in cases if case.metrics.get("ranking_scored")]
    no_evidence = [case for case in cases if case.metrics.get("abstention_checked")]
    integrity_passed = sum(1 for case in cases if case.metrics.get("integrity_ok"))
    abstention_passed = sum(
        1 for case in no_evidence if case.metrics.get("abstention_ok")
    )
    filter_checks = int(metrics.get("metadata_filter_checks") or 0)
    filter_passes = int(metrics.get("metadata_filter_passes") or 0)
    collection_ok = bool(metrics.get("empty_collection_ok")) and (
        filter_passes == filter_checks
    )
    integrity_ok = collection_ok and integrity_passed == len(cases)

    method_rows: dict[str, Any] = {}
    for method in ("semantic", "bm25", "hybrid"):
        rows = [case for case in labeled if case.metrics.get("method") == method]
        passed = sum(1 for case in rows if case.metrics.get("ranking_ok"))
        # Offline semantic ranking is a lexical approximation, not the product
        # embedding model, so a miss there is not evidence of a regression.
        blocking = not (mode == "offline" and method == "semantic")
        method_rows[method] = {
            "status": (
                "not_applicable"
                if not rows
                else ("pass" if passed == len(rows) else "fail")
            ),
            "passed": passed,
            "total": len(rows),
            "blocking": blocking,
            "failed_cases": [
                case.case_id for case in rows if not case.metrics.get("ranking_ok")
            ],
        }

    blocking_ranking_failures = [
        name for name, row in method_rows.items() if row["blocking"] and row["status"] == "fail"
    ]
    advisory_ranking_failures = [
        name
        for name, row in method_rows.items()
        if not row["blocking"] and row["status"] == "fail"
    ]
    ranking_passed = sum(1 for case in labeled if case.metrics.get("ranking_ok"))

    return {
        "ranking": {
            "status": (
                "not_applicable"
                if not labeled
                else ("fail" if blocking_ranking_failures else "pass")
            ),
            "passed": ranking_passed,
            "total": len(labeled),
            "blocking": True,
            "blocking_failure_methods": blocking_ranking_failures,
            "advisory_failure_methods": advisory_ranking_failures,
            "methods": method_rows,
        },
        "integrity": {
            "status": "pass" if integrity_ok else "fail",
            "passed": integrity_passed,
            "total": len(cases),
            "collection_checks_ok": collection_ok,
            "blocking": True,
        },
        "abstention": {
            "status": (
                "not_applicable"
                if not no_evidence
                else ("pass" if abstention_passed == len(no_evidence) else "fail")
            ),
            "passed": abstention_passed,
            "total": len(no_evidence),
            "blocking": False,
        },
    }

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
    ranking_failures: list[str] = []
    abstention_failures: list[str] = []
    integrity_failures: list[str] = []
    try:
        results = list(search(query.query, top_k=k, filters=filters) or [])
    except Exception as exc:
        # An exception makes every downstream measurement meaningless. Record it
        # as an integrity failure rather than a ranking miss so it cannot be
        # mistaken for ordinary retrieval quality.
        return CaseResult(
            case_id=f"{query.id}:{method}",
            suite="retrieval",
            passed=False,
            metrics={
                "method": method,
                "ranking_scored": False,
                "abstention_checked": False,
                "ranking_ok": False,
                "abstention_ok": False,
                "integrity_ok": False,
                "engine_error": type(exc).__name__,
            },
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
        integrity_failures.append("duplicate chunk ids in results")
    if len(doc_ids) != len({str(_value(result, "doc_id") or "") for result in results}):
        integrity_failures.append("duplicate document ids in results")
    if len(results) > k:
        integrity_failures.append(f"returned {len(results)} results for top_k={k}")
    if filters:
        expected_doc = str(filters.get("doc_id") or "")
        if expected_doc and any(
            str(_value(result, "doc_id") or "") != expected_doc for result in results
        ):
            integrity_failures.append("metadata filter returned a non-matching document")
    provenance_ok = all(
        _value(result, "chunk_id")
        and _value(result, "doc_id")
        and _value(result, "source_path")
        for result in results
    )
    citation_available = all(bool(_value(result, "chunk_text")) for result in results)
    if not provenance_ok:
        integrity_failures.append("missing source provenance in one or more results")
    if results and not citation_available:
        integrity_failures.append("missing chunk text for citation")
    if relevant_docs and recall_at_k(doc_ids, relevant_docs, k) == 0:
        ranking_failures.append(f"no relevant document in top {k}")
    if not relevant_docs and results:
        abstention_failures.append("no-evidence query returned results")

    doc_counts = Counter(chunk_docs.values())
    doc_counts.pop("", None)
    max_chunks_per_document = max(doc_counts.values(), default=0)
    has_relevant = bool(relevant_docs)
    scores = [float(value) for value in (_value(result, "score") for result in results)
              if isinstance(value, (int, float)) and not isinstance(value, bool)]

    if has_relevant:
        measured = {
            "recall@3": recall_at_k(doc_ids, relevant_docs, 3),
            "recall@5": recall_at_k(doc_ids, relevant_docs, 5),
            "precision@3": precision_at_k(doc_ids, relevant_docs, 3),
            "precision@5": precision_at_k(doc_ids, relevant_docs, 5),
            "mrr@5": reciprocal_rank_at_k(doc_ids, relevant_docs, 5),
            "ndcg@3": ndcg_at_k(doc_ids, relevant_docs, 3),
            "ndcg@5": ndcg_at_k(doc_ids, relevant_docs, 5),
            "chunk_recall@5": recall_at_k(chunk_ids, relevant_chunk_ids, 5),
            "chunk_precision@5": precision_at_k(chunk_ids, relevant_chunk_ids, 5),
        }
    else:
        # Deliberately undefined: no labeled relevant document exists, so any
        # recall/nDCG value here would be a fabricated zero.
        measured = {key: None for key in RANKING_METRIC_KEYS}

    metrics = {
        "method": method,
        "has_relevant": has_relevant,
        "ranking_scored": has_relevant,
        "abstention_checked": not has_relevant,
        "ranking_ok": has_relevant and not ranking_failures and not integrity_failures,
        "abstention_ok": (
            (not has_relevant) and not abstention_failures and not integrity_failures
        ),
        "integrity_ok": not integrity_failures,
        "ranking_failures": ranking_failures,
        "abstention_failures": abstention_failures,
        "integrity_failures": integrity_failures,
        "distinct_documents@5": len(doc_ids),
        "max_chunks_per_document@5": max_chunks_per_document,
        "chunk_concentration@5": (
            max_chunks_per_document / len(results) if results else 0.0
        ),
        "retrieved_doc_ids": doc_ids,
        "retrieved_chunk_ids": chunk_ids,
        "result_count": len(results),
        "top_score": max(scores) if scores else None,
        "min_score": min(scores) if scores else None,
        **measured,
        "no_evidence_returned": (not has_relevant) and bool(results),
        "provenance_ok": provenance_ok,
        "citation_available": citation_available,
        "latency_ms": duration,
    }
    return CaseResult(
        case_id=f"{query.id}:{method}",
        suite="retrieval",
        passed=not (ranking_failures or abstention_failures or integrity_failures),
        metrics=metrics,
        failures=ranking_failures + abstention_failures + integrity_failures,
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
                "pass_rate": (
                    sum(1 for row in rows if row.passed) / len(rows) if rows else 0.0
                ),
                "ranking_pass_rate": (
                    sum(1 for row in labeled if row.metrics.get("ranking_ok"))
                    / len(labeled)
                    if labeled
                    else 1.0
                ),
                "integrity_pass_rate": (
                    sum(1 for row in rows if row.metrics.get("integrity_ok")) / len(rows)
                    if rows
                    else 1.0
                ),
                "abstention_pass_rate": (
                    sum(1 for row in no_evidence if row.metrics.get("abstention_ok"))
                    / len(no_evidence)
                    if no_evidence
                    else 1.0
                ),
                "recall@3": _mean_defined(labeled, "recall@3"),
                "recall@5": _mean_defined(labeled, "recall@5"),
                "precision@3": _mean_defined(labeled, "precision@3"),
                "precision@5": _mean_defined(labeled, "precision@5"),
                "mrr@5": _mean_defined(labeled, "mrr@5"),
                "ndcg@3": _mean_defined(labeled, "ndcg@3"),
                "ndcg@5": _mean_defined(labeled, "ndcg@5"),
                "chunk_recall@5": _mean_defined(labeled, "chunk_recall@5"),
                "chunk_precision@5": _mean_defined(labeled, "chunk_precision@5"),
                # Document coverage is recall@5 at the document level; reported
                # under both names so the denominator is explicit.
                "relevant_doc_coverage@5": _mean_defined(labeled, "recall@5"),
                "distinct_documents@5": average(
                    row.metrics["distinct_documents@5"] for row in rows
                ),
                "max_chunks_per_document@5": average(
                    row.metrics["max_chunks_per_document@5"] for row in rows
                ),
                "chunk_concentration@5": average(
                    row.metrics["chunk_concentration@5"] for row in rows
                ),
                "abstention_queries": len(no_evidence),
                "abstention_returned_results": sum(
                    1 for row in no_evidence if row.metrics.get("no_evidence_returned")
                ),
                "abstention_clean_rate": (
                    1.0
                    - sum(
                        1
                        for row in no_evidence
                        if row.metrics.get("no_evidence_returned")
                    )
                    / len(no_evidence)
                    if no_evidence
                    else 1.0
                ),
                "avg_latency_ms": average(row.metrics["latency_ms"] for row in rows),
                "no_evidence_query_returned_results": sum(
                    1 for row in no_evidence if row.metrics["no_evidence_returned"]
                ),
                "provenance_ok_rate": (
                    sum(1 for row in rows if row.metrics.get("provenance_ok")) / len(rows)
                    if rows
                    else 0.0
                ),
                "citation_available_rate": (
                    sum(1 for row in rows if row.metrics.get("citation_available"))
                    / len(rows)
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
            "labeled_queries": sum(1 for case in cases if case.metrics.get("ranking_scored")),
            "no_evidence_queries": sum(
                1 for case in cases if case.metrics.get("abstention_checked")
            ),
        }
        metrics["gates"] = retrieval_gates(metrics, cases, mode=mode)
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
                "positives; that count is reported as its own advisory gate and is "
                "never mixed into recall or nDCG.",
                "The abstention gate is advisory because this evaluator does not "
                "implement a no-evidence threshold; see the abstention suite for "
                "threshold trade-offs over the expanded negative-query dataset.",
            ],
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    finally:
        if owns_runtime:
            runtime.close()
