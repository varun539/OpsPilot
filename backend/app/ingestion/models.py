"""Validated data models and error types for the knowledge base ingestion pipeline.

This module is the shared foundation of the ingestion package. Everything else
(the loader, parser, cleaner and pipeline) either produces or consumes the two
models defined here:

* :class:`DocumentMetadata` -- the YAML frontmatter of one document, validated.
* :class:`KnowledgeDocument` -- one complete, cleaned document: metadata plus body.

It also defines the exception hierarchy used by the other modules, so failures
always carry the path of the offending file and a message a human can act on.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Severity vocabulary agreed for the synthetic knowledge base. Keep this in sync
# with the documents in data/knowledge_base/ rather than inventing new levels.
Severity = Literal["critical", "high", "medium", "low"]

# Every document we ingest today is synthetic (written by us, not captured from a
# real incident). Widening the pipeline to other sources must be a deliberate
# schema change, so this stays a single allowed value for now.
SourceType = Literal["synthetic"]

# Document IDs are stable citation keys (e.g. "KB-001"); they appear in RAG
# answers later, so they must follow one predictable shape.
DOC_ID_PATTERN = r"^KB-\d{3}$"

_DOC_ID_RE = re.compile(DOC_ID_PATTERN)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class IngestionError(Exception):
    """Base class for every failure raised by the ingestion pipeline.

    Subclasses only add meaning, not state: ``path`` always points at the file
    (or directory) that caused the problem, and ``message`` explains what was
    wrong with it. The rendered message always starts with the path so that a
    traceback is enough to locate the broken document.
    """

    def __init__(self, path: Path | str, message: str) -> None:
        self.path = Path(path)
        self.message = message
        super().__init__(f"{self.path}: {message}")


class KnowledgeBaseError(IngestionError):
    """The knowledge base directory is missing, is not a directory, or is unreadable."""


class DocumentLoadError(IngestionError):
    """A Markdown file exists but could not be read as UTF-8 text."""


class FrontmatterError(IngestionError):
    """The document does not have a usable YAML frontmatter block."""


class MissingFrontmatterError(FrontmatterError):
    """The opening/closing ``---`` delimiters are missing or unbalanced."""


class MalformedFrontmatterError(FrontmatterError):
    """The frontmatter block is present but is not valid YAML of the expected shape."""


class MetadataValidationError(IngestionError):
    """The frontmatter parsed as YAML but does not satisfy :class:`DocumentMetadata`."""

    def __init__(self, path: Path | str, error: ValidationError) -> None:
        self.validation_error = error
        super().__init__(path, f"invalid metadata: {_format_validation_error(error)}")


class DocumentValidationError(IngestionError):
    """The parts parsed individually but do not form a valid document (e.g. no body)."""

    def __init__(self, path: Path | str, error: ValidationError) -> None:
        self.validation_error = error
        super().__init__(path, f"invalid document: {_format_validation_error(error)}")


class DuplicateDocumentIdError(IngestionError):
    """Two documents declare the same ``id``, which would make citations ambiguous."""


def _format_validation_error(error: ValidationError) -> str:
    """Render Pydantic errors as ``field: message; field: message``.

    Pydantic's own message is multi-line and mentions the input value, which is
    noisy in a log line. The field name plus the first sentence is enough to fix
    the document.
    """
    parts = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "<frontmatter>"
        message = item.get("msg", "invalid value").split("\n")[0]
        parts.append(f"{location}: {message}")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class DocumentMetadata(BaseModel):
    """The validated YAML frontmatter of a single knowledge base document.

    Field names are snake_case Python names; three of them map onto YAML keys
    that differ (``doc_id`` <- ``id``). Unknown keys are *allowed* and kept, so
    metadata fields we add later (``owner``, ``reviewed_by``, ...) survive
    ingestion instead of being silently dropped.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    doc_id: str = Field(
        validation_alias="id",
        pattern=DOC_ID_PATTERN,
        description="Stable citation key, e.g. 'KB-001'.",
    )
    title: str = Field(min_length=1, description="Human-readable document title.")
    category: str = Field(min_length=1, description="Topic area, e.g. 'api-reliability'.")
    component: str = Field(min_length=1, description="System component the document covers.")
    severity: Severity = Field(description="Operational impact: critical, high, medium or low.")
    tags: list[str] = Field(min_length=1, description="Lower-case search keywords.")
    source_type: SourceType = Field(
        description="Provenance marker; all current docs are synthetic."
    )

    # Optional-but-standard fields. They are preserved rather than required so
    # that a minimal new document still validates, while our real documents keep
    # their full metadata for later retrieval and citation.
    applies_to: str | None = Field(default=None, description="Version applicability note.")
    last_updated: date | None = Field(default=None, description="Last review date (YYYY-MM-DD).")
    related_documents: list[str] = Field(
        default_factory=list,
        description="IDs of related documents, used to build a citation graph later.",
    )

    @field_validator("tags")
    @classmethod
    def _reject_blank_tags(cls, tags: list[str]) -> list[str]:
        """A blank tag is always a typo; reject it instead of indexing noise."""
        if any(not tag for tag in tags):
            raise ValueError("tags must not contain empty strings")
        return tags

    @field_validator("related_documents")
    @classmethod
    def _validate_related_document_ids(cls, related: list[str]) -> list[str]:
        """Cross-references must use the same ID format as documents themselves."""
        invalid = [doc_id for doc_id in related if not _DOC_ID_RE.match(doc_id)]
        if invalid:
            raise ValueError(f"related_documents must look like 'KB-001', got: {invalid}")
        return related


class KnowledgeDocument(BaseModel):
    """One complete knowledge base document, ready for later retrieval stages.

    ``content`` is the Markdown body *after* cleaning: frontmatter removed, line
    endings normalised, code blocks intact. ``source_path`` keeps attribution, so
    an answer built from this document can point back at the file it came from.
    """

    metadata: DocumentMetadata
    content: str = Field(description="Cleaned Markdown body.")
    source_path: str = Field(min_length=1, description="Path relative to the knowledge base root.")

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, content: str) -> str:
        """A document with no body carries no support knowledge, so treat it as invalid."""
        if not content.strip():
            raise ValueError(
                "document body is empty after cleaning; the document has frontmatter "
                "but no Markdown content"
            )
        return content

    @property
    def doc_id(self) -> str:
        """Convenience accessor for the citation key."""
        return self.metadata.doc_id
