"""Encoding validated token chunks into dense vectors.

The encoder preserves input ordering and provenance (chunk IDs, parent IDs,
document IDs) while producing one vector per input chunk. Vectors are kept in
memory only; persistence is a later milestone.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

DEFAULT_BATCH_SIZE = 16
DEFAULT_TOLERANCE = 1e-5


@dataclass(frozen=True)
class EmbeddedChunk:
    """One embedding together with the provenance of its source token chunk."""

    token_chunk_id: str
    parent_chunk_id: str
    doc_id: str
    embedding: np.ndarray

    # Convenience aliases kept for compatibility with alternate naming.
    @property
    def chunk_id(self) -> str:  # noqa: D401 - alias
        return self.token_chunk_id

    @property
    def vector(self) -> np.ndarray:  # noqa: D401 - alias
        return self.embedding


# Backwards/alternate names for the same abstraction.
EmbeddingResult = EmbeddedChunk
TokenEmbedding = EmbeddedChunk


class _HasEncode(Protocol):
    def encode(
        self,
        sentences: Any,
        *,
        batch_size: int = ...,
        normalize_embeddings: bool = ...,
        convert_to_numpy: bool = ...,
        show_progress_bar: bool = ...,
    ) -> Any: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class EmbeddingValidationError(ValueError):
    """Raised when an embedding fails numerical or shape validation."""


def _as_numpy(value: Any) -> np.ndarray:
    """Convert a model output into a 2-D numpy array."""
    if isinstance(value, np.ndarray):
        array = value
    elif hasattr(value, "detach") and hasattr(value, "cpu"):
        array = value.detach().cpu().numpy()
    elif isinstance(value, (list, tuple)):
        array = np.asarray(value)
    else:
        array = np.asarray(value)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    return array


def _validate_batch_size(batch_size: Any) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("batch_size must be an integer")
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    return batch_size


def _chunk_fields(chunk: Any) -> tuple[str, str, str, str]:
    """Extract (token_chunk_id, parent_chunk_id, doc_id, text) from a chunk."""
    token_chunk_id = getattr(chunk, "token_chunk_id", None)
    if token_chunk_id is None:
        token_chunk_id = getattr(chunk, "chunk_id", None)
    parent_chunk_id = getattr(chunk, "parent_chunk_id", None)
    doc_id = getattr(chunk, "doc_id", None)
    text = getattr(chunk, "text", None)
    if token_chunk_id is None or parent_chunk_id is None or doc_id is None or text is None:
        raise ValueError(
            "chunk is missing required provenance fields "
            "(token_chunk_id/parent_chunk_id/doc_id/text)"
        )
    return str(token_chunk_id), str(parent_chunk_id), str(doc_id), str(text)


def _model_dimension(model: Any) -> int:
    for name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        method = getattr(model, name, None)
        if callable(method):
            try:
                value = method()
                if value is not None:
                    return int(value)
            except Exception:  # pragma: no cover - defensive
                continue
    # Fallback: infer from a later encode? Not possible here.
    return 0


class EmbeddingEncoder:
    """Encode :class:`TokenDocumentChunk` objects into vectors."""

    def __init__(
        self,
        model: Any,
        batch_size: int = DEFAULT_BATCH_SIZE,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
        tolerance: float = DEFAULT_TOLERANCE,
    ) -> None:
        self.model = model
        self.batch_size = _validate_batch_size(batch_size)
        self.normalize_embeddings = bool(normalize_embeddings)
        self.convert_to_numpy = bool(convert_to_numpy)
        self.show_progress_bar = bool(show_progress_bar)
        self.tolerance = float(tolerance)

    def encode(self, chunks: Sequence[Any] | Iterable[Any]) -> list[EmbeddedChunk]:
        chunk_list = list(chunks)
        if not chunk_list:
            return []

        # Validate batch size again (in case it was mutated).
        self.batch_size = _validate_batch_size(self.batch_size)

        ids_and_text = [_chunk_fields(c) for c in chunk_list]
        texts = [item[3] for item in ids_and_text]

        expected_dim = _model_dimension(self.model)
        if expected_dim <= 0:
            expected_dim = 384

        try:
            raw = self.model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=self.normalize_embeddings,
                convert_to_numpy=self.convert_to_numpy,
                show_progress_bar=self.show_progress_bar,
            )
        except TypeError:
            # Some (mock) models may not accept all kwargs; fall back to positional.
            raw = self.model.encode(texts)

        vectors = _as_numpy(raw)

        if vectors.shape[0] != len(chunk_list):
            raise EmbeddingValidationError(
                f"encoder returned {vectors.shape[0]} vectors for {len(chunk_list)} chunks"
            )
        if vectors.shape[1] != expected_dim:
            raise EmbeddingValidationError(
                f"embedding dimension {vectors.shape[1]} != expected {expected_dim}"
            )

        results: list[EmbeddedChunk] = []
        for (token_chunk_id, parent_chunk_id, doc_id, _text), vector in zip(
            ids_and_text, vectors, strict=True
        ):
            vec = np.asarray(vector, dtype=np.float64)
            if not np.all(np.isfinite(vec)):
                raise EmbeddingValidationError(
                    f"embedding for {token_chunk_id} contains NaN or infinity"
                )
            norm = float(np.linalg.norm(vec))
            if norm == 0.0 or not math.isfinite(norm):
                raise EmbeddingValidationError(
                    f"embedding for {token_chunk_id} is zero-length"
                )
            if self.normalize_embeddings:
                if abs(norm - 1.0) > self.tolerance:
                    raise EmbeddingValidationError(
                        f"embedding for {token_chunk_id} is not normalized "
                        f"(L2 norm {norm} != 1.0)"
                    )
            # Store as float32 by default (documented suitable dtype).
            stored = np.asarray(vector, dtype=np.float32)
            results.append(
                EmbeddedChunk(
                    token_chunk_id=token_chunk_id,
                    parent_chunk_id=parent_chunk_id,
                    doc_id=doc_id,
                    embedding=stored,
                )
            )

        return results

    def encode_chunks(self, chunks: Sequence[Any] | Iterable[Any]) -> list[EmbeddedChunk]:
        return self.encode(chunks)

    __call__ = encode


def encode_chunks(
    model: Any,
    chunks: Sequence[Any] | Iterable[Any],
    batch_size: int = DEFAULT_BATCH_SIZE,
    **kwargs: Any,
) -> list[EmbeddedChunk]:
    """Convenience wrapper around :class:`EmbeddingEncoder`."""
    encoder = EmbeddingEncoder(model, batch_size=batch_size, **kwargs)
    return encoder.encode(chunks)


def encode_token_chunks(
    model: Any,
    chunks: Sequence[Any] | Iterable[Any],
    batch_size: int = DEFAULT_BATCH_SIZE,
    **kwargs: Any,
) -> list[EmbeddedChunk]:
    return encode_chunks(model, chunks, batch_size=batch_size, **kwargs)


# Aliases for alternate import expectations.
Encoder = EmbeddingEncoder
ChunkEncoder = EmbeddingEncoder
