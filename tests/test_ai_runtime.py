"""Tests for production AI runtime configuration and assembly."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.app.ai import (
    AIRuntimeConfig,
    AIRuntimeConfigurationError,
)
from backend.app.ai.runtime import (
    EMPTY_KNOWLEDGE_REASON,
    assemble_ai_runtime,
    disabled_ai_runtime,
)
from backend.app.embeddings import EmbeddedChunk
from backend.app.ingestion import TokenDocumentChunk
from backend.app.llm.config import LLMConfig
from backend.app.vectorstore import ChromaVectorStore
from tests.ai_fakes import StubEmbeddingModel

TEXT = (
    "FastAPI returns HTTP 500 when the database connection pool is exhausted. "
    "Check pool saturation and slow queries."
)


class StubProvider:
    model = "stub-model"

    def generate_structured(self, **_: object) -> object:  # pragma: no cover
        raise AssertionError("assembly must not call the provider")


def _token_chunk(text: str = TEXT, doc_id: str = "KB-001") -> TokenDocumentChunk:
    parent_id = f"{doc_id}::chunk::0000"
    return TokenDocumentChunk(
        token_chunk_id=f"{parent_id}::tok::0000",
        parent_chunk_id=parent_id,
        parent_chunk_index=0,
        doc_id=doc_id,
        title="Connection pool exhaustion",
        category="database",
        component="api",
        severity="high",
        tags=["database", "pool"],
        source_type="synthetic",
        source_path="KB-001.md",
        tokenizer_name="stub-fast-tokenizer",
        tokenizer_revision=None,
        token_chunk_index=0,
        text=text,
        token_count=8,
        max_tokens=240,
        token_overlap=0,
        source_char_start=0,
        source_char_end=len(text),
    )


def _seed(store: ChromaVectorStore, model: StubEmbeddingModel) -> None:
    chunk = _token_chunk()
    vector = model.encode([chunk.text])[0]
    store.upsert(
        [
            EmbeddedChunk(
                token_chunk_id=chunk.token_chunk_id,
                parent_chunk_id=chunk.parent_chunk_id,
                doc_id=chunk.doc_id,
                embedding=np.asarray(vector, dtype=np.float32),
            )
        ],
        token_chunks=[chunk],
    )


def _live_config() -> AIRuntimeConfig:
    return AIRuntimeConfig(mode="live", llm=LLMConfig(api_key="test-key"))


def test_mode_defaults_to_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSPILOT_AI_MODE", raising=False)
    config = AIRuntimeConfig.from_env()
    assert config.mode == "disabled"
    assert not config.live


@pytest.mark.parametrize("value", ["auto", "enabled", "true", "LIVE2"])
def test_unknown_modes_are_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OPSPILOT_AI_MODE", value)
    with pytest.raises(AIRuntimeConfigurationError, match="OPSPILOT_AI_MODE"):
        AIRuntimeConfig.from_env()


def test_live_mode_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPSPILOT_LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AIRuntimeConfigurationError, match="GEMINI_API_KEY"):
        assemble_ai_runtime(
            AIRuntimeConfig(
                mode="live",
                llm=LLMConfig(
                    provider="gemini",
                    model="gemini-free-test",
                    api_key=None,
                ),
            )
        )


def test_assembly_shares_one_model_store_and_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = StubEmbeddingModel()
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    _seed(store, model)
    provider = StubProvider()
    runtime = assemble_ai_runtime(
        _live_config(), provider=provider, model=model, store=store
    )
    try:
        assert runtime.ready
        assert runtime.knowledge_chunks == 1
        assert runtime.provider is provider
        assert runtime.store is store
        assert runtime.semantic_engine.model is model
        assert runtime.bm25_engine.size == 1
        assert runtime.rag_service.retrieval_engine is runtime.retrieval_engine

        hits = runtime.retrieval_engine.search("connection pool exhausted", top_k=1)
        assert len(hits) == 1
        assert hits[0].chunk_id == "KB-001::chunk::0000::tok::0000"
        assert hits[0].chunk_text == TEXT

        status = runtime.status()
        assert status["ready"] is True
        assert status["checks"]["knowledge_collection"] is True
        assert status["knowledge_chunks"] == 1
        serialized = json.dumps(status)
        assert "test-key" not in serialized
        assert str(tmp_path) not in serialized
    finally:
        runtime.close()


def test_runtime_uses_the_selected_provider_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = StubEmbeddingModel()
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    _seed(store, model)

    class SelectedProvider:
        provider_name = "gemini"
        model = "gemini-free-test"

        def generate_structured(self, **_: object) -> object:  # pragma: no cover
            raise AssertionError("assembly must not call the provider")

    selected = SelectedProvider()
    seen: list[LLMConfig] = []

    def factory(config: LLMConfig) -> SelectedProvider:
        seen.append(config)
        return selected

    monkeypatch.setattr(
        "backend.app.llm.factory.create_llm_provider", factory
    )
    runtime = assemble_ai_runtime(
        AIRuntimeConfig(
            mode="live",
            llm=LLMConfig(
                provider="gemini",
                model="gemini-free-test",
                api_key="test-key",
            ),
        ),
        model=model,
        store=store,
    )
    try:
        assert seen and seen[0].provider == "gemini"
        assert runtime.provider is selected
        assert runtime.provider_name == "gemini"
        assert runtime.status()["provider"] == "gemini"
    finally:
        runtime.close()


def test_embedding_model_is_loaded_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = StubEmbeddingModel()
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    _seed(store, model)
    calls: list[int] = []

    def fake_loader(**_: object) -> StubEmbeddingModel:
        calls.append(1)
        return model

    import backend.app.embeddings as embeddings

    monkeypatch.setattr(embeddings, "load_embedding_model", fake_loader)
    runtime = assemble_ai_runtime(
        _live_config(), provider=StubProvider(), store=store
    )
    try:
        assert len(calls) == 1
        assert runtime.embedding_model is model
        assert runtime.semantic_engine.model is model
    finally:
        runtime.close()


def test_empty_collection_is_explicitly_not_ready(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    runtime = assemble_ai_runtime(
        _live_config(), provider=StubProvider(), model=StubEmbeddingModel(), store=store
    )
    try:
        assert not runtime.ready
        assert runtime.reason == EMPTY_KNOWLEDGE_REASON
        status = runtime.status()
        assert status["mode"] == "live"
        assert status["ready"] is False
        assert status["reason"] == EMPTY_KNOWLEDGE_REASON
        assert status["checks"]["knowledge_collection"] is False
        assert "indexing" in runtime.unavailable_message()
    finally:
        runtime.close()


def test_dimension_mismatch_is_rejected(tmp_path: Path) -> None:
    store = ChromaVectorStore(persist_directory=tmp_path / "chroma")
    chunk = _token_chunk()
    vector = np.asarray([0.1, 0.2, 0.9], dtype=np.float32)
    vector = vector / np.linalg.norm(vector)
    store.upsert(
        [
            EmbeddedChunk(
                token_chunk_id=chunk.token_chunk_id,
                parent_chunk_id=chunk.parent_chunk_id,
                doc_id=chunk.doc_id,
                embedding=vector,
            )
        ],
        token_chunks=[chunk],
    )
    with pytest.raises(AIRuntimeConfigurationError, match="dimension 3"):
        assemble_ai_runtime(
            _live_config(),
            provider=StubProvider(),
            model=StubEmbeddingModel(),
            store=store,
        )
    store.close()


def test_disabled_runtime_is_safe_and_shallow() -> None:
    runtime = disabled_ai_runtime(
        AIRuntimeConfig(mode="disabled", llm=LLMConfig())
    )
    assert not runtime.ready
    status = runtime.status()
    assert status["mode"] == "disabled"
    assert status["reason"] == "ai_runtime_disabled"
    assert status["provider_configured"] is False
    assert "OPSPILOT_AI_MODE=disabled" in runtime.unavailable_message()


def test_missing_embedding_stack_has_actionable_error() -> None:
    script = """
import builtins
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] == 'sentence_transformers':
        raise ImportError('blocked for test', name=name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
from backend.app.ai import AIRuntimeConfig, AIRuntimeConfigurationError
from backend.app.ai.runtime import assemble_ai_runtime
from backend.app.llm.config import LLMConfig
try:
    assemble_ai_runtime(AIRuntimeConfig(mode='live', llm=LLMConfig(api_key='test')))
except AIRuntimeConfigurationError as exc:
    print(str(exc))
else:
    raise SystemExit('expected failure')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "requirements-embeddings.txt" in result.stdout
    assert "disabled" in result.stdout


def test_application_import_does_not_load_ml_stack() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, backend.app.main; "
            "print('torch' in sys.modules, 'chromadb' in sys.modules, "
            "'sentence_transformers' in sys.modules)",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False False False"
