"""Dependency-free constants shared by the vector store and AI runtime."""

from __future__ import annotations

#: Persistent ChromaDB collection used by indexing, retrieval and RAG.
DEFAULT_COLLECTION_NAME = "opspilot_embeddings"

#: Cosine distance is the collection's configured space. Stored vectors are
#: L2-normalized, so cosine distance is interpreted as ``1 - similarity``.
DEFAULT_DISTANCE_METRIC = "cosine"
