"""Semantic, BM25, and hybrid retrieval over the OpsPilot ChromaDB collection.

Retrieval only: no RAG, LLM, or agent orchestration.
"""

from __future__ import annotations

from .bm25_search import BM25SearchEngine
from .hybrid_search import HybridSearchEngine, HybridSearchResult
from .models import HybridResult, SearchResult
from .reranker import CrossEncoderReranker
from .semantic_search import (
    ALLOWED_FILTER_FIELDS,
    QueryValidationError,
    SemanticSearchEngine,
    SemanticSearchError,
    build_where,
    collect_filters,
    metadata_matches,
    search,
    semantic_search,
)

__all__ = [
    "SearchResult",
    "HybridSearchResult",
    "HybridResult",
    "SemanticSearchEngine",
    "BM25SearchEngine",
    "HybridSearchEngine",
    "CrossEncoderReranker",
    "QueryValidationError",
    "SemanticSearchError",
    "ALLOWED_FILTER_FIELDS",
    "collect_filters",
    "build_where",
    "metadata_matches",
    "semantic_search",
    "search",
]
