"""Validated result models for OpsPilot semantic retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """One semantic-search hit returned from the ChromaDB collection.

    Fields preserve the original chunk text and provenance stored during
    Phase 2. ``cosine_distance`` is the raw ChromaDB distance (lower is
    better); ``similarity_score`` is ``1 - cosine_distance``.
    """

    chunk_id: str
    parent_chunk_id: str = ""
    doc_id: str = ""
    title: str = ""
    category: str = ""
    component: str = ""
    severity: str = ""
    source_path: str = ""
    chunk_text: str = ""
    cosine_distance: float | None = 0.0
    similarity_score: float | None = 0.0
    bm25_score: float | None = None
    bm25_rank: int | None = None
    semantic_rank: int | None = None
    rrf_score: float | None = None
    reranker_score: float | None = None
    final_rank: int | None = None
    tokenizer_name: str = ""
    tokenizer_revision: str = ""
    token_count: int = 0
    source_char_start: int | None = None
    source_char_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Convenience aliases
    # ------------------------------------------------------------------ #
    @property
    def id(self) -> str:  # noqa: D401
        return self.chunk_id

    @property
    def token_chunk_id(self) -> str:  # noqa: D401
        return self.chunk_id

    @property
    def text(self) -> str:  # noqa: D401
        return self.chunk_text

    @property
    def document(self) -> str:  # noqa: D401
        return self.chunk_text

    @property
    def distance(self) -> float:  # noqa: D401
        return self.cosine_distance

    @property
    def score(self) -> float:  # noqa: D401
        return self.similarity_score


@dataclass
class HybridSearchResult:
    """A fused hybrid-retrieval hit.

    Semantic and lexical quantities are kept separate. Fields for a retrieval
    source that did not produce a value remain ``None`` instead of being
    fabricated.
    """

    chunk_id: str
    parent_chunk_id: str = ""
    doc_id: str = ""
    title: str = ""
    category: str = ""
    component: str = ""
    severity: str = ""
    source_path: str = ""
    chunk_text: str = ""
    cosine_distance: float | None = None
    semantic_similarity: float | None = None
    semantic_rank: int | None = None
    bm25_score: float | None = None
    bm25_rank: int | None = None
    rrf_score: float = 0.0
    reranker_score: float | None = None
    final_rank: int = 0
    tokenizer_name: str = ""
    tokenizer_revision: str = ""
    token_count: int = 0
    source_char_start: int | None = None
    source_char_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Convenience aliases
    # ------------------------------------------------------------------ #
    @property
    def id(self) -> str:
        return self.chunk_id

    @property
    def token_chunk_id(self) -> str:
        return self.chunk_id

    @property
    def text(self) -> str:
        return self.chunk_text

    @property
    def document(self) -> str:
        return self.chunk_text

    @property
    def similarity_score(self) -> float | None:
        return self.semantic_similarity

    @property
    def distance(self) -> float | None:
        return self.cosine_distance

    @property
    def score(self) -> float:
        return self.rrf_score


# Backwards/alternate name for the same abstraction.
HybridResult = HybridSearchResult


__all__ = [
    "SearchResult",
    "HybridSearchResult",
    "HybridResult",
]
