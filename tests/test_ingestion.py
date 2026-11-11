"""Tests for the knowledge base ingestion pipeline.

Two kinds of test live here:

* tests against the *real* documents in ``data/knowledge_base/`` -- these prove
  the pipeline works on the content we actually ship, and that all eight
  documents stay schema-valid;
* tests against tiny synthetic documents written into ``tmp_path`` -- these prove
  that each failure mode produces the specific, readable error we promise,
  without corrupting the real knowledge base to do it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from backend.app.ingestion import (
    DEFAULT_KNOWLEDGE_BASE_DIR,
    DocumentLoadError,
    DocumentValidationError,
    DuplicateDocumentIdError,
    KnowledgeBaseError,
    MalformedFrontmatterError,
    MetadataValidationError,
    MissingFrontmatterError,
    clean_document_text,
    ingest_document,
    ingest_knowledge_base,
    parse_document,
    split_frontmatter,
)

EXPECTED_DOCUMENT_COUNT = 8

VALID_FRONTMATTER = (
    "---\n"
    "id: KB-900\n"
    'title: "Synthetic Test Document"\n'
    "category: configuration\n"
    'component: "Test component"\n'
    "severity: high\n"
    "tags:\n"
    "  - testing\n"
    "  - fixture\n"
    "source_type: synthetic\n"
    'applies_to: "Python >= 3.10"\n'
    "last_updated: 2026-11-10\n"
    "related_documents:\n"
    "  - KB-001\n"
    "---\n"
)

SIMPLE_BODY = "# Synthetic Test Document\n\nA short body used by the ingestion tests.\n"


def write_document(
    directory: Path,
    filename: str = "KB-900-synthetic-test.md",
    frontmatter: str = VALID_FRONTMATTER,
    body: str = SIMPLE_BODY,
    encoding: str = "utf-8",
) -> Path:
    """Write one test document and return its path."""
    path = directory / filename
    path.write_text(frontmatter + "\n" + body, encoding=encoding)
    return path


@pytest.fixture(scope="module")
def documents():
    """The real knowledge base, ingested once for the read-only assertions."""
    return ingest_knowledge_base()


# --------------------------------------------------------------------------- #
# The real documents
# --------------------------------------------------------------------------- #
def test_loads_all_eight_documents(documents):
    assert len(documents) == EXPECTED_DOCUMENT_COUNT


def test_document_ids_are_unique(documents):
    ids = [document.doc_id for document in documents]
    assert len(set(ids)) == len(ids) == EXPECTED_DOCUMENT_COUNT


def test_document_ids_match_filenames(documents):
    """IDs are citation keys, so the file name must start with its own ID."""
    for document in documents:
        assert Path(document.source_path).name.startswith(f"{document.doc_id}-")


def test_metadata_is_extracted_correctly(documents):
    by_id = {document.doc_id: document.metadata for document in documents}
    metadata = by_id["KB-001"]

    assert metadata.title == "FastAPI Endpoints Returning HTTP 500 Internal Server Error"
    assert metadata.category == "api-framework"
    assert "ServerErrorMiddleware" in metadata.component
    assert metadata.severity == "high"
    assert "fastapi" in metadata.tags
    assert metadata.source_type == "synthetic"
    assert metadata.applies_to is not None and metadata.applies_to.startswith("FastAPI")
    assert metadata.last_updated == date(2026, 11, 10)
    assert metadata.related_documents == ["KB-002", "KB-005"]


def test_every_document_has_metadata(documents):
    """Guard against a document silently losing fields during a refactor."""
    for document in documents:
        metadata = document.metadata
        assert metadata.doc_id.startswith("KB-")
        assert metadata.title and metadata.category and metadata.component
        assert metadata.severity in {"critical", "high", "medium", "low"}
        assert metadata.source_type == "synthetic"
        assert len(metadata.tags) >= 4
        assert metadata.last_updated is not None


def test_content_is_non_empty_and_excludes_frontmatter(documents):
    for document in documents:
        assert len(document.content) > 500
        assert not document.content.startswith("---")
        assert document.content.startswith("# ")
        assert "\n## 1. Problem Overview" in document.content


def test_frontmatter_is_valid_yaml(documents):
    """Re-read the raw files: the frontmatter itself, not just the model, is valid."""
    for document in documents:
        raw = (DEFAULT_KNOWLEDGE_BASE_DIR / document.source_path).read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(raw, DEFAULT_KNOWLEDGE_BASE_DIR)

        parsed = yaml.safe_load(frontmatter)
        assert isinstance(parsed, dict)
        assert parsed["id"] == document.doc_id
        assert parsed["source_type"] == "synthetic"
        assert body.strip()


def test_ordering_is_deterministic(documents):
    again = ingest_knowledge_base()

    assert again == documents
    paths = [document.source_path for document in documents]
    assert paths == sorted(paths)


def test_source_paths_are_relative_to_the_knowledge_base(documents):
    for document in documents:
        assert document.source_path in {
            path.name for path in DEFAULT_KNOWLEDGE_BASE_DIR.glob("*.md")
        }
        assert not document.source_path.startswith("/")


# --------------------------------------------------------------------------- #
# Isolated documents in tmp_path
# --------------------------------------------------------------------------- #
def test_single_document_ingests_from_a_directory(tmp_path):
    write_document(tmp_path)

    document = ingest_document(tmp_path / "KB-900-synthetic-test.md", source_root=tmp_path)

    assert document.doc_id == "KB-900"
    assert document.metadata.severity == "high"
    assert document.source_path == "KB-900-synthetic-test.md"
    assert document.content.startswith("# Synthetic Test Document")


def test_non_markdown_files_are_skipped(tmp_path):
    write_document(tmp_path)
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")

    documents = ingest_knowledge_base(tmp_path)

    assert [document.source_path for document in documents] == ["KB-900-synthetic-test.md"]


def test_extra_metadata_fields_are_preserved(tmp_path):
    frontmatter = VALID_FRONTMATTER.replace(
        "source_type: synthetic\n",
        "source_type: synthetic\nowner: platform-team\n",
    )
    write_document(tmp_path, frontmatter=frontmatter)

    document = ingest_knowledge_base(tmp_path)[0]

    assert document.metadata.model_extra == {"owner": "platform-team"}


def test_missing_required_metadata_is_rejected(tmp_path):
    frontmatter = VALID_FRONTMATTER.replace("severity: high\n", "")
    path = write_document(tmp_path, frontmatter=frontmatter)

    with pytest.raises(MetadataValidationError) as excinfo:
        ingest_knowledge_base(tmp_path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "severity" in message
    assert "Field required" in message


def test_invalid_severity_is_rejected(tmp_path):
    frontmatter = VALID_FRONTMATTER.replace("severity: high", "severity: urgent")
    write_document(tmp_path, frontmatter=frontmatter)

    with pytest.raises(MetadataValidationError) as excinfo:
        ingest_knowledge_base(tmp_path)

    assert "severity" in str(excinfo.value)


def test_invalid_document_id_is_rejected(tmp_path):
    frontmatter = VALID_FRONTMATTER.replace("id: KB-900", "id: doc-900")
    write_document(tmp_path, frontmatter=frontmatter)

    with pytest.raises(MetadataValidationError) as excinfo:
        ingest_knowledge_base(tmp_path)

    assert "id" in str(excinfo.value)


def test_malformed_yaml_is_rejected(tmp_path):
    frontmatter = '---\nid: KB-900\ntitle: "unterminated\n---\n'
    path = write_document(tmp_path, frontmatter=frontmatter)

    with pytest.raises(MalformedFrontmatterError) as excinfo:
        ingest_knowledge_base(tmp_path)

    assert str(path) in str(excinfo.value)
    assert "invalid YAML" in str(excinfo.value)


def test_frontmatter_that_is_not_a_mapping_is_rejected(tmp_path):
    path = write_document(tmp_path, frontmatter="---\n- KB-900\n- KB-901\n---\n")

    with pytest.raises(MalformedFrontmatterError) as excinfo:
        ingest_knowledge_base(tmp_path)

    assert "mapping" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_empty_frontmatter_is_rejected(tmp_path):
    write_document(tmp_path, frontmatter="---\n---\n")

    with pytest.raises(MalformedFrontmatterError):
        ingest_knowledge_base(tmp_path)


def test_missing_frontmatter_is_rejected(tmp_path):
    path = tmp_path / "KB-900-no-frontmatter.md"
    path.write_text(SIMPLE_BODY, encoding="utf-8")

    with pytest.raises(MissingFrontmatterError) as excinfo:
        ingest_knowledge_base(tmp_path)

    assert str(path) in str(excinfo.value)
    assert "---" in str(excinfo.value)


def test_unclosed_frontmatter_is_rejected(tmp_path):
    write_document(tmp_path, frontmatter="---\nid: KB-900\ntitle: \"No closing delimiter\"\n")

    with pytest.raises(MissingFrontmatterError) as excinfo:
        ingest_knowledge_base(tmp_path)

    assert "never closed" in str(excinfo.value)


def test_duplicate_document_ids_are_rejected(tmp_path):
    write_document(tmp_path, filename="KB-900-first.md")
    second = write_document(tmp_path, filename="KB-900-second.md")

    with pytest.raises(DuplicateDocumentIdError) as excinfo:
        ingest_knowledge_base(tmp_path)

    message = str(excinfo.value)
    assert "KB-900" in message
    assert str(second) in message
    assert "KB-900-first.md" in message


def test_empty_body_is_rejected(tmp_path):
    path = write_document(tmp_path, body="\n\n   \n")

    with pytest.raises(DocumentValidationError) as excinfo:
        ingest_document(path, source_root=tmp_path)

    assert "empty" in str(excinfo.value)


def test_missing_directory_is_reported(tmp_path):
    missing = tmp_path / "does-not-exist"

    with pytest.raises(KnowledgeBaseError) as excinfo:
        ingest_knowledge_base(missing)

    assert str(missing) in str(excinfo.value)
    assert "does not exist" in str(excinfo.value)


def test_unreadable_file_is_reported(tmp_path):
    """A directory with a .md name cannot be read as a file."""
    directory_named_like_a_document = tmp_path / "KB-903-broken.md"
    directory_named_like_a_document.mkdir()

    with pytest.raises(DocumentLoadError) as excinfo:
        ingest_document(directory_named_like_a_document)

    assert "KB-903-broken.md" in str(excinfo.value)
    assert "cannot read file" in str(excinfo.value)


def test_invalid_utf8_is_reported(tmp_path):
    path = tmp_path / "KB-904-latin1.md"
    path.write_bytes(VALID_FRONTMATTER.encode("utf-8") + b"\n# Caf\xe9\n")

    with pytest.raises(DocumentLoadError) as excinfo:
        ingest_document(path)

    assert "UTF-8" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #
def test_cleaning_preserves_code_blocks_and_indentation(tmp_path):
    body = (
        "\n\n"
        "# Example\n"
        "\n"
        "Text before the block.\n"
        "\n"
        "```python\n"
        "def handler(request):\n"
        "    if request.method == 'GET':\n"
        "        return {'ok': True}\n"
        "\n"
        "    return {}\n"
        "```\n"
        "\n"
        "Text after.\n"
        "\n\n"
    )
    write_document(tmp_path, body=body)

    content = ingest_knowledge_base(tmp_path)[0].content

    assert content == (
        "# Example\n"
        "\n"
        "Text before the block.\n"
        "\n"
        "```python\n"
        "def handler(request):\n"
        "    if request.method == 'GET':\n"
        "        return {'ok': True}\n"
        "\n"
        "    return {}\n"
        "```\n"
        "\n"
        "Text after."
    )
    assert "    if request.method == 'GET':" in content


def test_cleaning_normalises_crlf_and_strips_bom(tmp_path):
    path = tmp_path / "KB-905-windows.md"
    frontmatter = VALID_FRONTMATTER.replace("id: KB-900", "id: KB-905")
    text = frontmatter + "\n# Windows Document\n\nLine one.\nLine two.\n"
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))

    document = ingest_document(path)

    assert document.doc_id == "KB-905"
    assert "\r" not in document.content
    assert "\ufeff" not in document.content
    assert document.content == "# Windows Document\n\nLine one.\nLine two."


def test_cleaner_keeps_internal_blank_lines():
    text = "\n\n# Title\n\n\nParagraph.\n\n"

    assert clean_document_text(text) == "# Title\n\n\nParagraph."


def test_cleaner_does_not_strip_trailing_hard_break_spaces():
    """Two trailing spaces are a Markdown hard line break, not noise."""
    text = "# Title  \nNext line.\n"

    assert clean_document_text(text) == "# Title  \nNext line."


# --------------------------------------------------------------------------- #
# Frontmatter closing-delimiter detection
# --------------------------------------------------------------------------- #
LITERAL_BLOCK_FRONTMATTER = (
    "---\n"
    "id: KB-906\n"
    'title: "Indented delimiter inside a literal block"\n'
    "category: configuration\n"
    'component: "Ingestion parser"\n'
    "severity: medium\n"
    "tags:\n"
    "  - parser\n"
    "source_type: synthetic\n"
    "applies_to: |\n"
    "  Supported versions:\n"
    "  ---\n"
    "  See the compatibility matrix.\n"
    "---\n"
)

LITERAL_BLOCK_VALUE = "Supported versions:\n---\nSee the compatibility matrix."


def test_indented_delimiter_in_literal_block_does_not_close_frontmatter(tmp_path):
    """A ``---`` indented inside a YAML literal block is data, not a delimiter."""
    path = tmp_path / "KB-906-indented-delimiter.md"
    path.write_text(LITERAL_BLOCK_FRONTMATTER + "\n# Title\n\nBody.\n", encoding="utf-8")

    frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"), path)

    assert "See the compatibility matrix." in frontmatter
    assert "  ---" in frontmatter
    assert body.startswith("\n# Title")


def test_multiline_metadata_value_keeps_its_original_content(tmp_path):
    metadata, _ = parse_document(
        LITERAL_BLOCK_FRONTMATTER + "\n# Title\n\nBody.\n",
        tmp_path / "KB-906-indented-delimiter.md",
    )

    assert metadata.applies_to == LITERAL_BLOCK_VALUE


def test_unindented_delimiter_closes_frontmatter(tmp_path):
    """The real closing delimiter ends the block and is not part of the body."""
    path = tmp_path / "KB-906-indented-delimiter.md"
    path.write_text(LITERAL_BLOCK_FRONTMATTER + "\n# Title\n\nBody.\n", encoding="utf-8")

    document = ingest_document(path, source_root=tmp_path)

    assert document.doc_id == "KB-906"
    assert document.content == "# Title\n\nBody."
    assert "source_type" not in document.content
    assert not document.content.endswith("---")


def test_body_after_closing_delimiter_is_preserved(tmp_path):
    """A horizontal rule in the body is body content, never a delimiter."""
    body = "# Title\n\nBefore the rule.\n\n---\n\nAfter the rule.\n"
    path = write_document(
        tmp_path,
        filename="KB-907-body-rule.md",
        frontmatter=VALID_FRONTMATTER.replace("id: KB-900", "id: KB-907"),
        body=body,
    )

    document = ingest_document(path, source_root=tmp_path)

    assert document.content == "# Title\n\nBefore the rule.\n\n---\n\nAfter the rule."


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_delimiter_detection_survives_line_ending_normalisation(tmp_path, newline):
    """CRLF input is normalised first, so the same rules apply to both endings."""
    path = tmp_path / "KB-906-indented-delimiter.md"
    text = LITERAL_BLOCK_FRONTMATTER + "\n# Title\n\nBody.\n"
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))

    document = ingest_document(path, source_root=tmp_path)

    assert document.metadata.applies_to == LITERAL_BLOCK_VALUE
    assert document.content == "# Title\n\nBody."


def test_all_eight_knowledge_documents_still_parse_and_validate():
    """The stricter delimiter rule must not reject any real document."""
    documents = ingest_knowledge_base()

    assert len(documents) == EXPECTED_DOCUMENT_COUNT
    assert sorted(document.doc_id for document in documents) == [
        f"KB-{number:03d}" for number in range(1, EXPECTED_DOCUMENT_COUNT + 1)
    ]
    for document in documents:
        assert document.metadata.title
        assert document.content.startswith("# ")


def test_parse_document_returns_raw_body_for_deliberate_cleaning(tmp_path):
    """The parser must hand the body over untouched; cleaning is a separate step."""
    text = VALID_FRONTMATTER + "\n\n# Title\n\nBody.\n\n"

    metadata, body = parse_document(text, tmp_path / "KB-900-synthetic-test.md")

    assert metadata.doc_id == "KB-900"
    assert body == "\n\n# Title\n\nBody.\n\n"
