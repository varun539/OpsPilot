"""Tests for the semantic search layer.

Tests use temporary ChromaDB storage and synthetic vectors; no model weights
are downloaded and no external services are contacted. A real-model smoke test
runs only when the pinned model is already cached.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.app.embeddings import EmbeddedChunk, EmbeddingEncoder
from backend.app.retrieval import (
    SearchResult,
    SemanticSearchEngine,
)
from backend.app.vectorstore import ChromaVectorStore

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _normalized_vectors(n: int, dim: int = 4, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
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
            category="configuration" if i % 2 == 0 else "networking",
            component="api" if i % 3 == 0 else "database",
            severity="high" if i % 2 == 0 else "medium",
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


def _populate(store: ChromaVectorStore, n: int = 6, dim: int = 4, seed: int = 7) -> np.ndarray:
    vectors = _normalized_vectors(n, dim, seed=seed)
    embedded = [
        EmbeddedChunk(
            token_chunk_id=f"chunk-{i}",
            parent_chunk_id=f"parent-{i}",
            doc_id=f"doc-{i % 2}",
            embedding=vectors[i],
        )
        for i in range(n)
    ]
    tokens = _token_chunks(n)
    store.upsert(embedded, token_chunks=tokens)
    return vectors


# --------------------------------------------------------------------------- #
# 1. Query validation
# --------------------------------------------------------------------------- #


def test_query_validation_empty(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, n=3, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        with pytest.raises(ValueError):
            engine.search("")
        with pytest.raises(ValueError):
            engine.search("   ")
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 2. Successful semantic search
# --------------------------------------------------------------------------- #


def test_successful_search(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=5, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        results = engine.search(vectors[0].tolist(), top_k=3)
        assert len(results) > 0
        assert all(isinstance(r, SearchResult) for r in results)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 3. Correct result ordering
# --------------------------------------------------------------------------- #


def test_result_ordering(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=6, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        results = engine.search(vectors[0].tolist(), top_k=5)
        distances = [r.cosine_distance for r in results]
        assert distances == sorted(distances)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 4. Top-k behavior
# --------------------------------------------------------------------------- #


def test_top_k(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=6, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        for k in (1, 2, 4):
            results = engine.search(vectors[0].tolist(), top_k=k)
            assert len(results) == k
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 5. Similarity score calculation
# --------------------------------------------------------------------------- #


def test_similarity_score_calculation(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=4, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        results = engine.search(vectors[0].tolist(), top_k=2)
        for r in results:
            assert abs(r.similarity_score - (1.0 - r.cosine_distance)) < 1e-9
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 6. Cosine distance preservation
# --------------------------------------------------------------------------- #


def test_cosine_distance_preservation(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=4, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        results = engine.search(vectors[0].tolist(), top_k=2)
        for r in results:
            assert 0.0 <= r.cosine_distance <= 2.0
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 7. Metadata filtering
# --------------------------------------------------------------------------- #


def test_metadata_filtering(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=6, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        results = engine.search(vectors[0].tolist(), top_k=6, category="configuration")
        assert all(r.category == "configuration" for r in results)
        results2 = engine.search(vectors[0].tolist(), top_k=6, doc_id="doc-0")
        assert all(r.doc_id == "doc-0" for r in results2)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 8. Document text preservation
# --------------------------------------------------------------------------- #


def test_document_text_preservation(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=4, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        results = engine.search(vectors[1].tolist(), top_k=1)
        assert results[0].chunk_text == "chunk text 1"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 9. Provenance preservation
# --------------------------------------------------------------------------- #


def test_provenance_preservation(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=4, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        results = engine.search(vectors[0].tolist(), top_k=1)
        r = results[0]
        assert r.tokenizer_name == "sentence-transformers/all-MiniLM-L6-v2"
        assert r.tokenizer_revision == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
        assert r.token_count == 10
        assert r.source_char_start == 0
        assert r.source_char_end == 50
        assert r.source_path == "data/knowledge_base/KB-000.md"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 10. Empty-result handling
# --------------------------------------------------------------------------- #


def test_empty_result_handling(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        engine = SemanticSearchEngine(store, expected_dimension=4)
        # Empty collection.
        assert engine.search([1.0, 0.0, 0.0, 0.0], top_k=3) == []
        # Threshold too high.
        vectors = _populate(store, n=3, dim=4)
        engine2 = SemanticSearchEngine(store, expected_dimension=4)
        results = engine2.search(vectors[0].tolist(), top_k=3, threshold=1.01)
        assert results == []
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 11. Similarity threshold filtering
# --------------------------------------------------------------------------- #


def test_similarity_threshold_filtering(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=6, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        all_results = engine.search(vectors[0].tolist(), top_k=6)
        filtered = engine.search(vectors[0].tolist(), top_k=6, threshold=0.5)
        assert len(filtered) <= len(all_results)
        assert all(r.similarity_score >= 0.5 for r in filtered)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 12. Invalid query vectors
# --------------------------------------------------------------------------- #


def test_invalid_query_vectors(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, n=3, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        with pytest.raises(ValueError):
            engine.search([float("nan"), 0.0, 0.0, 0.0])
        with pytest.raises(ValueError):
            engine.search([float("inf"), 0.0, 0.0, 0.0])
        with pytest.raises(ValueError):
            engine.search([0.0, 0.0, 0.0, 0.0])
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 13. Dimension mismatch
# --------------------------------------------------------------------------- #


def test_dimension_mismatch(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, n=3, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        with pytest.raises(ValueError, match="dimension"):
            engine.search([1.0, 0.0, 0.0])
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 14. Non-finite vector rejection (inf already covered; unnormalized)
# --------------------------------------------------------------------------- #


def test_unnormalized_query_rejection(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, n=3, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        with pytest.raises(ValueError, match="not normalized"):
            engine.search([2.0, 0.0, 0.0, 0.0])
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 15. Repeated searches
# --------------------------------------------------------------------------- #


def test_repeated_searches(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=4, dim=4)
        engine = SemanticSearchEngine(store, expected_dimension=4)
        r1 = engine.search(vectors[0].tolist(), top_k=3)
        r2 = engine.search(vectors[0].tolist(), top_k=3)
        assert [(r.chunk_id, r.cosine_distance) for r in r1] == [
            (r.chunk_id, r.cosine_distance) for r in r2
        ]
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 16. Existing persistent collection compatibility (offline)
# --------------------------------------------------------------------------- #


def test_existing_collection_compatibility(tmp_path: Path) -> None:
    # Uses a fresh persisted store populated like Phase 2, then reopens.
    directory = tmp_path / "chroma"
    store = ChromaVectorStore(persist_directory=directory, expected_dimension=4)
    vectors = _populate(store, n=5, dim=4)
    store.close()

    reopened = ChromaVectorStore(persist_directory=directory, expected_dimension=4)
    try:
        engine = SemanticSearchEngine(reopened, expected_dimension=4)
        results = engine.search(vectors[2].tolist(), top_k=2)
        assert len(results) == 2
        assert results[0].chunk_id == "chunk-2"
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Text query embedding (mock model, no download)
# --------------------------------------------------------------------------- #


class _MockModel:
    def __init__(self, vector: np.ndarray) -> None:
        self._vector = vector

    def get_sentence_embedding_dimension(self) -> int:
        return self._vector.shape[0]

    def encode(self, texts, batch_size=16, normalize_embeddings=True,
               convert_to_numpy=True, show_progress_bar=False):
        return np.tile(self._vector, (len(texts), 1))


def test_text_query_embedding(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        vectors = _populate(store, n=4, dim=4)
        mock = _MockModel(vectors[0])
        engine = SemanticSearchEngine(store, model=mock, expected_dimension=4)
        # Mock returns same vector for any text, so search should work.
        results = engine.search("hello world", top_k=2)
        assert len(results) >= 1
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Offline real-model integration (cache only)
# --------------------------------------------------------------------------- #

MODEL_CACHE = Path.home() / ".cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2"
HAS_MODEL_CACHE = (MODEL_CACHE / "snapshots").exists() and any(
    (MODEL_CACHE / "snapshots").iterdir()
)


@pytest.mark.skipif(not HAS_MODEL_CACHE, reason="pinned model not cached")
def test_real_model_search(tmp_path: Path) -> None:
    import transformers

    from backend.app.embeddings import load_embedding_model
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
    encoder = EmbeddingEncoder(model, batch_size=16, show_progress_bar=False)
    embedded = encoder.encode(token_chunks)

    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    store.upsert(embedded, token_chunks=token_chunks)
    engine = SemanticSearchEngine(store, model=model)
    results = engine.search("Why does FastAPI return internal server error 500?", top_k=3)
    assert len(results) == 3
    assert results[0].chunk_text
    store.close()


# Canonical public API sanity.
def test_canonical_imports() -> None:
    assert SemanticSearchEngine is not None
    assert SearchResult is not None
