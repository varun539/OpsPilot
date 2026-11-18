"""Repeatable knowledge-base indexing into the persistent ChromaDB store.

This package orchestrates the existing ingestion, chunking, token-chunking,
embedding and vector-store modules. It does not implement a second pipeline.
"""

from __future__ import annotations

from .indexer import KnowledgeIndexResult, index_knowledge_base

__all__ = ["KnowledgeIndexResult", "index_knowledge_base"]
