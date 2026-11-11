"""YAML frontmatter extraction and metadata parsing.

Every knowledge base document starts with a frontmatter block:

```text
---
id: KB-001
title: "..."
...
---
```

This module separates that block from the Markdown body and validates it into a
:class:`~backend.app.ingestion.models.DocumentMetadata`. It performs no cleaning
of the body -- that is :mod:`backend.app.ingestion.cleaner`'s job -- so a parser
failure is always about structure or metadata, never about whitespace.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .cleaner import normalize_newlines, strip_bom
from .models import (
    DocumentMetadata,
    MalformedFrontmatterError,
    MetadataValidationError,
    MissingFrontmatterError,
)

FRONTMATTER_DELIMITER = "---"


def split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    """Split a document into ``(frontmatter, body)``.

    Line endings are normalised and a leading BOM removed *before* looking for
    delimiters, otherwise a Windows-saved file would appear to start with
    ``"---\\r\\n"`` and be reported as having no frontmatter.

    A closing delimiter is only recognised at the start of a line, so an
    indented ``---`` that is part of a YAML literal block stays in the metadata
    instead of truncating it.

    Raises:
        MissingFrontmatterError: the document does not open with ``---``, or the
            opening delimiter has no matching closing delimiter.
    """
    lines = normalize_newlines(strip_bom(text)).split("\n")

    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        raise MissingFrontmatterError(
            path,
            "document does not start with a '---' frontmatter delimiter",
        )

    # The first following '---' that *starts* a line closes the block. A '---'
    # later in the body is a Markdown horizontal rule and is left alone. An
    # indented '---' is content: inside a YAML literal block (``applies_to: |``)
    # it is part of the scalar value, so it must not end the frontmatter.
    # Trailing whitespace is tolerated (some editors leave it), leading
    # whitespace is not: indented delimiters are data, not structure.
    for index in range(1, len(lines)):
        if lines[index].rstrip() == FRONTMATTER_DELIMITER:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])

    raise MissingFrontmatterError(
        path,
        "frontmatter block is never closed with a '---' delimiter",
    )


def parse_metadata(frontmatter: str, path: Path) -> DocumentMetadata:
    """Validate a frontmatter block into :class:`DocumentMetadata`.

    ``yaml.safe_load`` is used deliberately: documents are untrusted input and
    must never be able to construct arbitrary Python objects.

    Raises:
        MalformedFrontmatterError: the block is empty, is not valid YAML, or is
            not a mapping of keys to values.
        MetadataValidationError: the YAML is fine but the metadata does not match
            the agreed schema (missing ``title``, unknown severity, and so on).
    """
    try:
        raw = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise MalformedFrontmatterError(path, f"invalid YAML in frontmatter: {exc}") from exc

    if raw is None:
        raise MalformedFrontmatterError(path, "frontmatter block is empty")
    if not isinstance(raw, dict):
        raise MalformedFrontmatterError(
            path,
            f"frontmatter must be a YAML mapping, got {type(raw).__name__}",
        )

    try:
        return DocumentMetadata.model_validate(raw)
    except ValidationError as exc:
        raise MetadataValidationError(path, exc) from exc


def parse_document(text: str, path: Path) -> tuple[DocumentMetadata, str]:
    """Parse a full document into its validated metadata and raw Markdown body.

    The body is returned exactly as written (line endings normalised), so the
    caller decides whether and how to clean it.
    """
    frontmatter, body = split_frontmatter(text, path)
    return parse_metadata(frontmatter, path), body
