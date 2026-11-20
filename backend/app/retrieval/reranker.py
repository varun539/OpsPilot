"""Optional cross-encoder reranking for OpsPilot retrieval.

Reranking is optional: hybrid retrieval works without any reranker. The adapter
below only loads a model when explicitly constructed with a model instance or a
cached model id; it never downloads weights.
"""

from __future__ import annotations

from typing import Any, Sequence

from .hybrid_search import HybridSearchResult


class RerankerError(RuntimeError):
    """Raised when reranking cannot be performed."""


class CrossEncoderReranker:
    """Adapter around a cross-encoder-like model.

    The wrapped model must expose either ``predict(pairs)`` returning scores, or
    be callable as ``model(query, text) -> score``. No model is downloaded.
    """

    def __init__(
        self,
        model: Any = None,
        model_id: str | None = None,
        revision: str | None = None,
        local_files_only: bool = True,
        device: str = "cpu",
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.model_id = model_id
        self.revision = revision
        self.local_files_only = bool(local_files_only)
        self.device = device
        self._kwargs = kwargs

    def _ensure_model(self) -> Any:
        if self.model is not None:
            return self.model
        if not self.model_id:
            raise RerankerError("no cross-encoder model configured")
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:  # pragma: no cover - defensive
            raise RerankerError(f"CrossEncoder unavailable: {exc}") from exc
        try:
            self.model = CrossEncoder(
                self.model_id,
                revision=self.revision,
                device=self.device,
                local_files_only=self.local_files_only,
                **self._kwargs,
            )
        except Exception as exc:
            raise RerankerError(f"failed to load cross-encoder: {exc}") from exc
        return self.model

    def score(self, query: str, text: str) -> float:
        model = self._ensure_model()
        if hasattr(model, "predict"):
            try:
                scores = model.predict([(query, text)])
                return float(list(scores)[0])
            except Exception:
                pass
        if callable(model):
            return float(model(query, text))
        raise RerankerError("cross-encoder model has no predict method")

    def predict(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        model = self._ensure_model()
        if hasattr(model, "predict"):
            try:
                scores = model.predict(list(pairs))
                return [float(s) for s in scores]
            except Exception as exc:
                raise RerankerError(f"cross-encoder predict failed: {exc}") from exc
        return [self.score(q, t) for q, t in pairs]

    def rerank(
        self, query: Any, candidates: Sequence[HybridSearchResult]
    ) -> list[HybridSearchResult]:
        query_text = (
            query.text if hasattr(query, "text") and not isinstance(query, str) else str(query)
        )
        pairs = [(query_text, c.chunk_text) for c in candidates]
        scores = self.predict(pairs)
        out = list(candidates)
        for c, s in zip(out, scores, strict=True):
            c.reranker_score = float(s)
        out.sort(
            key=lambda c: (
                -(c.reranker_score if c.reranker_score is not None else float("-inf")),
                c.chunk_id,
            )
        )
        return out


# Convenience: a no-op reranker is just None. Keep an alias for clarity.
NoOpReranker = None


__all__ = [
    "CrossEncoderReranker",
    "RerankerError",
]
