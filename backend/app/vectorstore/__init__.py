"""Persistent ChromaDB storage for OpsPilot embedding records.

This package stores already-generated embeddings. It does not generate
embeddings, run retrieval, or download model weights.

The ChromaDB-backed class is imported lazily so that lightweight consumers
(such as configuration) can read the shared constants without importing
ChromaDB or NumPy.
"""

from __future__ import annotations

from typing import Any

from .constants import DEFAULT_COLLECTION_NAME, DEFAULT_DISTANCE_METRIC

__all__ = [
    "ChromaVectorStore",
    "ChromaStore",
    "DEFAULT_COLLECTION_NAME",
    "DEFAULT_DISTANCE_METRIC",
]


def __getattr__(name: str) -> Any:
    if name in {"ChromaVectorStore", "ChromaStore"}:
        from .chroma_store import ChromaVectorStore

        return ChromaVectorStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
