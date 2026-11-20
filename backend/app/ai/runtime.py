"""Assembly and lifecycle of the production AI runtime.

One process owns exactly one embedding model instance, one persistent ChromaDB
client/collection, one BM25 index and one ``RAGService``. The existing
implementations are reused unchanged; this module only wires them together.

Heavy dependencies (PyTorch, sentence-transformers, ChromaDB) are imported
inside :func:`assemble_ai_runtime`, never at module import time, so disabled
mode stays lightweight and does not require the embedding stack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .config import AIRuntimeConfig, AIRuntimeConfigurationError

logger = logging.getLogger(__name__)

DISABLED_REASON = "ai_runtime_disabled"
EMPTY_KNOWLEDGE_REASON = "knowledge_base_empty"


@dataclass
class AIRuntime:
    """A fully assembled live runtime, or an explicit disabled marker."""

    mode: str
    provider: Any = None
    embedding_model: Any = None
    store: Any = None
    semantic_engine: Any = None
    bm25_engine: Any = None
    retrieval_engine: Any = None
    rag_service: Any = None
    knowledge_chunks: int = 0
    collection_name: str = ""
    reason: str | None = None
    embedding_model_id: str = ""
    embedding_revision: str = ""
    provider_model: str = ""
    provider_name: str = ""
    _owns_store: bool = field(default=False, repr=False)
    _owns_provider: bool = field(default=False, repr=False)

    @property
    def ready(self) -> bool:
        """Whether investigations can run against real persisted knowledge."""
        return (
            self.mode == "live"
            and self.provider is not None
            and self.retrieval_engine is not None
            and self.knowledge_chunks > 0
        )

    def unavailable_message(self) -> str:
        if self.mode != "live":
            return (
                "AI runtime is disabled (OPSPILOT_AI_MODE=disabled); "
                "investigation execution is unavailable."
            )
        if self.reason == EMPTY_KNOWLEDGE_REASON:
            return (
                "AI runtime is not ready: the knowledge base collection is empty. "
                "Run `python -m backend.app.indexing` to index the knowledge base."
            )
        return "AI runtime is not ready; see /health/ai for details."

    def status(self) -> dict[str, Any]:
        """Return a sanitized readiness snapshot safe for health endpoints.

        Deliberately excluded: API keys, filesystem paths, client objects,
        raw provider exceptions and any retrieved content.
        """
        ready = self.ready
        return {
            "mode": self.mode,
            "ready": ready,
            "reason": None if ready else (self.reason or DISABLED_REASON),
            "provider_configured": self.provider is not None,
            "provider": self.provider_name or None,
            "provider_model": self.provider_model or None,
            "embedding_model": self.embedding_model_id or None,
            "embedding_revision": self.embedding_revision or None,
            "collection": self.collection_name or None,
            "knowledge_chunks": int(self.knowledge_chunks),
            "checks": {
                "provider": self.provider is not None,
                "embedding_model": self.embedding_model is not None,
                "vector_store": self.store is not None,
                "knowledge_collection": self.knowledge_chunks > 0,
                "hybrid_retrieval": self.retrieval_engine is not None,
                "rag_service": self.rag_service is not None,
            },
        }

    def close(self) -> None:
        if self._owns_provider and self.provider is not None:
            try:
                close = getattr(self.provider, "close", None)
                if callable(close):
                    close()
            except Exception:  # pragma: no cover - defensive
                logger.warning("failed to close LLM provider", exc_info=True)
        if self._owns_store and self.store is not None:
            try:
                self.store.close()
            except Exception:  # pragma: no cover - defensive
                logger.warning("failed to close ChromaDB store", exc_info=True)


def disabled_ai_runtime(config: AIRuntimeConfig) -> AIRuntime:
    """Return the explicit disabled marker; no AI dependency is imported."""
    return AIRuntime(
        mode=config.mode,
        collection_name=config.collection_name,
        reason=DISABLED_REASON,
    )


def _validate_collection_dimension(store: Any, expected_dimension: int) -> None:
    """Reject an existing collection whose vectors cannot match the model."""
    try:
        count = int(store.count())
    except Exception as exc:
        raise AIRuntimeConfigurationError(
            f"could not read the ChromaDB collection count: {type(exc).__name__}"
        ) from exc
    if count <= 0:
        return
    try:
        sample = store.get(limit=1, include=["embeddings"])
        embeddings = sample.get("embeddings") if isinstance(sample, dict) else None
        if embeddings is None or len(embeddings) == 0:
            return
        dimension = len(embeddings[0])
    except Exception as exc:
        raise AIRuntimeConfigurationError(
            f"could not validate stored embedding dimensions: {type(exc).__name__}"
        ) from exc
    if dimension != expected_dimension:
        raise AIRuntimeConfigurationError(
            f"existing collection embeddings have dimension {dimension}, "
            f"but the pinned model produces {expected_dimension}; re-index the "
            "knowledge base into an empty OPSPILOT_CHROMA_DIR"
        )


def assemble_ai_runtime(
    config: AIRuntimeConfig,
    *,
    provider: Any = None,
    model: Any = None,
    store: Any = None,
) -> AIRuntime:
    """Construct the live provider, embeddings, retrieval and RAG services.

    Dependencies are only imported when this function runs. ``provider``,
    ``model`` and ``store`` exist for deterministic tests; production always
    calls this with configuration only.

    Raises:
        AIRuntimeConfigurationError: the runtime cannot be assembled safely.
    """
    if config.mode != "live":
        raise AIRuntimeConfigurationError("assemble_ai_runtime requires OPSPILOT_AI_MODE=live")

    # Local imports keep disabled mode free of the ML stack.
    try:
        from backend.app.embeddings import (
            EXPECTED_DIMENSION,
            MODEL_ID,
            MODEL_REVISION,
            load_embedding_model,
        )
        from backend.app.llm.factory import create_llm_provider
        from backend.app.rag import RAGService
        from backend.app.retrieval import (
            BM25SearchEngine,
            HybridSearchEngine,
            SemanticSearchEngine,
        )
        from backend.app.vectorstore import ChromaVectorStore
    except ImportError as exc:
        raise AIRuntimeConfigurationError(
            "the embedding runtime is not installed in this environment; "
            "install requirements-embeddings.txt (and CPU-only torch) or set "
            f"OPSPILOT_AI_MODE=disabled (missing module: {exc.name or 'unknown'})"
        ) from exc

    owns_store = store is None
    owns_provider = provider is None
    try:
        if provider is None:
            try:
                provider = create_llm_provider(config.llm)
            except Exception as exc:
                raise AIRuntimeConfigurationError(str(exc)) from exc
        if model is None:
            try:
                model = load_embedding_model(
                    device="cpu",
                    local_files_only=False,
                    model_id=MODEL_ID,
                    revision=MODEL_REVISION,
                )
            except Exception as exc:
                raise AIRuntimeConfigurationError(
                    "failed to load the pinned embedding model "
                    f"{MODEL_ID} (revision {MODEL_REVISION}); ensure the model "
                    "is cached in HF_HOME or the host can reach Hugging Face: "
                    f"{type(exc).__name__}"
                ) from exc
        if store is None:
            try:
                store = ChromaVectorStore(
                    persist_directory=config.chroma_directory,
                    collection_name=config.collection_name,
                    expected_dimension=EXPECTED_DIMENSION,
                )
            except Exception as exc:
                raise AIRuntimeConfigurationError(
                    "failed to initialize the persistent ChromaDB store; check "
                    "that OPSPILOT_CHROMA_DIR is writable: "
                    f"{type(exc).__name__}"
                ) from exc
    except Exception:
        if owns_provider and provider is not None:
            try:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
        if owns_store and store is not None:
            try:
                store.close()
            except Exception:
                pass
        raise

    try:
        _validate_collection_dimension(store, EXPECTED_DIMENSION)

        semantic_engine = SemanticSearchEngine(
            store=store,
            model=model,
            expected_dimension=EXPECTED_DIMENSION,
            local_files_only=False,
        )
        bm25_engine = BM25SearchEngine(store=store)
        bm25_engine.build_index()
        retrieval_engine = HybridSearchEngine(
            semantic_engine=semantic_engine,
            bm25_engine=bm25_engine,
        )
        rag_service = RAGService(retrieval_engine, provider)
        knowledge_chunks = int(store.count())
    except AIRuntimeConfigurationError:
        if owns_provider and provider is not None:
            try:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
        if owns_store:
            try:
                store.close()
            except Exception:
                pass
        raise
    except Exception as exc:
        if owns_provider and provider is not None:
            try:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
        if owns_store:
            try:
                store.close()
            except Exception:
                pass
        raise AIRuntimeConfigurationError(
            f"failed to assemble hybrid retrieval: {type(exc).__name__}"
        ) from exc

    ready = knowledge_chunks > 0
    if not ready:
        logger.warning(
            "AI runtime assembled but collection %r is empty; investigations "
            "will be refused until the knowledge base is indexed",
            config.collection_name,
        )
    else:
        logger.info(
            "AI runtime ready: provider=%s model=%s collection=%s chunks=%d",
            getattr(provider, "model", ""),
            MODEL_ID,
            config.collection_name,
            knowledge_chunks,
        )

    return AIRuntime(
        mode="live",
        provider=provider,
        embedding_model=model,
        store=store,
        semantic_engine=semantic_engine,
        bm25_engine=bm25_engine,
        retrieval_engine=retrieval_engine,
        rag_service=rag_service,
        knowledge_chunks=knowledge_chunks,
        collection_name=config.collection_name,
        reason=None if ready else EMPTY_KNOWLEDGE_REASON,
        embedding_model_id=MODEL_ID,
        embedding_revision=MODEL_REVISION,
        provider_model=str(getattr(provider, "model", "")),
        provider_name=str(
            getattr(provider, "provider_name", type(provider).__name__)
        ),
        _owns_store=owns_store,
        _owns_provider=owns_provider,
    )
