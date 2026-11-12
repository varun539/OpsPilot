"""Knowledge base ingestion: Markdown documents in, validated objects out.

Public API for the rest of the application::

    from backend.app.ingestion import ingest_knowledge_base

    documents = ingest_knowledge_base()          # data/knowledge_base/*.md
    documents[0].metadata.doc_id                 # "KB-001"
    documents[0].content                         # cleaned Markdown body

    from backend.app.ingestion import chunk_documents

    chunks = chunk_documents(documents)          # 1000-char chunks, 150 overlap
    chunks[0].chunk_id                           # "KB-001::chunk::0000"

Lower-level helpers are re-exported for tooling and tests. Nothing here embeds
or indexes text yet; this package only produces clean, trustworthy documents
and their searchable chunks for those later stages.
"""

from __future__ import annotations

from .chunker import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_document,
    chunk_documents,
)
from .cleaner import clean_document_text
from .loader import discover_documents, load_document_text
from .models import (
    DocumentChunk,
    DocumentLoadError,
    DocumentMetadata,
    DocumentValidationError,
    DuplicateDocumentIdError,
    FrontmatterError,
    IngestionError,
    KnowledgeBaseError,
    KnowledgeDocument,
    MalformedFrontmatterError,
    MetadataValidationError,
    MissingFrontmatterError,
)
from .parser import parse_document, parse_metadata, split_frontmatter
from .pipeline import DEFAULT_KNOWLEDGE_BASE_DIR, ingest_document, ingest_knowledge_base

__all__ = [
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_KNOWLEDGE_BASE_DIR",
    "DocumentChunk",
    "DocumentLoadError",
    "DocumentMetadata",
    "DocumentValidationError",
    "DuplicateDocumentIdError",
    "FrontmatterError",
    "IngestionError",
    "KnowledgeBaseError",
    "KnowledgeDocument",
    "MalformedFrontmatterError",
    "MetadataValidationError",
    "MissingFrontmatterError",
    "chunk_document",
    "chunk_documents",
    "clean_document_text",
    "discover_documents",
    "ingest_document",
    "ingest_knowledge_base",
    "load_document_text",
    "parse_document",
    "parse_metadata",
    "split_frontmatter",
]
