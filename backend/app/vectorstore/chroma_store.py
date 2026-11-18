"""Persistent ChromaDB storage for OpsPilot embedding records.

The store accepts already-generated :class:`EmbeddedChunk` records and writes
them to a local Chroma collection. It never generates embeddings, performs
retrieval, or downloads model weights.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import chromadb
import numpy as np

from .constants import DEFAULT_COLLECTION_NAME

DEFAULT_BATCH_SIZE = 100
DEFAULT_NORMALIZATION_TOLERANCE = 1e-5


class ChromaStoreError(RuntimeError):
    """Raised when the Chroma store cannot be initialized or used."""


class VectorValidationError(ValueError):
    """Raised when an embedding fails shape, finiteness, or normalization checks."""


def _repo_root() -> Path:
    # backend/app/vectorstore/chroma_store.py -> parents[3] is the repo root.
    return Path(__file__).resolve().parents[3]


def _default_persist_directory() -> Path:
    # Container deployments point this at a persistent volume; local runs fall
    # back to the repository-local ``chroma/`` directory.
    configured = os.getenv("OPSPILOT_CHROMA_DIR", "").strip()
    if configured:
        return Path(configured)
    return _repo_root() / "chroma"


def _as_vector(value: Any) -> np.ndarray:
    if isinstance(value, np.ndarray):
        array = value
    elif hasattr(value, "detach") and hasattr(value, "cpu"):
        array = value.detach().cpu().numpy()
    else:
        array = np.asarray(value)
    if array.ndim != 1:
        array = np.ravel(array)
    return np.asarray(array, dtype=np.float64)


def _json_or_none(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _scalar(value: Any) -> Any:
    """Coerce a value to a Chroma-supported scalar, or None to drop it."""
    if value is None:
        return None
    # Normalize numpy / torch scalars to native Python types.
    container_types = (str, bytes, bytearray, list, tuple, dict, set)
    if hasattr(value, "item") and not isinstance(value, container_types):
        try:
            return _scalar(value.item())
        except (TypeError, ValueError, AttributeError):
            pass
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        # Reject non-finite floats.
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, Path):
        return str(value)
    # Dates / datetimes.
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # pragma: no cover - defensive
            pass
    # Lists / dicts / tuples -> JSON string.
    if isinstance(value, (list, tuple, dict, set)):
        return json.dumps(value, default=str, sort_keys=True)
    return str(value)


def _token_chunk_map(token_chunks: Sequence[Any] | None) -> dict[str, Any]:
    if not token_chunks:
        return {}
    result: dict[str, Any] = {}
    for chunk in token_chunks:
        key = getattr(chunk, "token_chunk_id", None)
        if key is None:
            key = getattr(chunk, "chunk_id", None)
        if key is not None:
            result[str(key)] = chunk
    return result


def _metadata_from(embedded: Any, token_chunk: Any | None) -> dict[str, Any]:
    token_id = str(getattr(embedded, "token_chunk_id", getattr(embedded, "chunk_id", "")))
    metadata: dict[str, Any] = {
        "token_chunk_id": token_id,
        "parent_chunk_id": str(getattr(embedded, "parent_chunk_id", "")),
        "doc_id": str(getattr(embedded, "doc_id", "")),
    }

    if token_chunk is None:
        return {
            k: v for k, v in metadata.items() if v not in (None, "") or k in ("token_chunk_id",)
        }

    # Fields copied directly when present.
    direct_fields = [
        "parent_chunk_index",
        "title",
        "category",
        "component",
        "severity",
        "source_type",
        "source_path",
        "tokenizer_name",
        "token_count",
        "max_tokens",
        "token_overlap",
        "source_char_start",
        "source_char_end",
        "applies_to",
    ]
    for field in direct_fields:
        value = getattr(token_chunk, field, None)
        coerced = _scalar(value)
        if coerced is not None and coerced != "":
            metadata[field] = coerced
        elif coerced == "" and field in ("applies_to",):
            metadata[field] = ""

    # Tokenizer revision (may be None -> store empty string for traceability).
    revision = getattr(token_chunk, "tokenizer_revision", None)
    if isinstance(revision, str):
        metadata["tokenizer_revision"] = revision
    elif revision is None:
        metadata["tokenizer_revision"] = ""
    else:
        metadata["tokenizer_revision"] = str(revision)

    # last_updated (date/datetime).
    last_updated = getattr(token_chunk, "last_updated", None)
    if last_updated is not None:
        metadata["last_updated"] = _scalar(last_updated)

    # Nested structures serialized.
    tags = getattr(token_chunk, "tags", None)
    if tags is not None:
        metadata["tags"] = json.dumps(list(tags), default=str, sort_keys=True)

    related = getattr(token_chunk, "related_documents", None)
    if related is not None:
        metadata["related_documents"] = json.dumps(list(related), default=str, sort_keys=True)

    extra = getattr(token_chunk, "extra_metadata", None)
    if extra:
        metadata["extra_metadata"] = json.dumps(extra, default=str, sort_keys=True)

    # Drop None values; keep empty strings only where already set.
    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        cleaned[key] = value
    return cleaned


class ChromaVectorStore:
    """A thin, maintainable wrapper around a persistent Chroma collection."""

    def __init__(
        self,
        persist_directory: str | Path | None = None,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        batch_size: int = DEFAULT_BATCH_SIZE,
        expected_dimension: int | None = None,
        normalization_tolerance: float = DEFAULT_NORMALIZATION_TOLERANCE,
    ) -> None:
        if persist_directory is not None:
            self.persist_directory = Path(persist_directory)
        else:
            self.persist_directory = _default_persist_directory()
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.batch_size = max(1, int(batch_size))
        self.expected_dimension = expected_dimension
        self.normalization_tolerance = float(normalization_tolerance)

        try:
            self.client = chromadb.PersistentClient(path=str(self.persist_directory))
        except Exception as exc:
            raise ChromaStoreError(f"failed to initialize Chroma client: {exc}") from exc

        try:
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=None,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            raise ChromaStoreError(
                f"failed to create collection {self.collection_name!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _validate_vectors(
        self,
        embedded_chunks: Sequence[Any],
        vectors: list[np.ndarray],
    ) -> None:
        if not vectors:
            return
        dims = {v.shape[0] for v in vectors}
        if len(dims) != 1:
            raise VectorValidationError(f"inconsistent embedding dimensions: {sorted(dims)}")
        dim = next(iter(dims))
        if dim <= 0:
            raise VectorValidationError("embedding dimension must be positive")
        if self.expected_dimension is not None and dim != self.expected_dimension:
            raise VectorValidationError(
                f"embedding dimension {dim} != expected {self.expected_dimension}"
            )
        for embedded, vector in zip(embedded_chunks, vectors, strict=True):
            if not np.all(np.isfinite(vector)):
                raise VectorValidationError(
                    "embedding for "
                    f"{getattr(embedded, 'token_chunk_id', embedded)} "
                    "contains NaN or infinity"
                )
            norm = float(np.linalg.norm(vector))
            if not math.isfinite(norm) or norm == 0.0:
                raise VectorValidationError(
                    f"embedding for {getattr(embedded, 'token_chunk_id', embedded)} is zero-length"
                )
            if abs(norm - 1.0) > self.normalization_tolerance:
                raise VectorValidationError(
                    "embedding for "
                    f"{getattr(embedded, 'token_chunk_id', embedded)} "
                    "is not normalized "
                    f"(L2 norm {norm} != 1.0)"
                )

    # ------------------------------------------------------------------ #
    # Upsert
    # ------------------------------------------------------------------ #
    def upsert(
        self,
        embedded_chunks: Sequence[Any] | Iterable[Any],
        token_chunks: Sequence[Any] | None = None,
        batch_size: int | None = None,
    ) -> int:
        if embedded_chunks is None:
            chunk_list = []
        else:
            chunk_list = list(embedded_chunks)
        if not chunk_list:
            return 0

        mapping = _token_chunk_map(token_chunks)

        # Align token chunks by id when possible, otherwise by position.
        aligned_tokens: list[Any | None] = []
        if token_chunks is not None and len(token_chunks) == len(chunk_list):
            # Prefer id-based lookup.
            if mapping and all(
                str(getattr(c, "token_chunk_id", getattr(c, "chunk_id", ""))) in mapping
                for c in chunk_list
            ):
                for c in chunk_list:
                    key = str(getattr(c, "token_chunk_id", getattr(c, "chunk_id", "")))
                    aligned_tokens.append(mapping.get(key))
            else:
                aligned_tokens = list(token_chunks)
        elif token_chunks is not None:
            raise ValueError(
                f"embedded_chunks ({len(chunk_list)}) and token_chunks "
                f"({len(token_chunks)}) must have the same length"
            )
        else:
            aligned_tokens = [None] * len(chunk_list)

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        vectors: list[np.ndarray] = []

        for embedded, token_chunk in zip(chunk_list, aligned_tokens, strict=True):
            token_chunk_id = str(
                getattr(embedded, "token_chunk_id", getattr(embedded, "chunk_id", ""))
            )
            if not token_chunk_id:
                raise ValueError("embedded chunk is missing a token_chunk_id")
            ids.append(token_chunk_id)

            vector = _as_vector(getattr(embedded, "embedding", getattr(embedded, "vector", None)))
            vectors.append(vector)

            if token_chunk is not None:
                text = getattr(token_chunk, "text", "")
            else:
                text = getattr(embedded, "text", "")
            documents.append("" if text is None else str(text))

            metadatas.append(_metadata_from(embedded, token_chunk))

        self._validate_vectors(chunk_list, vectors)

        effective_batch = batch_size if batch_size is not None else self.batch_size
        effective_batch = max(1, int(effective_batch))
        try:
            max_batch = self.client.get_max_batch_size()
            if max_batch and max_batch > 0:
                effective_batch = min(effective_batch, max_batch)
        except Exception:  # pragma: no cover - defensive
            pass

        # Chroma expects lists of floats.
        vector_lists = [v.tolist() for v in vectors]

        for start in range(0, len(ids), effective_batch):
            end = start + effective_batch
            self.collection.upsert(
                ids=ids[start:end],
                embeddings=vector_lists[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )

        return len(ids)

    # ------------------------------------------------------------------ #
    # Inspection
    # ------------------------------------------------------------------ #
    def count(self) -> int:
        return int(self.collection.count())

    def peek(self, limit: int = 10) -> Any:
        return self.collection.peek(limit=limit)

    def get(
        self,
        ids: Sequence[str] | None = None,
        limit: int | None = None,
        include: Sequence[str] | None = None,
    ) -> Any:
        if include is None:
            include_list = ["embeddings", "documents", "metadatas"]
        else:
            include_list = list(include)
        kwargs: dict[str, Any] = {"include": include_list}
        if ids is not None:
            kwargs["ids"] = list(ids)
        if limit is not None:
            kwargs["limit"] = limit
        return self.collection.get(**kwargs)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # pragma: no cover - defensive
            pass

    def __enter__(self) -> "ChromaVectorStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# Alias kept for callers that use the shorter name.
ChromaStore = ChromaVectorStore
