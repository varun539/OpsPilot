"""Discovery and reading of knowledge base Markdown files.

This is the only module that touches the filesystem, which keeps every later
step easy to test with in-memory strings. It answers two questions:

* which files are documents? (:func:`discover_documents`)
* what text does one file contain? (:func:`load_document_text`)

Ordering is deterministic (sorted by file name) so ingestion produces the same
list on every run and on every machine.
"""

from __future__ import annotations

from pathlib import Path

from .models import DocumentLoadError, KnowledgeBaseError

# The documents are hand-written Markdown, but accepting the long extension costs
# nothing and avoids a surprise if one is saved as ".markdown".
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})


def discover_documents(directory: Path) -> list[Path]:
    """Return the Markdown files directly inside ``directory``, sorted by name.

    Non-Markdown files (READMEs, images, editor backups) are skipped rather than
    rejected, and sub-directories are not searched: the knowledge base is a flat
    folder, and a recursive walk would invite accidental duplicates.

    Raises:
        KnowledgeBaseError: the directory is missing, is not a directory, or
            cannot be listed (for example, a permissions problem).
    """
    directory = Path(directory)

    if not directory.exists():
        raise KnowledgeBaseError(
            directory,
            "knowledge base directory does not exist; expected data/knowledge_base/",
        )
    if not directory.is_dir():
        raise KnowledgeBaseError(directory, "knowledge base path is not a directory")

    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise KnowledgeBaseError(directory, f"cannot list directory: {exc}") from exc

    documents = [
        entry
        for entry in entries
        if entry.is_file() and entry.suffix.lower() in MARKDOWN_SUFFIXES
    ]
    documents.sort(key=lambda path: path.name)
    return documents


def load_document_text(path: Path) -> str:
    """Read one document as UTF-8 text.

    Files are read with ``encoding="utf-8"``; a stray BOM is left in the text for
    :func:`backend.app.ingestion.cleaner.strip_bom` to remove, so the raw bytes
    stay visible to the parser when it looks for the opening ``---``.

    Raises:
        DocumentLoadError: the file cannot be opened (missing, a directory, a
            permissions problem) or is not valid UTF-8.
    """
    path = Path(path)

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentLoadError(
            path,
            f"file is not valid UTF-8 (byte {exc.start}: {exc.reason})",
        ) from exc
    except OSError as exc:
        raise DocumentLoadError(path, f"cannot read file: {exc.strerror or exc}") from exc
