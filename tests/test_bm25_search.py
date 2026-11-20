from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.app.embeddings import EmbeddedChunk
from backend.app.retrieval import BM25SearchEngine
from backend.app.vectorstore import ChromaVectorStore

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _normalized_vectors(n: int, dim: int = 4, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    arr = rng.standard_normal((n, dim)).astype(np.float64)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    return arr.astype(np.float32)


def _populate(store: ChromaVectorStore, texts: list[str], metas: list[dict] | None = None) -> None:
    n = len(texts)
    vectors = _normalized_vectors(n, dim=4, seed=7)
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
    for i, text in enumerate(texts):
        from types import SimpleNamespace

        md = (metas or [{}] * n)[i]
        tokens.append(
            SimpleNamespace(
                token_chunk_id=f"chunk-{i}",
                parent_chunk_id=f"parent-{i}",
                parent_chunk_index=i,
                doc_id=md.get("doc_id", f"doc-{i % 2}"),
                title=md.get("title", f"Title {i}"),
                category=md.get("category", "configuration" if i % 2 == 0 else "networking"),
                component=md.get("component", "api"),
                severity=md.get("severity", "high"),
                source_type="synthetic",
                source_path=md.get("source_path", f"data/knowledge_base/KB-00{i}.md"),
                tokenizer_name="tok",
                tokenizer_revision="rev",
                token_count=len(text.split()),
                max_tokens=240,
                token_overlap=0,
                source_char_start=i * 10,
                source_char_end=i * 10 + len(text),
                applies_to="fastapi",
                last_updated=None,
                related_documents=[],
                extra_metadata={},
                text=text,
            )
        )
    store.upsert(embedded, token_chunks=tokens)


def test_corpus_indexing(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, ["alpha beta", "gamma delta"])
        engine = BM25SearchEngine(store)
        assert engine.build_index() == 2
        assert engine.size == 2
    finally:
        store.close()


def test_keyword_matching(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, ["the quick brown fox", "lazy dog sleeps"])
        engine = BM25SearchEngine(store)
        results = engine.search("quick fox", top_k=2)
        assert len(results) == 1
        assert results[0].chunk_id == "chunk-0"
    finally:
        store.close()


def test_technical_identifiers(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(
            store,
            ["HTTP 500 internal server error", "HTTP 429 rate limit", "asyncpg connection pool"],
        )
        engine = BM25SearchEngine(store)
        r500 = engine.search("HTTP 500", top_k=3)
        assert r500 and r500[0].chunk_id == "chunk-0"
        r429 = engine.search("HTTP 429", top_k=3)
        assert r429 and r429[0].chunk_id == "chunk-1"
        rasync = engine.search("asyncpg", top_k=3)
        assert rasync and rasync[0].chunk_id == "chunk-2"
    finally:
        store.close()


def test_error_code_matching(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, ["error code 429 too many requests", "error code 500 server error"])
        engine = BM25SearchEngine(store)
        results = engine.search("429", top_k=2)
        assert results[0].chunk_id == "chunk-0"
    finally:
        store.close()


def test_ranking(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(
            store,
            ["connection pool exhausted", "connection pool connection pool", "unrelated text"],
        )
        engine = BM25SearchEngine(store)
        results = engine.search("connection pool", top_k=3)
        assert len(results) >= 2
        scores = [r.bm25_score for r in results]
        assert scores == sorted(scores, reverse=True)
        # chunk-1 has term twice -> higher score
        assert results[0].chunk_id == "chunk-1"
    finally:
        store.close()


def test_metadata_filtering(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(
            store,
            ["HTTP 500 error", "HTTP 429 error"],
            metas=[{"doc_id": "doc-a", "category": "api"}, {"doc_id": "doc-b", "category": "api"}],
        )
        engine = BM25SearchEngine(store)
        results = engine.search("HTTP error", top_k=5, doc_id="doc-a")
        assert all(r.doc_id == "doc-a" for r in results)
    finally:
        store.close()


def test_empty_corpus(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        engine = BM25SearchEngine(store)
        assert engine.search("anything", top_k=3) == []
    finally:
        store.close()


def test_index_refresh(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma", expected_dimension=4)
    try:
        _populate(store, ["first document"])
        engine = BM25SearchEngine(store)
        engine.build_index()
        assert engine.search("second", top_k=3) == []
        # Add another chunk directly via upsert by reopening? Use store upsert with new text.
        from backend.app.embeddings import EmbeddedChunk

        vec = _normalized_vectors(1, dim=4, seed=99)[0]
        emb = [
            EmbeddedChunk(
                token_chunk_id="chunk-9", parent_chunk_id="parent-9", doc_id="doc-9", embedding=vec
            )
        ]
        from types import SimpleNamespace

        tok = SimpleNamespace(
            token_chunk_id="chunk-9",
            parent_chunk_id="parent-9",
            parent_chunk_index=9,
            doc_id="doc-9",
            title="Title 9",
            category="configuration",
            component="api",
            severity="high",
            source_type="synthetic",
            source_path="data/knowledge_base/KB-009.md",
            tokenizer_name="tok",
            tokenizer_revision="rev",
            token_count=1,
            max_tokens=240,
            token_overlap=0,
            source_char_start=0,
            source_char_end=6,
            applies_to="fastapi",
            last_updated=None,
            related_documents=[],
            extra_metadata={},
            text="second document",
        )
        store.upsert(emb, token_chunks=[tok])
        engine.refresh()
        results = engine.search("second", top_k=3)
        assert results and results[0].chunk_id == "chunk-9"
    finally:
        store.close()
