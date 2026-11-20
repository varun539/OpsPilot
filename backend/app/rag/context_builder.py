"""Construct bounded evidence context from hybrid retrieval results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import RetrievedEvidence


@dataclass
class EvidenceContext:
    """Bounded evidence context prepared for an LLM request."""

    items: list[RetrievedEvidence]
    formatted_context: str
    total_chars: int
    truncated: bool = False
    max_chunks: int = 0
    max_context_chars: int = 0


class ContextBuilder:
    """Build stable, deduplicated evidence context."""

    def __init__(
        self,
        max_chunks: int = 5,
        max_context_chars: int = 8000,
        include_ranking: bool = True,
    ) -> None:
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        if max_context_chars < 1:
            raise ValueError("max_context_chars must be positive")
        self.max_chunks = int(max_chunks)
        self.max_context_chars = int(max_context_chars)
        self.include_ranking = bool(include_ranking)

    def build(self, results: Any) -> EvidenceContext:
        items: list[RetrievedEvidence] = []
        seen: set[str] = set()
        formatted_blocks: list[str] = []
        total_chars = 0
        truncated = False

        for result in results or []:
            chunk_id = self._chunk_id(result)
            if not chunk_id or chunk_id in seen:
                continue
            if len(items) >= self.max_chunks:
                truncated = True
                break

            item = self._to_evidence(result)
            block = self._format_item(item, len(items) + 1)
            if items and total_chars + len(block) > self.max_context_chars:
                truncated = True
                break

            # Always allow the first chunk, even if it exceeds the budget.
            items.append(item)
            seen.add(chunk_id)
            formatted_blocks.append(block)
            total_chars += len(block)

        formatted_context = self._wrap(formatted_blocks)
        return EvidenceContext(
            items=items,
            formatted_context=formatted_context,
            total_chars=len(formatted_context),
            truncated=truncated,
            max_chunks=self.max_chunks,
            max_context_chars=self.max_context_chars,
        )

    @staticmethod
    def _chunk_id(result: Any) -> str:
        if isinstance(result, RetrievedEvidence):
            return result.chunk_id
        if isinstance(result, dict):
            return str(result.get("chunk_id", "") or "")
        return str(getattr(result, "chunk_id", "") or "")

    def _to_evidence(self, result: Any) -> RetrievedEvidence:
        if isinstance(result, RetrievedEvidence):
            return result

        def _get(attr: str, key: str, default: Any = None) -> Any:
            if isinstance(result, dict):
                return result.get(key, default)
            value = getattr(result, attr, None)
            return default if value is None else value

        metadata = _get("metadata", "metadata", {})
        metadata = dict(metadata or {})
        return RetrievedEvidence(
            chunk_id=str(_get("chunk_id", "chunk_id", "")),
            doc_id=str(_get("doc_id", "doc_id", "") or ""),
            title=str(_get("title", "title", "") or ""),
            source_path=str(_get("source_path", "source_path", "") or ""),
            chunk_text=str(
                _get(
                    "chunk_text",
                    "chunk_text",
                    _get("text", "text", _get("document", "document", "")),
                )
            ),
            metadata=metadata,
            semantic_rank=_get("semantic_rank", "semantic_rank", None),
            bm25_rank=_get("bm25_rank", "bm25_rank", None),
            rrf_score=_get("rrf_score", "rrf_score", None),
            final_rank=_get("final_rank", "final_rank", None),
        )

    def _format_item(self, item: RetrievedEvidence, index: int) -> str:
        lines = [f"[EVIDENCE {index}]"]
        lines.append(f"chunk_id: {item.chunk_id}")
        if item.doc_id:
            lines.append(f"doc_id: {item.doc_id}")
        if item.title:
            lines.append(f"title: {item.title}")
        if item.source_path:
            lines.append(f"source_path: {item.source_path}")
        if self.include_ranking:
            if item.final_rank is not None:
                lines.append(f"final_rank: {item.final_rank}")
            if item.rrf_score is not None:
                lines.append(f"rrf_score: {item.rrf_score}")
        lines.append("<untrusted_content>")
        lines.append(item.chunk_text)
        lines.append("</untrusted_content>")
        return "\n".join(lines)

    @staticmethod
    def _wrap(blocks: list[str]) -> str:
        if not blocks:
            return ""
        return "\n\n".join(blocks)


def build_context(results: Any, **kwargs: Any) -> "EvidenceContext":
    """Module-level helper for building an :class:`EvidenceContext`."""
    return ContextBuilder(**kwargs).build(results)
