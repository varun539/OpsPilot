"""BM25 keyword retrieval over the persisted OpsPilot ChromaDB collection.

This module builds a small in-memory lexical index from the chunks stored in
ChromaDB. It performs no embedding generation and downloads no model weights.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from backend.app.vectorstore import ChromaVectorStore

from .models import SearchResult
from .semantic_search import collect_filters, metadata_matches

# A conservative tokenizer: keep alphanumerics and underscores together so
# identifiers such as ``connection_pool``, ``asyncpg`` and ``JWT`` survive.
# Hyphenated/dotted technical identifiers are split on the separator but the
# individual parts are retained, which is a reasonable BM25 compromise.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: Any) -> list[str]:
    if text is None:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(str(text))]


DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


class BM25SearchError(RuntimeError):
    """Raised when BM25 retrieval cannot be performed."""


class BM25SearchEngine:
    """In-memory BM25 index over ChromaDB chunks."""

    def __init__(
        self,
        store: Any = None,
        vector_store: Any = None,
        chroma_store: Any = None,
        chroma: Any = None,
        persist_directory: Any = None,
        collection_name: str | None = None,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
        **kwargs: Any,
    ) -> None:
        self.store = store or vector_store or chroma_store or chroma
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.k1 = float(k1)
        self.b = float(b)

        if self.store is None and persist_directory is not None:
            try:
                self.store = ChromaVectorStore(
                    persist_directory=persist_directory,
                    collection_name=collection_name or "opspilot_embeddings",
                )
            except Exception as exc:  # pragma: no cover - defensive
                raise BM25SearchError(f"failed to open store: {exc}") from exc

        # Index state.
        self._ids: list[str] = []
        self._documents: list[str] = []
        self._metadatas: list[dict[str, Any]] = []
        self._tokens: list[list[str]] = []
        self._doc_lengths: np.ndarray | None = None
        self._avgdl: float = 0.0
        self._idf: dict[str, float] = {}
        self._built: bool = False

    # ------------------------------------------------------------------ #
    # Index management
    # ------------------------------------------------------------------ #
    def _fetch_all(self) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        store = self.store
        if store is None:
            raise BM25SearchError("no vector store configured")
        try:
            total = int(store.count())
        except Exception as exc:  # pragma: no cover - defensive
            raise BM25SearchError(f"failed to count store: {exc}") from exc
        if total <= 0:
            return [], [], []
        try:
            raw = store.get(limit=total, include=["documents", "metadatas"])
        except Exception as exc:
            raise BM25SearchError(f"failed to read store: {exc}") from exc
        ids = list(raw.get("ids") or [])
        docs = list(raw.get("documents") or [])
        metas = list(raw.get("metadatas") or [])
        # Pad.
        while len(docs) < len(ids):
            docs.append("")
        while len(metas) < len(ids):
            metas.append({})
        metas = [m or {} for m in metas]
        return ids, docs, metas

    def build_index(self) -> int:
        ids, docs, metas = self._fetch_all()
        self._ids = [str(i) for i in ids]
        self._documents = ["" if d is None else str(d) for d in docs]
        self._metadatas = [dict(m or {}) for m in metas]
        self._tokens = [tokenize(d) for d in self._documents]

        if not self._tokens:
            self._doc_lengths = np.zeros(0, dtype=np.float64)
            self._avgdl = 0.0
            self._idf = {}
            self._built = True
            return 0

        self._doc_lengths = np.asarray([len(t) for t in self._tokens], dtype=np.float64)
        self._avgdl = float(self._doc_lengths.mean()) if self._doc_lengths.size else 0.0
        if self._avgdl == 0.0:
            self._avgdl = 1.0

        # Document frequency.
        df: dict[str, int] = {}
        for tokens in self._tokens:
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1

        n = len(self._tokens)
        self._idf = {}
        for term, freq in df.items():
            # Standard BM25 idf with smoothing.
            self._idf[term] = math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))

        self._built = True
        return len(self._ids)

    def refresh(self) -> int:
        self._built = False
        return self.build_index()

    def _ensure_index(self) -> None:
        if not self._built:
            self.build_index()

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #
    def _score_one(self, query_tokens: list[str], idx: int) -> float:
        if not query_tokens:
            return 0.0
        tokens = self._tokens[idx]
        if not tokens:
            return 0.0
        tf_map: dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        dl = float(self._doc_lengths[idx]) if self._doc_lengths is not None else 0.0
        score = 0.0
        for term in query_tokens:
            if term not in self._idf:
                continue
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf[term]
            denom = tf + self.k1 * (1.0 - self.b + self.b * (dl / self._avgdl))
            score += idf * (tf * (self.k1 + 1.0)) / denom
        return float(score)

    def search(
        self,
        query: Any = None,
        *,
        top_k: int | None = None,
        k: int | None = None,
        n_results: int | None = None,
        limit: int | None = None,
        filters: Any = None,
        where: Any = None,
        doc_id: Any = None,
        category: Any = None,
        component: Any = None,
        severity: Any = None,
        parent_chunk_id: Any = None,
        title: Any = None,
        source_path: Any = None,
        **extra: Any,
    ) -> list[SearchResult]:
        self._ensure_index()

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

        collected = collect_filters(
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

        # Query tokens. Support query text or a simple object with .text.
        if hasattr(query, "text") and not isinstance(query, str):
            query_text = getattr(query, "text")
        else:
            query_text = query
        q_tokens = tokenize(query_text)

        # Candidate indices.
        candidates: list[int] = []
        for i, meta in enumerate(self._metadatas):
            if metadata_matches(meta, collected):
                candidates.append(i)

        if not candidates or not q_tokens:
            return []

        scored: list[tuple[int, float]] = []
        for i in candidates:
            s = self._score_one(q_tokens, i)
            if s > 0.0:
                scored.append((i, s))

        # Sort by score desc, then id asc for stability.
        scored.sort(key=lambda x: (-x[1], self._ids[x[0]]))

        tk = min(tk, len(scored))
        out: list[SearchResult] = []
        for rank, (i, s) in enumerate(scored[:tk], start=1):
            meta = self._metadatas[i]

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

            out.append(
                SearchResult(
                    chunk_id=str(self._ids[i]),
                    parent_chunk_id=str(_g("parent_chunk_id", "")),
                    doc_id=str(_g("doc_id", "")),
                    title=str(_g("title", "")),
                    category=str(_g("category", "")),
                    component=str(_g("component", "")),
                    severity=str(_g("severity", "")),
                    source_path=str(_g("source_path", "")),
                    chunk_text=self._documents[i],
                    cosine_distance=None,
                    similarity_score=None,
                    bm25_score=float(s),
                    bm25_rank=rank,
                    tokenizer_name=str(_g("tokenizer_name", "")),
                    tokenizer_revision=str(_g("tokenizer_revision", "")),
                    token_count=token_count,
                    source_char_start=scs,
                    source_char_end=sce,
                    metadata=dict(meta),
                )
            )
        return out

    # Aliases.
    def search_query(self, *a: Any, **kw: Any) -> list[SearchResult]:
        return self.search(*a, **kw)

    def query(self, *a: Any, **kw: Any) -> list[SearchResult]:
        return self.search(*a, **kw)

    def __call__(self, *a: Any, **kw: Any) -> list[SearchResult]:
        return self.search(*a, **kw)

    @property
    def size(self) -> int:
        return len(self._ids)

    def close(self) -> None:
        pass


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "BM25SearchEngine",
    "BM25SearchError",
    "tokenize",
]
