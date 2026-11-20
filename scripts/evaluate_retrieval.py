"""Evaluate OpsPilot retrieval strategies on a small labeled query set.

Runs entirely offline using the persisted ChromaDB collection and the cached
embedding model. No model weights are downloaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a script from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from typing import Any  # noqa: E402

from backend.app.embeddings import load_embedding_model  # noqa: E402
from backend.app.retrieval import (  # noqa: E402
    BM25SearchEngine,
    HybridSearchEngine,
    SemanticSearchEngine,
)
from backend.app.vectorstore import ChromaVectorStore  # noqa: E402

DEFAULT_DATASET = Path("tests/data/retrieval_eval.json")
DEFAULT_TOP_K = 5


def _doc_ids(results: list[Any]) -> list[str]:
    out: list[str] = []
    for r in results:
        did = getattr(r, "doc_id", None)
        if did is None and isinstance(r, dict):
            did = r.get("doc_id")
        if did is not None:
            out.append(str(did))
    return out


def _recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = retrieved[:k]
    return len(set(top) & relevant) / len(relevant)


def _mrr_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    for i, did in enumerate(retrieved[:k], start=1):
        if did in relevant:
            return 1.0 / i
    return 0.0


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    relevant_rows = [r for r in rows if r["relevant"]]
    if not relevant_rows:
        return {"recall@3": 0.0, "recall@5": 0.0, "mrr@5": 0.0, "n": 0}
    return {
        "recall@3": sum(r["recall@3"] for r in relevant_rows) / len(relevant_rows),
        "recall@5": sum(r["recall@5"] for r in relevant_rows) / len(relevant_rows),
        "mrr@5": sum(r["mrr@5"] for r in relevant_rows) / len(relevant_rows),
        "n": len(relevant_rows),
    }


def _run_method(
    name: str, search_fn, queries, top_k: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    per_query: list[dict[str, Any]] = []
    for q in queries:
        relevant = set(q.get("relevant_doc_ids") or [])
        results = search_fn(q["query"], top_k=top_k)
        retrieved = _doc_ids(results)
        row = {
            "id": q["id"],
            "relevant": bool(relevant),
            "recall@3": _recall_at_k(retrieved, relevant, 3) if relevant else 0.0,
            "recall@5": _recall_at_k(retrieved, relevant, 5) if relevant else 0.0,
            "mrr@5": _mrr_at_k(retrieved, relevant, 5) if relevant else 0.0,
            "retrieved": retrieved[:top_k],
            "no_evidence_returned": (not relevant) and len(retrieved) > 0,
        }
        rows.append(row)
        per_query.append({"method": name, "id": q["id"], "query": q["query"], **row})
    return rows, per_query


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--reranker-model", type=str, default=None)
    args = parser.parse_args()

    with args.dataset.open("r", encoding="utf-8") as f:
        data = json.load(f)
    queries = data["queries"]

    store = ChromaVectorStore()
    try:
        model = load_embedding_model(device="cpu", local_files_only=True)
        semantic = SemanticSearchEngine(store, model=model)
        bm25 = BM25SearchEngine(store)
        bm25.build_index()
        hybrid = HybridSearchEngine(semantic_engine=semantic, bm25_engine=bm25)

        # Optional reranker (only if explicitly provided and cached).
        reranker = None
        if args.reranker_model:
            from backend.app.retrieval.reranker import CrossEncoderReranker

            reranker = CrossEncoderReranker(model_id=args.reranker_model, local_files_only=True)
            hybrid_rerank = HybridSearchEngine(
                semantic_engine=semantic, bm25_engine=bm25, reranker=reranker
            )
        else:
            hybrid_rerank = None

        methods: list[tuple[str, Any]] = [
            ("semantic", lambda q, top_k: semantic.search(q, top_k=top_k)),
            ("bm25", lambda q, top_k: bm25.search(q, top_k=top_k)),
            ("hybrid", lambda q, top_k: hybrid.search(q, top_k=top_k)),
        ]
        if hybrid_rerank is not None:
            methods.append(("hybrid+rerank", lambda q, top_k: hybrid_rerank.search(q, top_k=top_k)))

        all_per_query: list[dict[str, Any]] = []
        print(f"Dataset: {args.dataset} ({len(queries)} queries)")
        print(f"Top-k: {args.top_k}")
        print()
        for name, fn in methods:
            rows, per_query = _run_method(
                name, lambda q, top_k, fn=fn: fn(q, top_k), queries, args.top_k
            )
            agg = _aggregate(rows)
            no_ev = [r for r in rows if not r["relevant"]]
            false_pos = sum(1 for r in no_ev if r["no_evidence_returned"])
            print(f"[{name}]")
            print(f"  recall@3 = {agg['recall@3']:.4f}")
            print(f"  recall@5 = {agg['recall@5']:.4f}")
            print(f"  mrr@5    = {agg['mrr@5']:.4f}")
            print(f"  queries  = {agg['n']} (with relevant docs)")
            if no_ev:
                print(f"  no-evidence queries returning results: {false_pos}/{len(no_ev)}")
            print()
            all_per_query.extend(per_query)

        # Per-query detail for hybrid vs semantic.
        print("Per-query (hybrid retrieved doc ids):")
        for q in queries:
            relevant = set(q.get("relevant_doc_ids") or [])
            res = hybrid.search(q["query"], top_k=args.top_k)
            print(f"  {q['id']}: {_doc_ids(res)[: args.top_k]}  relevant={sorted(relevant)}")

    finally:
        store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
