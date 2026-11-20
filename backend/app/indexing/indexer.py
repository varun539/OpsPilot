"""Idempotent knowledge-base indexing over the existing OpsPilot pipeline.

Reuses, in order:

1. :func:`backend.app.ingestion.ingest_knowledge_base`
2. :func:`backend.app.ingestion.chunk_documents`
3. :func:`backend.app.ingestion.token_chunk_documents`
4. :class:`backend.app.embeddings.EmbeddingEncoder`
5. :class:`backend.app.vectorstore.ChromaVectorStore`

Chunk IDs are deterministic and the store upserts by ID, so running the
command repeatedly updates the same records instead of creating duplicates.
The command never deletes the collection or unrelated stored records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 16


@dataclass(frozen=True)
class KnowledgeIndexResult:
    """Counts reported by one indexing run."""

    documents: int
    parent_chunks: int
    token_chunks: int
    upserted: int
    collection_count: int
    collection_name: str
    dry_run: bool
    directory: str

    def summary(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "parent_chunks": self.parent_chunks,
            "token_chunks": self.token_chunks,
            "upserted": self.upserted,
            "collection_count": self.collection_count,
            "collection": self.collection_name,
            "dry_run": self.dry_run,
            "directory": self.directory,
        }


def _load_tokenizer(*, local_files_only: bool) -> Any:
    try:
        import transformers
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "the embedding stack is not installed; install "
            "requirements-embeddings.txt in this environment"
        ) from exc

    from backend.app.embeddings import MODEL_ID, MODEL_REVISION

    return transformers.AutoTokenizer.from_pretrained(
        MODEL_ID,
        use_fast=True,
        local_files_only=local_files_only,
        revision=MODEL_REVISION,
    )


def index_knowledge_base(
    *,
    directory: Path | str | None = None,
    store: Any = None,
    model: Any = None,
    tokenizer: Any = None,
    dry_run: bool = False,
    local_files_only: bool = False,
) -> KnowledgeIndexResult:
    """Index every supported document into the persistent collection.

    Args:
        directory: knowledge-base folder; defaults to ``data/knowledge_base``.
        store: existing vector store (tests); a persistent one is created when
            omitted.
        model: existing SentenceTransformer (tests); the pinned model is loaded
            when omitted.
        tokenizer: existing fast tokenizer (tests); otherwise the model's
            tokenizer is used so token counts match the encoded vectors.
        dry_run: parse, clean, chunk and token-chunk only; no model download,
            no embedding and no writes.
        local_files_only: never download model/tokenizer files.
    """
    from backend.app.embeddings import (
        EXPECTED_DIMENSION,
        MODEL_ID,
        MODEL_REVISION,
        EmbeddingEncoder,
    )
    from backend.app.ingestion import (
        DEFAULT_KNOWLEDGE_BASE_DIR,
        chunk_documents,
        ingest_knowledge_base,
        token_chunk_documents,
    )
    from backend.app.vectorstore import ChromaVectorStore

    resolved_directory = Path(directory) if directory is not None else DEFAULT_KNOWLEDGE_BASE_DIR
    documents = ingest_knowledge_base(resolved_directory)
    parent_chunks = chunk_documents(documents)

    if dry_run:
        if tokenizer is None and model is not None:
            tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is None:
            tokenizer = _load_tokenizer(local_files_only=local_files_only)
        token_chunks = token_chunk_documents(parent_chunks, tokenizer)
        collection_count = 0
        collection_name = ""
        if store is not None:
            collection_count = int(store.count())
            collection_name = str(getattr(store, "collection_name", ""))
        return KnowledgeIndexResult(
            documents=len(documents),
            parent_chunks=len(parent_chunks),
            token_chunks=len(token_chunks),
            upserted=0,
            collection_count=collection_count,
            collection_name=collection_name,
            dry_run=True,
            directory=str(resolved_directory),
        )

    owns_store = store is None
    if model is None:
        try:
            from backend.app.embeddings import load_embedding_model

            model = load_embedding_model(
                device="cpu",
                local_files_only=local_files_only,
                model_id=MODEL_ID,
                revision=MODEL_REVISION,
            )
        except Exception as exc:
            raise RuntimeError(
                f"failed to load embedding model {MODEL_ID} "
                f"(revision {MODEL_REVISION}): {type(exc).__name__}"
            ) from exc

    try:
        if tokenizer is None:
            tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is None:
            tokenizer = _load_tokenizer(local_files_only=local_files_only)

        token_chunks = token_chunk_documents(parent_chunks, tokenizer)

        if store is None:
            store = ChromaVectorStore(expected_dimension=EXPECTED_DIMENSION)

        encoder = EmbeddingEncoder(
            model,
            batch_size=DEFAULT_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        embedded = encoder.encode(token_chunks)
        upserted = store.upsert(embedded, token_chunks=token_chunks)
        collection_count = int(store.count())

        logger.info(
            "Indexed %d document(s), %d token chunk(s), %d upserted; collection count %d",
            len(documents),
            len(token_chunks),
            upserted,
            collection_count,
        )
        return KnowledgeIndexResult(
            documents=len(documents),
            parent_chunks=len(parent_chunks),
            token_chunks=len(token_chunks),
            upserted=upserted,
            collection_count=collection_count,
            collection_name=str(getattr(store, "collection_name", "")),
            dry_run=False,
            directory=str(resolved_directory),
        )
    finally:
        if owns_store and store is not None:
            store.close()
