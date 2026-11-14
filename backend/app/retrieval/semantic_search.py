"""Semantic search over the persistent OpsPilot ChromaDB collection.

This module performs retrieval only. It reuses the existing embedding model and
ChromaDB collection; it does not generate document embeddings, run RAG, or
contact any LLM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from backend.app.embeddings import (
    EXPECTED_DIMENSION,
    MODEL_ID,
    MODEL_REVISION,
    EmbeddingEncoder,
    load_embedding_model,
)
from backend.app.vectorstore import ChromaVectorStore

from .models import SearchResult

# Metadata fields that may be used for filtering. Arbitrary Chroma filter
# expressions are not accepted from callers.
ALLOWED_FILTER_FIELDS = frozenset(
    {
        "chunk_id",
        "token_chunk_id",
        "parent_chunk_id",
        "parent_chunk_index",
        "doc_id",
        "title",
        "category",
        "component",
        "severity",
        "source_type",
        "source_path",
        "tokenizer_name",
        "tokenizer_revision",
        "token_count",
        "max_tokens",
        "token_overlap",
        "source_char_start",
        "source_char_end",
        "applies_to",
        "last_updated",
    }
)

DEFAULT_TOP_K = 5
DEFAULT_NORMALIZATION_TOLERANCE = 1e-5


class QueryValidationError(ValueError):
    """Raised when a query string or query vector fails validation."""


class SemanticSearchError(RuntimeError):
    """Raised when semantic search cannot be performed."""


def _is_store(obj: Any) -> bool:
    return (
        isinstance(obj, ChromaVectorStore)
        or hasattr(obj, "collection")
        and hasattr(obj, "count")
        and callable(getattr(obj, "count", None))
    )


def _is_model(obj: Any) -> bool:
    return hasattr(obj, "encode") and (
        hasattr(obj, "get_sentence_embedding_dimension")
        or hasattr(obj, "get_embedding_dimension")
        or hasattr(obj, "max_seq_length")
    )


def _coerce_path(value: Any) -> Path | None:
    if value is None:
        return None
    return Path(value)


def _as_vector(value: Any) -> np.ndarray:
    if isinstance(value, np.ndarray):
        array = value
    elif hasattr(value, "detach") and hasattr(value, "cpu"):
        array = value.detach().cpu().numpy()
    elif isinstance(value, (list, tuple)):
        array = np.asarray(value)
    else:
        array = np.asarray(value)
    if array.ndim != 1:
        array = np.ravel(array)
    return np.asarray(array, dtype=np.float64)


@dataclass
class _FilterSpec:
    filters: dict[str, Any] | None = None
    where: Any = None
    doc_id: Any = None
    category: Any = None
    component: Any = None
    severity: Any = None
    parent_chunk_id: Any = None
    title: Any = None
    source_path: Any = None


class SemanticSearchEngine:
    """Semantic nearest-neighbor search over a ChromaDB collection."""

    def __init__(
        self,
        *args: Any,
        store: Any = None,
        vector_store: Any = None,
        chroma_store: Any = None,
        chroma: Any = None,
        model: Any = None,
        embedding_model: Any = None,
        encoder: Any = None,
        persist_directory: Any = None,
        collection_name: str | None = None,
        expected_dimension: int | None = None,
        expected_dim: int | None = None,
        dimension: int | None = None,
        embedding_dimension: int | None = None,
        dim: int | None = None,
        normalization_tolerance: float = DEFAULT_NORMALIZATION_TOLERANCE,
        local_files_only: bool = True,
        **kwargs: Any,
    ) -> None:
        # Resolve positional arguments.
        positional_store = None
        positional_model = None
        for arg in args:
            if _is_store(arg):
                positional_store = arg
            elif _is_model(arg):
                positional_model = arg
            elif isinstance(arg, (str, Path)):
                # Could be persist directory.
                if persist_directory is None:
                    persist_directory = arg
            else:
                # Unknown; ignore.
                pass

        self.store = (
            store
            or vector_store
            or chroma_store
            or chroma
            or positional_store
        )
        self.model = model or embedding_model or positional_model
        self.encoder = encoder
        self.local_files_only = bool(local_files_only)

        # Extra kwargs may carry store/model/filter config.
        if self.store is None and "store" in kwargs:
            self.store = kwargs.pop("store")
        if self.model is None and "model" in kwargs:
            self.model = kwargs.pop("model")
        if persist_directory is None and "persist_directory" in kwargs:
            persist_directory = kwargs.pop("persist_directory")

        if self.store is None:
            pd = _coerce_path(persist_directory)
            if pd is not None or persist_directory is None:
                try:
                    self.store = ChromaVectorStore(
                        persist_directory=pd,
                        collection_name=collection_name or "opspilot_embeddings",
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    raise SemanticSearchError(f"failed to open store: {exc}") from exc
        else:
            if collection_name is not None and hasattr(self.store, "collection_name"):
                # Do not rename an existing store's collection; respect it.
                pass

        # Resolve expected dimension.
        ed = (
            expected_dimension
            if expected_dimension is not None
            else expected_dim
            if expected_dim is not None
            else dimension
            if dimension is not None
            else embedding_dimension
            if embedding_dimension is not None
            else dim
        )
        if ed is None and self.store is not None:
            ed = getattr(self.store, "expected_dimension", None)
        if ed is None and self.model is not None:
            for name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
                meth = getattr(self.model, name, None)
                if callable(meth):
                    try:
                        ed = int(meth())
                        break
                    except Exception:  # pragma: no cover - defensive
                        continue
        if ed is None and self.store is not None:
            try:
                got = self.store.get(limit=1, include=["embeddings"])
                embs = got.get("embeddings") if isinstance(got, dict) else None
                if embs is not None and len(embs) > 0:
                    ed = len(embs[0])
            except Exception:  # pragma: no cover - defensive
                pass
        if ed is None:
            ed = EXPECTED_DIMENSION
        self.expected_dimension = int(ed)
        self.normalization_tolerance = float(normalization_tolerance)

    # ------------------------------------------------------------------ #
    # Model / encoder helpers
    # ------------------------------------------------------------------ #
    def _get_model(self) -> Any:
        if self.model is None:
            self.model = load_embedding_model(
                device="cpu",
                local_files_only=self.local_files_only,
                model_id=MODEL_ID,
                revision=MODEL_REVISION,
            )
        return self.model

    def _get_encoder(self) -> Any:
        if self.encoder is None:
            self.encoder = EmbeddingEncoder(
                self._get_model(),
                batch_size=16,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return self.encoder

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _validate_query_vector(self, value: Any) -> np.ndarray:
        vec = _as_vector(value)
        if vec.size == 0:
            raise QueryValidationError("query vector must not be empty")
        # Infer dimension lazily if needed.
        if self.expected_dimension is None:
            self.expected_dimension = EXPECTED_DIMENSION
        if vec.shape[0] != self.expected_dimension:
            raise QueryValidationError(
                f"query vector dimension {vec.shape[0]} != expected {self.expected_dimension}"
            )
        if not np.all(np.isfinite(vec)):
            raise QueryValidationError("query vector contains NaN or infinity")
        norm = float(np.linalg.norm(vec))
        if not math.isfinite(norm) or norm == 0.0:
            raise QueryValidationError("query vector is zero-length")
        if abs(norm - 1.0) > self.normalization_tolerance:
            raise QueryValidationError(
                f"query vector is not normalized (L2 norm {norm} != 1.0)"
            )
        return vec

    def _embed_text(self, query: str) -> np.ndarray:
        if not isinstance(query, str) or not query.strip():
            raise QueryValidationError("query must be a non-empty string")
        dummy = type(
            "_Q",
            (),
            {
                "token_chunk_id": "__query__",
                "parent_chunk_id": "__query__",
                "doc_id": "__query__",
                "text": query.strip(),
            },
        )()
        try:
            results = self._get_encoder().encode([dummy])
        except Exception as exc:
            raise SemanticSearchError(f"failed to embed query: {exc}") from exc
        if not results:
            raise SemanticSearchError("query embedding produced no vector")
        return self._validate_query_vector(results[0].embedding)

    # ------------------------------------------------------------------ #
    # Filtering
    # ------------------------------------------------------------------ #
    def _collect_filters(
        self,
        filters: Any = None,
        where: Any = None,
        *,
        doc_id: Any = None,
        category: Any = None,
        component: Any = None,
        severity: Any = None,
        parent_chunk_id: Any = None,
        title: Any = None,
        source_path: Any = None,
        **extra: Any,
    ) -> dict[str, Any] | None:
        collected: dict[str, Any] = {}

        def _add(key: str, value: Any) -> None:
            if value is None:
                return
            if key not in ALLOWED_FILTER_FIELDS:
                raise QueryValidationError(f"unsupported filter field: {key!r}")
            collected[key] = value

        if isinstance(filters, dict):
            for k, v in filters.items():
                _add(str(k), v)
        if isinstance(where, dict):
            for k, v in where.items():
                key = str(k)
                if key not in ALLOWED_FILTER_FIELDS:
                    raise QueryValidationError(f"unsupported filter field: {key!r}")
                # Support Chroma-style {"field": {"$eq": val}} and other operators.
                if isinstance(v, dict):
                    collected[key] = v
                else:
                    collected[key] = v

        _add("doc_id", doc_id)
        _add("category", category)
        _add("component", component)
        _add("severity", severity)
        _add("parent_chunk_id", parent_chunk_id)
        _add("title", title)
        _add("source_path", source_path)

        # Extra kwargs that look like filters.
        for k, v in extra.items():
            if v is None:
                continue
            if k in ALLOWED_FILTER_FIELDS:
                _add(k, v)

        if not collected:
            return None
        return collected

    def _build_where(self, collected: dict[str, Any] | None) -> Any:
        if not collected:
            return None
        if len(collected) == 1:
            key, value = next(iter(collected.items()))
            return {key: value}
        return {"$and": [{k: v} for k, v in collected.items()]}

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
        threshold: float | None = None,
        similarity_threshold: float | None = None,
        min_similarity: float | None = None,
        min_score: float | None = None,
        max_distance: float | None = None,
        filters: Any = None,
        where: Any = None,
        doc_id: Any = None,
        category: Any = None,
        component: Any = None,
        severity: Any = None,
        parent_chunk_id: Any = None,
        title: Any = None,
        source_path: Any = None,
        query_vector: Any = None,
        vector: Any = None,
        embedding: Any = None,
        **extra: Any,
    ) -> list[SearchResult]:
        # Resolve threshold.
        if threshold is None:
            threshold = similarity_threshold
        if threshold is None:
            threshold = min_similarity
        if threshold is None:
            threshold = min_score

        # Resolve top-k.
        tk = top_k
        if tk is None:
            tk = k
        if tk is None:
            tk = n_results
        if tk is None:
            tk = limit
        if tk is None:
            tk = DEFAULT_TOP_K
        try:
            tk = int(tk)
        except (TypeError, ValueError) as exc:
            raise QueryValidationError(f"invalid top_k: {tk!r}") from exc
        if tk < 1:
            tk = 1

        # Resolve query vector.
        qv = query_vector
        if qv is None:
            qv = vector
        if qv is None:
            qv = embedding
        if qv is None and query is not None:
            if isinstance(query, str):
                qv = self._embed_text(query)
            elif isinstance(query, (list, tuple, np.ndarray)) or hasattr(query, "tolist"):
                qv = query
            elif hasattr(query, "embedding"):
                qv = query.embedding
            elif hasattr(query, "vector"):
                qv = query.vector
            else:
                raise QueryValidationError("unsupported query type")
        if qv is None:
            raise QueryValidationError("a query or query vector is required")

        vec = self._validate_query_vector(qv)

        # Collect filters.
        collected = self._collect_filters(
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
        chroma_where = self._build_where(collected)

        store = self.store
        if store is None:
            raise SemanticSearchError("no vector store configured")

        try:
            total = int(store.count())
        except Exception:  # pragma: no cover - defensive
            total = 0
        if total <= 0:
            return []

        tk = min(tk, total)

        try:
            raw = store.collection.query(
                query_embeddings=[vec.tolist()],
                n_results=tk,
                where=chroma_where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise SemanticSearchError(f"Chroma query failed: {exc}") from exc

        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metas = (raw.get("metadatas") or [[]])[0]
        dists = (raw.get("distances") or [[]])[0]

        results: list[SearchResult] = []
        for i, cid in enumerate(ids):
            meta = metas[i] if i < len(metas) and metas[i] else {}
            meta = meta or {}
            dist = float(dists[i]) if i < len(dists) else 0.0
            sim = 1.0 - dist
            if threshold is not None and sim < float(threshold):
                continue
            if max_distance is not None and dist > float(max_distance):
                continue
            doc_text = docs[i] if i < len(docs) else ""
            if doc_text is None:
                doc_text = ""

            def _g(key: str, default: Any = "") -> Any:
                return meta.get(key, default)

            token_count_raw = _g("token_count", 0)
            try:
                token_count = int(token_count_raw) if token_count_raw not in (None, "") else 0
            except (TypeError, ValueError):
                token_count = 0

            scs = _g("source_char_start", None)
            sce = _g("source_char_end", None)
            try:
                scs = int(scs) if scs not in (None, "") else None
            except (TypeError, ValueError):
                scs = None
            try:
                sce = int(sce) if sce not in (None, "") else None
            except (TypeError, ValueError):
                sce = None

            result = SearchResult(
                chunk_id=str(cid),
                parent_chunk_id=str(_g("parent_chunk_id", "")),
                doc_id=str(_g("doc_id", "")),
                title=str(_g("title", "")),
                category=str(_g("category", "")),
                component=str(_g("component", "")),
                severity=str(_g("severity", "")),
                source_path=str(_g("source_path", "")),
                chunk_text="" if doc_text is None else str(doc_text),
                cosine_distance=dist,
                similarity_score=sim,
                tokenizer_name=str(_g("tokenizer_name", "")),
                tokenizer_revision=str(_g("tokenizer_revision", "")),
                token_count=token_count,
                source_char_start=scs,
                source_char_end=sce,
                metadata=dict(meta),
            )
            results.append(result)

        results.sort(key=lambda r: r.cosine_distance)
        return results

    # Aliases.
    def search_query(self, *a: Any, **kw: Any) -> list[SearchResult]:
        return self.search(*a, **kw)

    def query(self, *a: Any, **kw: Any) -> list[SearchResult]:
        return self.search(*a, **kw)

    def __call__(self, *a: Any, **kw: Any) -> list[SearchResult]:
        return self.search(*a, **kw)


# Module-level convenience functions.
def _make_engine(store: Any = None, model: Any = None, **kw: Any) -> SemanticSearchEngine:
    return SemanticSearchEngine(store, model, **kw)


def semantic_search(store_or_query: Any = None, query: Any = None, **kw: Any) -> list[SearchResult]:
    """Convenience wrapper.

    Supports ``semantic_search(store, query, ...)`` and
    ``semantic_search(query, store=store, ...)``.
    """
    ctor_keys = {
        "model",
        "embedding_model",
        "encoder",
        "persist_directory",
        "collection_name",
        "expected_dimension",
        "expected_dim",
        "dimension",
        "embedding_dimension",
        "dim",
        "normalization_tolerance",
        "local_files_only",
    }
    if _is_store(store_or_query):
        store = store_or_query
        q = query
    elif _is_store(kw.get("store")):
        store = kw.pop("store")
        q = store_or_query if query is None else query
    else:
        q = store_or_query
        store = kw.pop("store", None)

    ctor_kwargs = {k: v for k, v in kw.items() if k in ctor_keys}
    search_kwargs = {k: v for k, v in kw.items() if k not in ctor_keys}
    engine = SemanticSearchEngine(store, **ctor_kwargs)
    if q is None and "query" in search_kwargs:
        q = search_kwargs.pop("query")
    return engine.search(q, **search_kwargs)


def search(store_or_query: Any = None, query: Any = None, **kw: Any) -> list[SearchResult]:
    return semantic_search(store_or_query, query, **kw)


__all__ = [
    "SemanticSearchEngine",
    "SearchResult",
    "QueryValidationError",
    "SemanticSearchError",
    "ALLOWED_FILTER_FIELDS",
    "semantic_search",
    "search",
]
