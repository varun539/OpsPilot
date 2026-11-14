"""Validate citations produced by the RAG pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import InvestigationReport


class CitationValidationError(ValueError):
    """Raised when a report cites evidence that was not supplied."""


@dataclass
class CitationValidationResult:
    """Result of citation validation."""

    valid: bool
    referenced_ids: set[str]
    unknown_ids: set[str]
    errors: list[str]


class CitationValidator:
    """Validate report chunk IDs against the current retrieved evidence set."""

    def extract_chunk_ids(self, report: InvestigationReport | Any) -> set[str]:
        ids: set[str] = set()
        for hypothesis in getattr(report, "root_cause_hypotheses", []) or []:
            ids.update(getattr(hypothesis, "supporting_chunk_ids", []) or [])
        for action in getattr(report, "recommended_actions", []) or []:
            ids.update(getattr(action, "supporting_chunk_ids", []) or [])
        for item in getattr(report, "supporting_evidence", []) or []:
            if isinstance(item, str):
                ids.add(item)
            else:
                chunk_id = getattr(item, "chunk_id", None)
                if chunk_id is not None:
                    ids.add(str(chunk_id))
        return ids

    def validate(
        self, report: InvestigationReport | Any, evidence: Any
    ) -> CitationValidationResult:
        allowed = {self._chunk_id(item) for item in (evidence or [])}
        allowed.discard("")
        referenced = self.extract_chunk_ids(report)
        unknown = referenced - allowed
        errors = [f"Unknown citation chunk ID: {chunk_id}" for chunk_id in sorted(unknown)]
        return CitationValidationResult(
            valid=not unknown,
            referenced_ids=referenced,
            unknown_ids=unknown,
            errors=errors,
        )

    def validate_or_raise(
        self, report: InvestigationReport | Any, evidence: Any
    ) -> CitationValidationResult:
        result = self.validate(report, evidence)
        if not result.valid:
            raise CitationValidationError("; ".join(result.errors))
        return result

    @staticmethod
    def _chunk_id(item: Any) -> str:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            return str(item.get("chunk_id", "") or "")
        return str(getattr(item, "chunk_id", "") or "")

