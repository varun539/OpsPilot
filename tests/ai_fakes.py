"""Deterministic test doubles for the AI runtime tests.

These fakes never touch a network and are never importable from production
code. They implement just enough of the SentenceTransformer, tokenizer and LLM
provider protocols to exercise the real ChromaDB store, BM25 index, hybrid
fusion, RAG service and LangGraph graph.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import numpy as np

EMBEDDING_DIMENSION = 384


class StubFastTokenizer:
    """Deterministic fast tokenizer with real character offsets."""

    is_fast = True
    supports_offsets = True

    def __init__(self, name: str = "stub-fast-tokenizer") -> None:
        self.name_or_path = name
        self._pattern = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")

    def __call__(
        self,
        text: str,
        add_special_tokens: bool = True,
        truncation: bool = False,
        return_offsets_mapping: bool = False,
        **_: object,
    ) -> dict[str, Any]:
        offsets: list[tuple[int, int]] = [
            (match.start(), match.end()) for match in self._pattern.finditer(text)
        ]
        ids = list(range(len(offsets)))
        if add_special_tokens:
            ids = [101] + [token + 1 for token in ids] + [102]
            offsets = [(0, 0)] + offsets + [(0, 0)]
        result: dict[str, Any] = {"input_ids": ids}
        if return_offsets_mapping:
            result["offset_mapping"] = offsets
        return result

    def get_vocab(self) -> dict[str, int]:
        return {"[CLS]": 101, "[SEP]": 102}


class StubEmbeddingModel:
    """Deterministic 384-dimensional normalized embedding model."""

    max_seq_length = 256

    def __init__(self) -> None:
        self.tokenizer = StubFastTokenizer()

    def get_sentence_embedding_dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def encode(self, texts: Any, **_: Any) -> np.ndarray:
        rows = [_deterministic_vector(str(text)) for text in texts]
        return np.asarray(rows, dtype=np.float32)


def _deterministic_vector(text: str) -> np.ndarray:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = np.frombuffer(digest * (EMBEDDING_DIMENSION // len(digest) + 1), dtype=np.uint8)
    vector = raw[:EMBEDDING_DIMENSION].astype(np.float64) + 1.0
    return vector / np.linalg.norm(vector)
