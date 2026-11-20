"""Deterministic offline retrieval fixture.

This is an actual semantic/BM25/hybrid retrieval run, but over an in-memory
fixture store and a lexical hashing embedding model. It exercises the production
retrieval classes without ChromaDB, PyTorch model downloads or network access.
It is not a substitute for the local real-embedding evaluation.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import numpy as np

from backend.app.embeddings import EmbeddedChunk
from backend.app.ingestion import (
    chunk_documents,
    ingest_knowledge_base,
    token_chunk_documents,
)
from backend.app.retrieval import BM25SearchEngine, HybridSearchEngine, SemanticSearchEngine

DIMENSION = 384
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class EvaluationTokenizer:
    """Small deterministic tokenizer with the tokenizer interface we need."""

    is_fast = True
    name_or_path = "evaluation-lexical-tokenizer"
    revision = "fixture-v1"

    def __call__(
        self,
        text: str,
        add_special_tokens: bool = True,
        truncation: bool = False,
        return_offsets_mapping: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        offsets = [(match.start(), match.end()) for match in _TOKEN_RE.finditer(text)]
        ids = list(range(len(offsets)))
        if add_special_tokens:
            offsets = [(0, 0), *offsets, (0, 0)]
            ids = [101, *[value + 1 for value in ids], 102]
        result: dict[str, Any] = {"input_ids": ids}
        if return_offsets_mapping:
            result["offset_mapping"] = offsets
        return result

    def get_vocab(self) -> dict[str, int]:
        return {"[CLS]": 101, "[SEP]": 102}


class LexicalEmbeddingModel:
    """Bag-of-tokens hashing model used only for offline deterministic runs."""

    max_seq_length = 256

    def __init__(self) -> None:
        self.tokenizer = EvaluationTokenizer()

    def get_sentence_embedding_dimension(self) -> int:
        return DIMENSION

    def encode(self, texts: Any, **_: Any) -> np.ndarray:
        rows = [_lexical_vector(str(text)) for text in texts]
        return np.asarray(rows, dtype=np.float32)


def _lexical_vector(text: str) -> np.ndarray:
    vector = np.zeros(DIMENSION, dtype=np.float64)
    tokens = [token.lower() for token in _TOKEN_RE.findall(text)]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSION
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector[0] = 1.0
        norm = 1.0
    return vector / norm


def _metadata_from(chunk: Any) -> dict[str, Any]:
    fields = (
        "parent_chunk_id",
        "doc_id",
        "title",
        "category",
        "component",
        "severity",
        "source_type",
        "source_path",
        "tokenizer_name",
        "tokenizer_revision",
        "token_count",
        "max_tokens",
        "token_overlap",
        "source_char_start",
        "source_char_end",
        "applies_to",
    )
    metadata = {"token_chunk_id": chunk.token_chunk_id}
    for field in fields:
        value = getattr(chunk, field, None)
        if value is not None:
            metadata[field] = value
    return metadata


class FixtureCollection:
    def __init__(self, store: "FixtureVectorStore") -> None:
        self.store = store

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: Any = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        query = np.asarray(query_embeddings[0], dtype=np.float64)
        candidates = [
            index
            for index, metadata in enumerate(self.store.metadatas)
            if _matches(metadata, where)
        ]
        scored = [
            (index, float(1.0 - np.dot(query, self.store.embeddings[index])))
            for index in candidates
        ]
        scored.sort(key=lambda item: (item[1], self.store.ids[item[0]]))
        selected = scored[: max(0, int(n_results))]
        return {
            "ids": [[self.store.ids[index] for index, _ in selected]],
            "documents": [[self.store.documents[index] for index, _ in selected]],
            "metadatas": [[self.store.metadatas[index] for index, _ in selected]],
            "distances": [[distance for _, distance in selected]],
        }


def _matches(metadata: dict[str, Any], where: Any) -> bool:
    if not where:
        return True
    if isinstance(where, dict) and "$and" in where:
        return all(_matches(metadata, item) for item in where["$and"])
    if not isinstance(where, dict):
        return True
    for key, expected in where.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            if "$eq" in expected and actual != expected["$eq"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        elif actual != expected:
            return False
    return True


class FixtureVectorStore:
    """Minimal in-memory subset of the Chroma store used by retrieval."""

    collection_name = "evaluation_fixture"
    expected_dimension = DIMENSION

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.documents: list[str] = []
        self.metadatas: list[dict[str, Any]] = []
        self.embeddings: list[np.ndarray] = []
        self.collection = FixtureCollection(self)

    def count(self) -> int:
        return len(self.ids)

    def upsert(
        self, embedded_chunks: list[EmbeddedChunk], token_chunks: list[Any] | None = None
    ) -> int:
        by_id = {chunk.token_chunk_id: chunk for chunk in token_chunks or []}
        for embedded in embedded_chunks:
            chunk = by_id.get(embedded.token_chunk_id)
            if chunk is None:
                continue
            if embedded.token_chunk_id in self.ids:
                index = self.ids.index(embedded.token_chunk_id)
            else:
                index = len(self.ids)
                self.ids.append(embedded.token_chunk_id)
                self.documents.append(str(chunk.text))
                self.metadatas.append(_metadata_from(chunk))
                self.embeddings.append(np.asarray(embedded.embedding, dtype=np.float64))
                continue
            self.documents[index] = str(chunk.text)
            self.metadatas[index] = _metadata_from(chunk)
            self.embeddings[index] = np.asarray(embedded.embedding, dtype=np.float64)
        return len(embedded_chunks)

    def get(
        self,
        ids: list[str] | None = None,
        limit: int | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        indices = list(range(len(self.ids)))
        if ids is not None:
            wanted = set(ids)
            indices = [index for index in indices if self.ids[index] in wanted]
        if limit is not None:
            indices = indices[:limit]
        return {
            "ids": [self.ids[index] for index in indices],
            "documents": [self.documents[index] for index in indices],
            "metadatas": [self.metadatas[index] for index in indices],
            "embeddings": [self.embeddings[index].tolist() for index in indices],
        }

    def close(self) -> None:
        pass


class OfflineRetrievalRuntime:
    def __init__(self, store: FixtureVectorStore, model: LexicalEmbeddingModel) -> None:
        self.store = store
        self.model = model
        self.semantic = SemanticSearchEngine(
            store, model=model, expected_dimension=DIMENSION
        )
        self.bm25 = BM25SearchEngine(store)
        self.bm25.build_index()
        self.hybrid = HybridSearchEngine(
            semantic_engine=self.semantic, bm25_engine=self.bm25
        )
        self.mode = "offline"
        self.label = "offline fixture (lexical hash embeddings; real BM25/RRF)"

    def close(self) -> None:
        self.store.close()


def build_offline_runtime(
    knowledge_base_dir: Path | str | None = None,
) -> OfflineRetrievalRuntime:
    from backend.app.ingestion import DEFAULT_KNOWLEDGE_BASE_DIR

    directory = (
        Path(knowledge_base_dir)
        if knowledge_base_dir is not None
        else DEFAULT_KNOWLEDGE_BASE_DIR
    )
    model = LexicalEmbeddingModel()
    documents = ingest_knowledge_base(directory)
    parents = chunk_documents(documents)
    chunks = token_chunk_documents(parents, model.tokenizer)
    vectors = model.encode([chunk.text for chunk in chunks])
    embedded = [
        EmbeddedChunk(
            token_chunk_id=chunk.token_chunk_id,
            parent_chunk_id=chunk.parent_chunk_id,
            doc_id=chunk.doc_id,
            embedding=np.asarray(vector, dtype=np.float64),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    store = FixtureVectorStore()
    store.upsert(embedded, token_chunks=chunks)
    return OfflineRetrievalRuntime(store, model)
