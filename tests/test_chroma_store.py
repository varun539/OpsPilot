"""Tests for the persistent ChromaDB storage layer.

All tests use temporary directories and synthetic vectors; no model weights are
downloaded and no external services are contacted.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.app.embeddings import EmbeddedChunk
from backend.app.vectorstore import ChromaVectorStore

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _normalized_vectors(n: int, dim: int = 4) -> np.ndarray:
    rng = np.random.default_rng(7)
    arr = rng.standard_normal((n, dim)).astype(np.float64)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    return arr.astype(np.float32)


def _embedded(n: int, dim: int = 4, prefix: str = "chunk") -> list[EmbeddedChunk]:
    vectors = _normalized_vectors(n, dim)
    return [
        EmbeddedChunk(
            token_chunk_id=f"{prefix}-{i}",
            parent_chunk_id=f"parent-{i}",
            doc_id=f"doc-{i % 2}",
            embedding=vectors[i],
        )
        for i in range(n)
    ]


def _token_chunks(n: int, prefix: str = "chunk") -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            token_chunk_id=f"{prefix}-{i}",
            parent_chunk_id=f"parent-{i}",
            parent_chunk_index=i,
            doc_id=f"doc-{i % 2}",
            title=f"Title {i}",
            category="configuration",
            component="api",
            severity="high",
            tags=["a", "b"],
            source_type="synthetic",
            source_path=f"data/knowledge_base/KB-00{i}.md",
            tokenizer_name="sentence-transformers/all-MiniLM-L6-v2",
            tokenizer_revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            token_count=10 + i,
            max_tokens=240,
            token_overlap=32,
            source_char_start=i * 100,
            source_char_end=i * 100 + 50,
            applies_to="fastapi",
            last_updated=None,
            related_documents=["KB-002"],
            extra_metadata={"foo": "bar"},
            text=f"chunk text {i}",
        )
        for i in range(n)
    ]


def test_persistent_client_initialization(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        assert (tmp_path / "chroma").exists()
        assert store.count() == 0
    finally:
        store.close()


def test_collection_creation(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", collection_name="mycoll")
    try:
        assert store.collection.name == "mycoll"
        assert store.collection.metadata.get("hnsw:space") == "cosine"
    finally:
        store.close()


def test_upsert_representative_embeddings(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        embedded = _embedded(5)
        tokens = _token_chunks(5)
        n = store.upsert(embedded, token_chunks=tokens)
        assert n == 5
        assert store.count() == 5
    finally:
        store.close()


def test_correct_vector_dimensions(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        store.upsert(_embedded(3), token_chunks=_token_chunks(3))
        got = store.get(limit=3)
        assert got["embeddings"] is not None
        for vec in got["embeddings"]:
            assert len(vec) == 4
    finally:
        store.close()


def test_metadata_preservation(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        store.upsert(_embedded(2), token_chunks=_token_chunks(2))
        got = store.get(ids=["chunk-0"])
        meta = got["metadatas"][0]
        assert meta["token_chunk_id"] == "chunk-0"
        assert meta["parent_chunk_id"] == "parent-0"
        assert meta["doc_id"] == "doc-0"
        assert meta["title"] == "Title 0"
        assert meta["category"] == "configuration"
        assert meta["component"] == "api"
        assert meta["severity"] == "high"
        assert json.loads(meta["tags"]) == ["a", "b"]
        assert meta["source_path"] == "data/knowledge_base/KB-000.md"
        assert meta["tokenizer_revision"] == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
        assert meta["token_count"] == 10
        assert meta["max_tokens"] == 240
        assert meta["token_overlap"] == 32
        assert meta["source_char_start"] == 0
        assert meta["source_char_end"] == 50
        assert json.loads(meta["related_documents"]) == ["KB-002"]
        assert json.loads(meta["extra_metadata"]) == {"foo": "bar"}
    finally:
        store.close()


def test_original_text_preservation(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        store.upsert(_embedded(2), token_chunks=_token_chunks(2))
        got = store.get(ids=["chunk-1"])
        assert got["documents"][0] == "chunk text 1"
    finally:
        store.close()


def test_stable_record_ids(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        store.upsert(_embedded(3), token_chunks=_token_chunks(3))
        got = store.get()
        assert sorted(got["ids"]) == ["chunk-0", "chunk-1", "chunk-2"]
    finally:
        store.close()


def test_repeated_upsert_no_duplicates(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        store.upsert(_embedded(4), token_chunks=_token_chunks(4))
        store.upsert(_embedded(4), token_chunks=_token_chunks(4))
        assert store.count() == 4
    finally:
        store.close()


def test_persistence_across_reopen(tmp_path: Path) -> None:
    directory = tmp_path / "chroma"
    store = ChromaVectorStore(persist_directory=directory)
    store.upsert(_embedded(6), token_chunks=_token_chunks(6))
    store.close()

    reopened = ChromaVectorStore(persist_directory=directory)
    try:
        assert reopened.count() == 6
        got = reopened.get(ids=["chunk-3"])
        assert got["documents"][0] == "chunk text 3"
    finally:
        reopened.close()


def test_invalid_vector_dimension(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        bad = _embedded(2, dim=3)
        # align token chunks count but vectors dim 3
        with pytest.raises(ValueError, match="dimension"):
            store.upsert(bad, token_chunks=_token_chunks(2))
    finally:
        store.close()


def test_nan_rejection(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        vectors = _normalized_vectors(2, 4)
        vectors[0, 0] = float("nan")
        embedded = [
            EmbeddedChunk(
                token_chunk_id="n0", parent_chunk_id="p0", doc_id="d0", embedding=vectors[0]
            ),
            EmbeddedChunk(
                token_chunk_id="n1", parent_chunk_id="p1", doc_id="d1", embedding=vectors[1]
            ),
        ]
        with pytest.raises(ValueError, match="NaN"):
            store.upsert(embedded)
    finally:
        store.close()


def test_infinity_rejection(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        vectors = _normalized_vectors(2, 4)
        vectors[1, 2] = float("inf")
        embedded = [
            EmbeddedChunk(
                token_chunk_id="i0", parent_chunk_id="p0", doc_id="d0", embedding=vectors[0]
            ),
            EmbeddedChunk(
                token_chunk_id="i1", parent_chunk_id="p1", doc_id="d1", embedding=vectors[1]
            ),
        ]
        with pytest.raises(ValueError, match="NaN"):
            store.upsert(embedded)
    finally:
        store.close()


def test_empty_input(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        assert store.upsert([]) == 0
        assert store.count() == 0
    finally:
        store.close()


def test_context_manager(tmp_path: Path) -> None:
    with ChromaVectorStore(persist_directory=tmp_path / "chroma") as store:
        store.upsert(_embedded(1), token_chunks=_token_chunks(1))
        assert store.count() == 1


def test_zero_vector_rejection(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        zero = np.zeros(4, dtype=np.float32)
        embedded = [
            EmbeddedChunk(token_chunk_id="z0", parent_chunk_id="p0", doc_id="d0", embedding=zero),
        ]
        with pytest.raises(ValueError, match="zero-length"):
            store.upsert(embedded)
    finally:
        store.close()


def test_unnormalized_rejection(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    try:
        vecs = _normalized_vectors(1, 4) * 2.0
        embedded = [
            EmbeddedChunk(
                token_chunk_id="u0", parent_chunk_id="p0", doc_id="d0", embedding=vecs[0]
            )
        ]
        with pytest.raises(ValueError, match="not normalized"):
            store.upsert(embedded)
    finally:
        store.close()