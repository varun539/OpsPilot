from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.app.embeddings import EmbeddedChunk
from backend.app.retrieval import (
    HybridSearchEngine,
    HybridSearchResult,
)
from backend.app.retrieval.bm25_search import BM25SearchEngine
from backend.app.retrieval.semantic_search import SemanticSearchEngine
from backend.app.vectorstore import ChromaVectorStore

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _normalized_vectors(n: int, dim: int = 4, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    arr = rng.standard_normal((n, dim)).astype(np.float64)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    return arr.astype(np.float32)


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
    tokens = []
    for i in range(n):
        tokens.append(
            SimpleNamespace(
                token_chunk_id=f"chunk-{i}",
                parent_chunk_id=f"parent-{i}",
                parent_chunk_index=i,
                doc_id=f"doc-{i % 2}",
                title=f"Title {i}",
                category="configuration" if i % 2 == 0 else "networking",
                component="api" if i % 3 == 0 else "database",
                severity="high" if i % 2 == 0 else "medium",
                source_type="synthetic",
                source_path=f"data/knowledge_base/KB-00{i}.md",
                tokenizer_name="tok",
                tokenizer_revision="rev",
                token_count=10 + i,
                max_tokens=240,
                token_overlap=0,
                source_char_start=i * 100,
                source_char_end=i * 100 + 50,
                applies_to="fastapi",
                last_updated=None,
                related_documents=[],
                extra_metadata={},
                text=f"chunk text {i}",
            )
        )
    store.upsert(embedded, token_chunks=tokens)
    return vectors


class _MockModel:
    def __init__(self, vector: np.ndarray) -> None:
        self._vector = vector

    def get_sentence_embedding_dimension(self) -> int:
        return self._vector.shape[0]

    def encode(
        self,
        texts,
        batch_size=16,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ):
        return np.tile(self._vector, (len(texts), 1))


def _make_engines(tmp_path: Path, seed: int = 7):
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    vectors = _populate(store, n=6, dim=4, seed=seed)
    mock = _MockModel(vectors[0])
    semantic = SemanticSearchEngine(store, model=mock, expected_dimension=4)
    bm25 = BM25SearchEngine(store)
    bm25.build_index()
    hybrid = HybridSearchEngine(semantic_engine=semantic, bm25_engine=bm25)
    return store, vectors, semantic, bm25, hybrid


def test_rrf_calculation(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        results = hybrid.search("chunk text 0", top_k=5)
        assert results
        for r in results:
            expected = 0.0
            if r.semantic_rank is not None:
                expected += 1.0 / (60 + r.semantic_rank)
            if r.bm25_rank is not None:
                expected += 1.0 / (60 + r.bm25_rank)
            assert abs(r.rrf_score - expected) < 1e-12
    finally:
        store.close()


def test_deduplication(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        results = hybrid.search("chunk text 0", top_k=10)
        ids = [r.chunk_id for r in results]
        assert len(ids) == len(set(ids))
    finally:
        store.close()


def test_stable_ids(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        r1 = hybrid.search("chunk text 0", top_k=3)
        r2 = hybrid.search("chunk text 0", top_k=3)
        assert [r.chunk_id for r in r1] == [r.chunk_id for r in r2]
    finally:
        store.close()


def test_ranking_order(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        results = hybrid.search("chunk text 0", top_k=5)
        scores = [r.rrf_score for r in results]
        assert scores == sorted(scores, reverse=True)
    finally:
        store.close()


def test_missing_results_in_one_source(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        # Query vector only -> BM25 skipped.
        results = hybrid.search(vectors[0].tolist(), top_k=3)
        assert len(results) > 0
        for r in results:
            assert r.bm25_rank is None
            assert r.semantic_rank is not None
    finally:
        store.close()


def test_metadata_filter_consistency(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        results = hybrid.search("chunk text 0", top_k=10, category="configuration")
        assert all(r.category == "configuration" for r in results)
    finally:
        store.close()


def test_empty_search_results(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        semantic = SemanticSearchEngine(store, expected_dimension=4)
        bm25 = BM25SearchEngine(store)
        bm25.build_index()
        hybrid = HybridSearchEngine(semantic_engine=semantic, bm25_engine=bm25)
        assert hybrid.search("anything", top_k=3) == []
    finally:
        store.close()


def test_top_k_behavior(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        for k in (1, 2, 4):
            results = hybrid.search("chunk text 0", top_k=k)
            assert len(results) <= k
    finally:
        store.close()


def test_score_preservation(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        sem_results = semantic.search("chunk text 0", top_k=5)
        sem_map = {r.chunk_id: r for r in sem_results}
        bm25_results = bm25.search("chunk text 0", top_k=5)
        bm25_map = {r.chunk_id: r for r in bm25_results}
        hybrid_results = hybrid.search("chunk text 0", top_k=5)
        for r in hybrid_results:
            if r.chunk_id in sem_map:
                assert r.semantic_similarity == sem_map[r.chunk_id].similarity_score
            if r.chunk_id in bm25_map:
                assert r.bm25_score == bm25_map[r.chunk_id].bm25_score
    finally:
        store.close()


class _FakeReranker:
    def rerank(self, query, candidates):
        # Reverse order and assign scores.
        out = list(reversed(candidates))
        for i, c in enumerate(out):
            c.reranker_score = float(len(out) - i)
        return out


def test_reranker_integration(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        hybrid.reranker = _FakeReranker()
        results = hybrid.search("chunk text 0", top_k=3, rerank=True)
        assert all(r.reranker_score is not None for r in results)
        # Scores descending.
        scores = [r.reranker_score for r in results]
        assert scores == sorted(scores, reverse=True)
    finally:
        store.close()


def test_hybrid_result_type(tmp_path: Path) -> None:
    store, vectors, semantic, bm25, hybrid = _make_engines(tmp_path)
    try:
        results = hybrid.search("chunk text 0", top_k=2)
        assert all(isinstance(r, HybridSearchResult) for r in results)
    finally:
        store.close()


def test_canonical_imports() -> None:
    from backend.app.retrieval import (
        BM25SearchEngine,
        CrossEncoderReranker,
        HybridSearchEngine,
        HybridSearchResult,
    )

    assert all(
        x is not None
        for x in (BM25SearchEngine, HybridSearchEngine, HybridSearchResult, CrossEncoderReranker)
    )
