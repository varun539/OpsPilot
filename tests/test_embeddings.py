"""Tests for the embedding generation layer.

Unit tests use mock models so they never need a model download. A small
real-model smoke test runs only when the pinned model is already cached.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.app.embeddings import (
    EmbeddedChunk,
    EmbeddingEncoder,
    load_embedding_model,
    verify_cpu_only,
)
from backend.app.embeddings.encoder import EmbeddingValidationError, _validate_batch_size

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_CACHE = Path.home() / (
    ".cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2"
)
HAS_MODEL_CACHE = (MODEL_CACHE / "snapshots").exists() and any(
    (MODEL_CACHE / "snapshots").iterdir()
)

pytestmark = pytest.mark.filterwarnings("ignore::FutureWarning")


def make_chunks(n: int) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            token_chunk_id=f"KB-001::chunk::0000::tok::{i:04d}",
            parent_chunk_id="KB-001::chunk::0000",
            doc_id="KB-001",
            text=f"chunk text number {i}",
        )
        for i in range(n)
    ]


def normalized_vectors(n: int, dim: int = 384) -> np.ndarray:
    rng = np.random.default_rng(42)
    arr = rng.standard_normal((n, dim)).astype(np.float32)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    return arr


class MockModel:
    def __init__(self, vectors: np.ndarray) -> None:
        self._vectors = vectors
        self.encode_calls: list[dict] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self._vectors.shape[1]

    def encode(self, texts, batch_size=16, normalize_embeddings=True,
               convert_to_numpy=True, show_progress_bar=False):
        self.encode_calls.append(
            {
                "n": len(texts),
                "batch_size": batch_size,
                "normalize_embeddings": normalize_embeddings,
                "convert_to_numpy": convert_to_numpy,
                "show_progress_bar": show_progress_bar,
            }
        )
        return self._vectors.copy()


# --------------------------------------------------------------------------- #
# Unit tests with mock models
# --------------------------------------------------------------------------- #


def test_correct_vector_dimensions() -> None:
    chunks = make_chunks(4)
    vectors = normalized_vectors(4)
    model = MockModel(vectors)
    encoder = EmbeddingEncoder(model, batch_size=2)
    results = encoder.encode(chunks)
    assert all(r.embedding.shape == (384,) for r in results)


def test_correct_input_output_count() -> None:
    n = 7
    chunks = make_chunks(n)
    model = MockModel(normalized_vectors(n))
    encoder = EmbeddingEncoder(model)
    results = encoder.encode(chunks)
    assert len(results) == n


def test_ordering_preservation() -> None:
    n = 5
    chunks = make_chunks(n)
    vectors = normalized_vectors(n)
    model = MockModel(vectors)
    encoder = EmbeddingEncoder(model)
    results = encoder.encode(chunks)
    for i, result in enumerate(results):
        assert result.token_chunk_id == chunks[i].token_chunk_id


def test_chunk_id_mapping() -> None:
    chunks = make_chunks(3)
    model = MockModel(normalized_vectors(3))
    encoder = EmbeddingEncoder(model)
    results = encoder.encode(chunks)
    for chunk, result in zip(chunks, results, strict=True):
        assert result.token_chunk_id == chunk.token_chunk_id
        assert result.parent_chunk_id == chunk.parent_chunk_id
        assert result.doc_id == chunk.doc_id


def test_metadata_provenance_preservation() -> None:
    chunks = make_chunks(2)
    model = MockModel(normalized_vectors(2))
    encoder = EmbeddingEncoder(model)
    results = encoder.encode(chunks)
    for chunk, result in zip(chunks, results, strict=True):
        assert result.token_chunk_id == chunk.token_chunk_id
        assert result.parent_chunk_id == chunk.parent_chunk_id
        assert result.doc_id == chunk.doc_id


def test_nan_rejection() -> None:
    chunks = make_chunks(2)
    vectors = normalized_vectors(2)
    vectors[0, 0] = float("nan")
    model = MockModel(vectors)
    encoder = EmbeddingEncoder(model)
    with pytest.raises(EmbeddingValidationError, match="NaN"):
        encoder.encode(chunks)


def test_infinity_rejection() -> None:
    chunks = make_chunks(2)
    vectors = normalized_vectors(2)
    vectors[1, 3] = float("inf")
    model = MockModel(vectors)
    encoder = EmbeddingEncoder(model)
    with pytest.raises(EmbeddingValidationError, match="NaN"):
        encoder.encode(chunks)


def test_zero_vector_rejection() -> None:
    chunks = make_chunks(2)
    vectors = normalized_vectors(2)
    vectors[0, :] = 0.0
    model = MockModel(vectors)
    encoder = EmbeddingEncoder(model)
    with pytest.raises(EmbeddingValidationError, match="zero-length"):
        encoder.encode(chunks)


def test_empty_input() -> None:
    model = MockModel(normalized_vectors(0) if False else np.zeros((0, 384), dtype=np.float32))
    # make encode not called
    model.encode = lambda *a, **k: np.zeros((0, 384), dtype=np.float32)
    encoder = EmbeddingEncoder(model)
    results = encoder.encode([])
    assert results == []


def test_invalid_batch_size() -> None:
    with pytest.raises(ValueError):
        _validate_batch_size(0)
    with pytest.raises(ValueError):
        _validate_batch_size(-1)
    with pytest.raises(ValueError):
        _validate_batch_size(1.5)
    with pytest.raises(ValueError):
        _validate_batch_size(True)
    model = MockModel(normalized_vectors(2))
    with pytest.raises(ValueError):
        EmbeddingEncoder(model, batch_size=0)


def test_wrong_embedding_dimension() -> None:
    chunks = make_chunks(2)
    vectors = normalized_vectors(2, dim=128)
    model = MockModel(vectors)
    model.get_sentence_embedding_dimension = lambda: 384  # type: ignore[method-assign]
    encoder = EmbeddingEncoder(model)
    with pytest.raises(EmbeddingValidationError, match="dimension"):
        encoder.encode(chunks)


def test_cpu_device_configuration() -> None:
    verify_cpu_only()  # should not raise in this environment
    with pytest.raises(ValueError, match="device"):
        load_embedding_model(device="gpu")
    with pytest.raises(ValueError, match="device"):
        load_embedding_model(device="cuda")


def test_batch_size_forwarded() -> None:
    chunks = make_chunks(6)
    model = MockModel(normalized_vectors(6))
    encoder = EmbeddingEncoder(model, batch_size=2)
    encoder.encode(chunks)
    assert model.encode_calls
    assert model.encode_calls[0]["batch_size"] == 2


def test_normalize_flag_forwarded() -> None:
    chunks = make_chunks(2)
    model = MockModel(normalized_vectors(2))
    encoder = EmbeddingEncoder(model, normalize_embeddings=True)
    encoder.encode(chunks)
    assert model.encode_calls[0]["normalize_embeddings"] is True


def test_aliases_exist() -> None:
    assert EmbeddedChunk is not None
    from backend.app.embeddings import ChunkEncoder, EmbeddingResult, Encoder, TokenEmbedding

    assert EmbeddingResult is EmbeddedChunk
    assert TokenEmbedding is EmbeddedChunk
    assert Encoder is EmbeddingEncoder
    assert ChunkEncoder is EmbeddingEncoder


def test_encode_chunks_wrapper() -> None:
    from backend.app.embeddings.encoder import encode_chunks

    chunks = make_chunks(3)
    model = MockModel(normalized_vectors(3))
    results = encode_chunks(model, chunks, batch_size=2)
    assert len(results) == 3


def test_call_interface() -> None:
    chunks = make_chunks(2)
    model = MockModel(normalized_vectors(2))
    encoder = EmbeddingEncoder(model)
    results = encoder(chunks)
    assert len(results) == 2


def test_non_normalized_rejected_when_requested() -> None:
    chunks = make_chunks(2)
    vectors = normalized_vectors(2) * 2.0  # norm ~2
    model = MockModel(vectors)
    encoder = EmbeddingEncoder(model, normalize_embeddings=True)
    with pytest.raises(EmbeddingValidationError, match="not normalized"):
        encoder.encode(chunks)


def test_non_normalized_accepted_when_not_requested() -> None:
    chunks = make_chunks(2)
    vectors = normalized_vectors(2) * 2.0
    model = MockModel(vectors)
    encoder = EmbeddingEncoder(model, normalize_embeddings=False)
    results = encoder.encode(chunks)
    assert len(results) == 2


# --------------------------------------------------------------------------- #
# Real-model smoke test (cache only)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not HAS_MODEL_CACHE, reason="pinned model not cached")
def test_real_model_smoke() -> None:
    import transformers

    from backend.app.ingestion import chunk_documents, ingest_knowledge_base, token_chunk_documents

    documents = ingest_knowledge_base()
    parents = chunk_documents(documents)
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        "sentence-transformers/all-MiniLM-L6-v2",
        use_fast=True,
        local_files_only=True,
        revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    )
    token_chunks = token_chunk_documents(parents, tokenizer)

    model = load_embedding_model(device="cpu", local_files_only=True)
    assert model.get_sentence_embedding_dimension() == 384
    assert model.max_seq_length >= 240

    encoder = EmbeddingEncoder(model, batch_size=16, show_progress_bar=False)
    results = encoder.encode(token_chunks[:8])
    assert len(results) == 8
    for r in results:
        assert r.embedding.shape == (384,)
        assert r.embedding.dtype == np.float32
    vectors = np.stack([r.embedding for r in results])
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)

    # Repeated encoding consistency.
    a = np.stack([r.embedding for r in encoder.encode(token_chunks[:4])])
    b = np.stack([r.embedding for r in encoder.encode(token_chunks[:4])])
    assert float(np.max(np.abs(a - b))) < 1e-6
