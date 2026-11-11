"""The ingestion pipeline: turn files on disk into validated documents.

``ingest_knowledge_base`` is the entry point the rest of OpsPilot will call. It
composes the other modules in a fixed order:

1. discover + read files      -> :mod:`backend.app.ingestion.loader`
2. split + validate frontmatter -> :mod:`backend.app.ingestion.parser`
3. clean the Markdown body    -> :mod:`backend.app.ingestion.cleaner`
4. build validated documents, rejecting duplicate IDs

The result is a deterministic ``list[KnowledgeDocument]`` sorted by file name,
which is the input for the later chunking and retrieval steps.

For a quick manual smoke check::

    python -c "from backend.app.ingestion import ingest_knowledge_base as kb; \
print([(d.doc_id, d.metadata.severity) for d in kb()])"
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from .cleaner import clean_document_text
from .loader import discover_documents, load_document_text
from .models import (
    DocumentValidationError,
    DuplicateDocumentIdError,
    KnowledgeDocument,
)
from .parser import parse_document

logger = logging.getLogger(__name__)

#: Repository root, used to locate the default knowledge base directory
#: (``backend/app/ingestion/pipeline.py`` -> four levels up).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "data" / "knowledge_base"


def ingest_document(path: Path, source_root: Path | None = None) -> KnowledgeDocument:
    """Load, parse, clean and validate a single Markdown document.

    Args:
        path: the Markdown file to ingest.
        source_root: directory that recorded ``source_path`` values are relative
            to. Defaults to ``path.parent`` so a single file still gets a short,
            portable attribution path.

    Raises:
        DocumentLoadError: the file cannot be read as UTF-8.
        FrontmatterError: the frontmatter is missing or not valid YAML.
        MetadataValidationError: the metadata does not match the schema.
        DocumentValidationError: the cleaned body is empty.
    """
    path = Path(path)
    root = source_root if source_root is not None else path.parent

    text = load_document_text(path)
    metadata, body = parse_document(text, path)
    content = clean_document_text(body)

    try:
        document = KnowledgeDocument(
            metadata=metadata,
            content=content,
            source_path=_relative_source_path(path, root),
        )
    except ValidationError as exc:
        raise DocumentValidationError(path, exc) from exc

    logger.debug(
        "Parsed %s (%s, %d characters of body)",
        metadata.doc_id,
        document.source_path,
        len(content),
    )
    return document


def ingest_knowledge_base(directory: Path | str | None = None) -> list[KnowledgeDocument]:
    """Ingest every Markdown document in ``directory`` and return them sorted.

    Duplicate document IDs are rejected outright: two documents sharing an ID
    would make every future citation ambiguous, and the failure is cheap to fix
    at ingestion time but expensive to debug inside a retrieval answer.

    Args:
        directory: knowledge base folder. Defaults to ``data/knowledge_base``.

    Raises:
        KnowledgeBaseError: the directory is missing or unreadable.
        DuplicateDocumentIdError: two documents declare the same ``id``.
        IngestionError: any per-document failure, carrying the offending path.
    """
    root = Path(directory) if directory is not None else DEFAULT_KNOWLEDGE_BASE_DIR

    files = discover_documents(root)
    if not files:
        # Not an error: an empty knowledge base is a valid (if useless) state.
        logger.warning("No Markdown documents found in %s", root)

    documents: list[KnowledgeDocument] = []
    seen_ids: dict[str, Path] = {}

    for path in files:
        document = ingest_document(path, source_root=root)

        previous = seen_ids.get(document.doc_id)
        if previous is not None:
            raise DuplicateDocumentIdError(
                path,
                f"duplicate document id '{document.doc_id}' (already used by {previous})",
            )
        seen_ids[document.doc_id] = path

        logger.info(
            "Ingested %s [%s] from %s",
            document.doc_id,
            document.metadata.severity,
            document.source_path,
        )
        documents.append(document)

    logger.info("Ingested %d document(s) from %s", len(documents), root)
    return documents


def _relative_source_path(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` when possible, else its absolute form.

    Relative paths keep ``source_path`` stable across machines and safe to log or
    show in a citation.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        # File lives outside the knowledge base root; keep attribution honest.
        return path.as_posix()

