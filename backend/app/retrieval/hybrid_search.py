"""Hybrid retrieval: semantic search + BM25, fused with Reciprocal Rank Fusion.

Reranking is optional and is only applied when a real reranker is supplied.

The final window favours document coverage: a single knowledge document can
often supply many high-scoring chunks, and without a cap those chunks fill the
whole window so the report never sees other documents that the incident also
concerns. See ``DEFAULT_MAX_CHUNKS_PER_DOCUMENT``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .bm25_search import BM25SearchEngine
from .models import HybridSearchResult
from .semantic_search import SemanticSearchEngine, collect_filters

DEFAULT_SEMANTIC_TOP_K = 20
DEFAULT_BM25_TOP_K = 20
DEFAULT_RRF_K = 60
# At most this many chunks per document appear in the final window, so one
# document cannot occupy every slot. The cap is only enforced when enough
# distinct documents exist to fill the window; otherwise the plain top-k is
# returned, so small single-document collections are unaffected.
DEFAULT_MAX_CHUNKS_PER_DOCUMENT = 2


class Reranker(Protocol):
    """Minimal reranker interface."""

    def rerank(
        self, query: Any, candidates: Sequence[HybridSearchResult]
    ) -> Sequence[HybridSearchResult]: ...


class HybridSearchError(RuntimeError):
    """Raised when hybrid retrieval cannot be performed."""


def _result_to_hybrid(
    result: Any,
    *,
    semantic_rank: int | None = None,
    bm25_rank: int | None = None,
    semantic_similarity: float | None = None,
    cosine_distance: float | None = None,
    bm25_score: float | None = None,
    rrf_score: float = 0.0,
) -> HybridSearchResult:
    meta = getattr(result, "metadata", None)
    if meta is None and isinstance(result, dict):
        meta = result.get("metadata", {})
    meta = dict(meta or {})

    chunk_id = getattr(result, "chunk_id", None)
    if chunk_id is None and isinstance(result, dict):
        chunk_id = result.get("chunk_id")
    chunk_id = str(chunk_id or "")

    def _g(attr: str, key: str, default: Any = "") -> Any:
        val = getattr(result, attr, None)
        if val is None and isinstance(result, dict):
            val = result.get(key, default)
        if val is None:
            val = default
        return val

    scs = _g("source_char_start", "source_char_start", None)
    sce = _g("source_char_end", "source_char_end", None)
    try:
        scs = int(scs) if scs not in (None, "") else None
    except (TypeError, ValueError):
        scs = None
    try:
        sce = int(sce) if sce not in (None, "") else None
    except (TypeError, ValueError):
        sce = None

    token_count_raw = _g("token_count", "token_count", 0)
    try:
        token_count = int(token_count_raw) if token_count_raw not in (None, "") else 0
    except (TypeError, ValueError):
        token_count = 0

    # Preserve semantic similarity if available on the result.
    if semantic_similarity is None:
        ss = getattr(result, "semantic_similarity", None)
        if ss is None:
            ss = getattr(result, "similarity_score", None)
        if ss is None and isinstance(result, dict):
            ss = result.get("semantic_similarity", result.get("similarity_score"))
        semantic_similarity = ss

    if cosine_distance is None:
        cd = getattr(result, "cosine_distance", None)
        if cd is None and isinstance(result, dict):
            cd = result.get("cosine_distance")
        cosine_distance = cd

    if bm25_score is None:
        bs = getattr(result, "bm25_score", None)
        if bs is None and isinstance(result, dict):
            bs = result.get("bm25_score")
        bm25_score = bs

    if semantic_rank is None:
        sr = getattr(result, "semantic_rank", None)
        if sr is None and isinstance(result, dict):
            sr = result.get("semantic_rank")
        semantic_rank = sr

    if bm25_rank is None:
        br = getattr(result, "bm25_rank", None)
        if br is None and isinstance(result, dict):
            br = result.get("bm25_rank")
        bm25_rank = br

    return HybridSearchResult(
        chunk_id=chunk_id,
        parent_chunk_id=str(_g("parent_chunk_id", "parent_chunk_id", "")),
        doc_id=str(_g("doc_id", "doc_id", "")),
        title=str(_g("title", "title", "")),
        category=str(_g("category", "category", "")),
        component=str(_g("component", "component", "")),
        severity=str(_g("severity", "severity", "")),
        source_path=str(_g("source_path", "source_path", "")),
        chunk_text=str(
            _g("chunk_text", "chunk_text", _g("text", "text", _g("document", "document", "")))
        ),
        cosine_distance=cosine_distance,
        semantic_similarity=semantic_similarity,
        semantic_rank=semantic_rank,
        bm25_score=bm25_score,
        bm25_rank=bm25_rank,
        rrf_score=float(rrf_score),
        reranker_score=None,
        final_rank=0,
        tokenizer_name=str(_g("tokenizer_name", "tokenizer_name", "")),
        tokenizer_revision=str(_g("tokenizer_revision", "tokenizer_revision", "")),
        token_count=token_count,
        source_char_start=scs,
        source_char_end=sce,
        metadata=meta,
    )


class HybridSearchEngine:
    """Combine semantic and BM25 retrieval with RRF."""

    def __init__(
        self,
        *args: Any,
        semantic_engine: Any = None,
        semantic_search: Any = None,
        bm25_engine: Any = None,
        bm25_search: Any = None,
        reranker: Any = None,
        store: Any = None,
        vector_store: Any = None,
        chroma_store: Any = None,
        model: Any = None,
        embedding_model: Any = None,
        persist_directory: Any = None,
        collection_name: str | None = None,
        rrf_k: int = DEFAULT_RRF_K,
        semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K,
        bm25_top_k: int = DEFAULT_BM25_TOP_K,
        max_chunks_per_document: int | None = DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
        local_files_only: bool = True,
        **kwargs: Any,
    ) -> None:
        # Resolve positional args.
        positional_store = None
        positional_model = None
        for arg in args:
            if hasattr(arg, "collection") and hasattr(arg, "count"):
                positional_store = arg
            elif hasattr(arg, "encode"):
                positional_model = arg
            elif isinstance(arg, str):
                if persist_directory is None:
                    persist_directory = arg

        self.store = store or vector_store or chroma_store or positional_store
        self.model = model or embedding_model or positional_model
        self.local_files_only = bool(local_files_only)
        self.rrf_k = int(rrf_k)
        self.semantic_top_k = int(semantic_top_k)
        self.bm25_top_k = int(bm25_top_k)
        self.max_chunks_per_document = self._normalize_cap(max_chunks_per_document)
        self.reranker = reranker

        # Semantic engine.
        if semantic_engine is not None:
            self.semantic_engine = semantic_engine
        elif semantic_search is not None:
            self.semantic_engine = semantic_search
        else:
            try:
                self.semantic_engine = SemanticSearchEngine(
                    self.store,
                    model=self.model,
                    persist_directory=persist_directory,
                    collection_name=collection_name,
                    local_files_only=self.local_files_only,
                    **{
                        k: v
                        for k, v in kwargs.items()
                        if k
                        in {
                            "expected_dimension",
                            "expected_dim",
                            "dimension",
                            "normalization_tolerance",
                        }
                    },
                )
            except Exception:  # pragma: no cover - defensive
                self.semantic_engine = None

        # BM25 engine.
        if bm25_engine is not None:
            self.bm25_engine = bm25_engine
        elif bm25_search is not None:
            self.bm25_engine = bm25_search
        else:
            try:
                self.bm25_engine = BM25SearchEngine(
                    store=self.store,
                    persist_directory=persist_directory,
                    collection_name=collection_name,
                )
            except Exception:  # pragma: no cover - defensive
                self.bm25_engine = None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _collect(self, **kwargs: Any) -> Any:
        return collect_filters(**kwargs)

    @staticmethod
    def _normalize_cap(value: int | None) -> int | None:
        """Return a positive per-document cap, or ``None`` for no cap."""
        if value is None:
            return None
        try:
            cap = int(value)
        except (TypeError, ValueError):
            return None
        return cap if cap > 0 else None

    @staticmethod
    def _take_diverse(
        ordered: Sequence[HybridSearchResult],
        limit: int,
        max_per_document: int | None,
    ) -> list[HybridSearchResult]:
        """Take up to ``limit`` results in rank order, capped per document.

        The capped pass is only used when it fills the whole window: a store
        with a single document (or very few) must still return ``limit``
        chunks, so the plain top-k is the fallback.
        """
        if not max_per_document:
            return list(ordered[:limit])
        selected: list[HybridSearchResult] = []
        per_document: dict[str, int] = {}
        for result in ordered:
            document = result.doc_id or result.chunk_id
            if per_document.get(document, 0) >= max_per_document:
                continue
            selected.append(result)
            per_document[document] = per_document.get(document, 0) + 1
            if len(selected) >= limit:
                break
        if len(selected) < limit:
            return list(ordered[:limit])
        return selected

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: Any = None,
        *,
        top_k: int | None = None,
        k: int | None = None,
        n_results: int | None = None,
        limit: int | None = None,
        semantic_top_k: int | None = None,
        bm25_top_k: int | None = None,
        rrf_k: int | None = None,
        max_chunks_per_document: int | None = None,
        rerank: bool = True,
        rerank_top_k: int | None = None,
        filters: Any = None,
        where: Any = None,
        doc_id: Any = None,
        category: Any = None,
        component: Any = None,
        severity: Any = None,
        parent_chunk_id: Any = None,
        title: Any = None,
        source_path: Any = None,
        threshold: float | None = None,
        similarity_threshold: float | None = None,
        min_similarity: float | None = None,
        min_score: float | None = None,
        max_distance: float | None = None,
        query_vector: Any = None,
        vector: Any = None,
        embedding: Any = None,
        **extra: Any,
    ) -> list[HybridSearchResult]:
        # Resolve top-k.
        tk = top_k
        if tk is None:
            tk = k
        if tk is None:
            tk = n_results
        if tk is None:
            tk = limit
        if tk is None:
            tk = 5
        try:
            tk = int(tk)
        except (TypeError, ValueError):
            tk = 5
        if tk < 1:
            tk = 1

        stk = semantic_top_k if semantic_top_k is not None else self.semantic_top_k
        btk = bm25_top_k if bm25_top_k is not None else self.bm25_top_k
        rk = rrf_k if rrf_k is not None else self.rrf_k

        # Query text for BM25.
        query_text = query
        if hasattr(query, "text") and not isinstance(query, str):
            query_text = getattr(query, "text")
        qv = query_vector or vector or embedding
        if qv is None and isinstance(query, (list, tuple)) and not isinstance(query, str):
            qv = query

        collect_filters(
            filters=filters,
            where=where,
            doc_id=doc_id,
            category=category,
            component=component,
            severity=severity,
            parent_chunk_id=parent_chunk_id,
            title=title,
            source_path=source_path,
            **extra,
        )

        # Semantic retrieval.
        semantic_results: list[Any] = []
        if self.semantic_engine is not None:
            try:
                semantic_results = self.semantic_engine.search(
                    query
                    if qv is None and isinstance(query, str)
                    else (qv if qv is not None else query),
                    top_k=stk,
                    filters=filters,
                    where=where,
                    doc_id=doc_id,
                    category=category,
                    component=component,
                    severity=severity,
                    parent_chunk_id=parent_chunk_id,
                    title=title,
                    source_path=source_path,
                    threshold=threshold,
                    similarity_threshold=similarity_threshold,
                    min_similarity=min_similarity,
                    min_score=min_score,
                    max_distance=max_distance,
                    query_vector=qv,
                    **extra,
                )
            except Exception:
                semantic_results = []

        # BM25 retrieval (requires text).
        bm25_results: list[Any] = []
        if (
            self.bm25_engine is not None
            and query_text is not None
            and isinstance(query_text, str)
            and query_text.strip()
        ):
            try:
                bm25_results = self.bm25_engine.search(
                    query_text,
                    top_k=btk,
                    filters=filters,
                    where=where,
                    doc_id=doc_id,
                    category=category,
                    component=component,
                    severity=severity,
                    parent_chunk_id=parent_chunk_id,
                    title=title,
                    source_path=source_path,
                    **extra,
                )
            except Exception:
                bm25_results = []

        # RRF fusion.
        fused: dict[str, HybridSearchResult] = {}
        rrf_scores: dict[str, float] = {}

        for rank, res in enumerate(semantic_results, start=1):
            cid = getattr(res, "chunk_id", None)
            if cid is None and isinstance(res, dict):
                cid = res.get("chunk_id")
            cid = str(cid or "")
            if not cid:
                continue
            contribution = 1.0 / (rk + rank)
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + contribution
            if cid not in fused:
                fused[cid] = _result_to_hybrid(
                    res,
                    semantic_rank=rank,
                    semantic_similarity=getattr(
                        res, "similarity_score", getattr(res, "semantic_similarity", None)
                    ),
                    cosine_distance=getattr(res, "cosine_distance", None),
                    rrf_score=rrf_scores[cid],
                )
            else:
                fused[cid].semantic_rank = fused[cid].semantic_rank or rank
                fused[cid].semantic_similarity = fused[cid].semantic_similarity or getattr(
                    res, "similarity_score", None
                )
                fused[cid].cosine_distance = (
                    fused[cid].cosine_distance
                    if fused[cid].cosine_distance is not None
                    else getattr(res, "cosine_distance", None)
                )
                fused[cid].rrf_score = rrf_scores[cid]

        for rank, res in enumerate(bm25_results, start=1):
            cid = getattr(res, "chunk_id", None)
            if cid is None and isinstance(res, dict):
                cid = res.get("chunk_id")
            cid = str(cid or "")
            if not cid:
                continue
            contribution = 1.0 / (rk + rank)
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + contribution
            if cid not in fused:
                fused[cid] = _result_to_hybrid(
                    res,
                    bm25_rank=rank,
                    bm25_score=getattr(res, "bm25_score", None),
                    rrf_score=rrf_scores[cid],
                )
            else:
                fused[cid].bm25_rank = fused[cid].bm25_rank or rank
                fused[cid].bm25_score = (
                    fused[cid].bm25_score
                    if fused[cid].bm25_score is not None
                    else getattr(res, "bm25_score", None)
                )
                fused[cid].rrf_score = rrf_scores[cid]

        # Ensure rrf_score updated for all.
        for cid, r in fused.items():
            r.rrf_score = rrf_scores.get(cid, r.rrf_score)

        # Order by rrf desc, then id.
        ordered = sorted(fused.values(), key=lambda r: (-r.rrf_score, r.chunk_id))

        # Optional reranking.
        if rerank and self.reranker is not None and ordered:
            candidate_count = rerank_top_k if rerank_top_k is not None else len(ordered)
            candidate_count = max(1, min(candidate_count, len(ordered)))
            to_rerank = ordered[:candidate_count]
            rest = ordered[candidate_count:]
            reranked = self._apply_reranker(
                query_text
                if isinstance(query_text, str)
                else (query if isinstance(query, str) else ""),
                to_rerank,
            )
            ordered = reranked + rest

        # Assign final ranks and trim, keeping the window document-diverse.
        cap = (
            self.max_chunks_per_document
            if max_chunks_per_document is None
            else self._normalize_cap(max_chunks_per_document)
        )
        window = self._take_diverse(ordered, tk, cap)
        for i, r in enumerate(window, start=1):
            r.final_rank = i
        return window

    def _apply_reranker(
        self, query: str, candidates: list[HybridSearchResult]
    ) -> list[HybridSearchResult]:
        reranker = self.reranker
        if reranker is None:
            return candidates

        # Try common interfaces.
        try:
            if hasattr(reranker, "rerank"):
                out = reranker.rerank(query, candidates)
                if isinstance(out, (list, tuple)):
                    # Normalize pair scores or reordered result objects.
                    if out and isinstance(out[0], (tuple, list)):
                        score_map = {str(item[0]): item[1] for item in out}
                        for c in candidates:
                            if c.chunk_id in score_map:
                                c.reranker_score = float(score_map[c.chunk_id])
                    else:
                        # Assume reordered list of result objects.
                        new_candidates = []
                        for item in out:
                            if isinstance(item, HybridSearchResult):
                                new_candidates.append(item)
                            else:
                                new_candidates.append(item)
                        if new_candidates:
                            candidates = new_candidates
            elif hasattr(reranker, "predict"):
                pairs = [(query, c.chunk_text) for c in candidates]
                scores = reranker.predict(pairs)
                for c, s in zip(candidates, scores, strict=True):
                    c.reranker_score = float(s)
            elif hasattr(reranker, "score"):
                for c in candidates:
                    c.reranker_score = float(reranker.score(query, c.chunk_text))
            else:
                # Callable reranker: reranker(query, text) -> score
                for c in candidates:
                    try:
                        c.reranker_score = float(reranker(query, c.chunk_text))
                    except Exception:
                        c.reranker_score = None
        except Exception:
            # If reranking fails, keep fused order.
            return candidates

        # If reranker_score assigned, sort by it desc.
        if any(c.reranker_score is not None for c in candidates):
            candidates = sorted(
                candidates,
                key=lambda c: (
                    -(c.reranker_score if c.reranker_score is not None else float("-inf")),
                    c.chunk_id,
                ),
            )
        return candidates

    # Aliases.
    def search_query(self, *a: Any, **kw: Any) -> list[HybridSearchResult]:
        return self.search(*a, **kw)

    def query(self, *a: Any, **kw: Any) -> list[HybridSearchResult]:
        return self.search(*a, **kw)

    def __call__(self, *a: Any, **kw: Any) -> list[HybridSearchResult]:
        return self.search(*a, **kw)


__all__ = [
    "HybridSearchEngine",
    "HybridSearchError",
    "HybridSearchResult",
    "Reranker",
]
