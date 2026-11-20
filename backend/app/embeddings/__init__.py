"""CPU-only embedding generation for OpsPilot token-aware chunks.

This package turns validated :class:`TokenDocumentChunk` objects into dense
vectors using a pinned SentenceTransformer model. It does not persist
anything: vectors are returned in memory for the caller to store.

Public API::

    from backend.app.embeddings import load_embedding_model, EmbeddingEncoder

    model = load_embedding_model()
    encoder = EmbeddingEncoder(model, batch_size=16)
    results = encoder.encode(token_chunks)
"""

from __future__ import annotations

from .encoder import (
    ChunkEncoder,
    EmbeddedChunk,
    EmbeddingEncoder,
    EmbeddingResult,
    Encoder,
    TokenEmbedding,
    encode_chunks,
    encode_token_chunks,
)
from .model import (
    EXPECTED_DIMENSION,
    MIN_MAX_SEQ_LENGTH,
    MODEL_ID,
    MODEL_REVISION,
    ModelLoadError,
    load_embedding_model,
    verify_cpu_only,
)

__all__ = [
    "MODEL_ID",
    "MODEL_REVISION",
    "EXPECTED_DIMENSION",
    "MIN_MAX_SEQ_LENGTH",
    "ModelLoadError",
    "load_embedding_model",
    "verify_cpu_only",
    "EmbeddedChunk",
    "EmbeddingResult",
    "TokenEmbedding",
    "EmbeddingEncoder",
    "Encoder",
    "ChunkEncoder",
    "encode_chunks",
    "encode_token_chunks",
]
