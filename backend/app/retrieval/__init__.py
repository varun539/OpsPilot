"""Semantic search over the persistent OpsPilot ChromaDB collection.

Retrieval only: no RAG, LLM, or agent orchestration.
"""

from __future__ import annotations

from .models import SearchResult
from .semantic_search import (
    ALLOWED_FILTER_FIELDS,
    QueryValidationError,
    SemanticSearchEngine,
    SemanticSearchError,
    search,
    semantic_search,
)

__all__ = [
    "SearchResult",
    "SemanticSearchEngine",
    "QueryValidationError",
    "SemanticSearchError",
    "ALLOWED_FILTER_FIELDS",
    "semantic_search",
    "search",
]
