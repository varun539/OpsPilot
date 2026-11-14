"""Persistent ChromaDB storage for OpsPilot embedding records.

This package stores already-generated embeddings. It does not generate
embeddings, run retrieval, or download model weights.
"""

from __future__ import annotations

from .chroma_store import ChromaVectorStore

# Backwards/alternate name for the same abstraction.
ChromaStore = ChromaVectorStore

__all__ = ["ChromaVectorStore", "ChromaStore"]