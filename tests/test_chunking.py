"""Tests for the character-based document chunker.

The chunker is deterministic and pure, so these tests use two complementary
fixtures:

* the eight *real* knowledge base documents, to prove chunking works on the
  content we actually ship and preserves every citation-relevant metadata field;
* small synthetic documents built in memory, to exercise boundary conditions
  (empty input, invalid configuration, huge paragraphs, fenced code) without
  modifying the real Markdown files.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

import pytest
from pydantic import ValidationError

from backend.app.ingestion import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_KNOWLEDGE_BASE_DIR,
    DocumentChunk,
    DocumentMetadata,
    KnowledgeDocument,
    chunk_document,
    chunk_documents,
    ingest_knowledge_base,
)

# The fence scanner and overlap selector are internal, but their boundary rules
# are exactly what these regressions pin down; testing through them is more
# precise than inferring block shape from packed chunk text.
from backend.app.ingestion.chunker import _markdown_blocks, _overlap_tail

EXPECTED_DOCUMENT_COUNT = 8

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def documents() -> list[KnowledgeDocument]:
    """The eight real documents, ingested exactly as the API uses them."""
    return ingest_knowledge_base()


def make_metadata(doc_id: str = "KB-900", **overrides: object) -> DocumentMetadata:
    """Build valid metadata for a synthetic document.

    ``overrides`` may add unknown keys; ``DocumentMetadata`` keeps those in
    ``model_extra`` so the tests can verify they survive chunking.
    """
    payload: dict[str, object] = {
        "id": doc_id,
        "title": "Synthetic Chunking Document",
        "category": "testing",
        "component": "Chunker",
        "severity": "medium",
        "tags": ["chunking", "synthetic"],
        "source_type": "synthetic",
        "applies_to": "pytest",
        "last_updated": "2026-11-10",
        "related_documents": ["KB-001"],
    }
    payload.update(overrides)
    return DocumentMetadata.model_validate(payload)


def make_document(
    content: str,
    doc_id: str = "KB-900",
    **metadata_overrides: object,
) -> KnowledgeDocument:
    return KnowledgeDocument(
        metadata=make_metadata(doc_id, **metadata_overrides),
        content=content,
        source_path=f"{doc_id}-synthetic.md",
    )


def prose_paragraphs(count: int, words: int = 14) -> str:
    """Return ``count`` short, distinct paragraphs separated by blank lines."""
    paragraphs = []
    for index in range(count):
        body = " ".join(f"word{index:02d}_{word}" for word in range(words))
        paragraphs.append(f"Paragraph {index:02d}: {body}")
    return "\n\n".join(paragraphs)


def longest_shared_boundary(previous: str, following: str) -> int:
    """Length of the longest suffix of ``previous`` that prefixes ``following``."""
    for length in range(min(len(previous), len(following)), 0, -1):
        if previous.endswith(following[:length]):
            return length
    return 0


def valid_chunk_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "chunk_id": "KB-900::chunk::0000",
        "doc_id": "KB-900",
        "title": "Synthetic Chunking Document",
        "category": "testing",
        "component": "Chunker",
        "severity": "medium",
        "tags": ["chunking"],
        "source_type": "synthetic",
        "source_path": "KB-900-synthetic.md",
        "chunk_index": 0,
        "text": "A chunk of text.",
        "character_count": len("A chunk of text."),
        "applies_to": None,
        "last_updated": None,
        "related_documents": [],
        "extra_metadata": {},
    }
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------- #
# End-to-end behaviour on the real knowledge base
# --------------------------------------------------------------------------- #
def test_all_eight_real_documents_are_chunked(documents: list[KnowledgeDocument]) -> None:
    assert len(documents) == EXPECTED_DOCUMENT_COUNT

    chunks = chunk_documents(documents)

    assert chunks
    assert {chunk.doc_id for chunk in chunks} == {document.doc_id for document in documents}
    for document in documents:
        assert any(chunk.doc_id == document.doc_id for chunk in chunks)


def test_chunk_count_exceeds_document_count(documents: list[KnowledgeDocument]) -> None:
    chunks = chunk_documents(documents)
    assert len(chunks) > len(documents)


def test_chunk_ids_are_unique(documents: list[KnowledgeDocument]) -> None:
    chunk_ids = [chunk.chunk_id for chunk in chunk_documents(documents)]
    assert len(chunk_ids) == len(set(chunk_ids))


def test_chunk_ids_and_ordering_are_deterministic(documents: list[KnowledgeDocument]) -> None:
    first = chunk_documents(documents)
    second = chunk_documents(documents)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert [chunk.model_dump() for chunk in first] == [chunk.model_dump() for chunk in second]


def test_original_document_ids_are_preserved(documents: list[KnowledgeDocument]) -> None:
    chunks = chunk_documents(documents)
    original_ids = {document.doc_id for document in documents}
    assert all(chunk.doc_id in original_ids for chunk in chunks)


def test_metadata_is_preserved(documents: list[KnowledgeDocument]) -> None:
    by_id = {document.doc_id: document for document in documents}
    chunks = chunk_documents(documents)

    for chunk in chunks:
        document = by_id[chunk.doc_id]
        metadata = document.metadata
        assert chunk.title == metadata.title
        assert chunk.category == metadata.category
        assert chunk.component == metadata.component
        assert chunk.severity == metadata.severity
        assert chunk.tags == metadata.tags
        assert chunk.source_type == metadata.source_type
        assert chunk.source_path == document.source_path
        assert chunk.applies_to == metadata.applies_to
        assert chunk.last_updated == metadata.last_updated
        assert chunk.related_documents == metadata.related_documents


def test_chunk_ordering_and_indices_are_correct(documents: list[KnowledgeDocument]) -> None:
    chunks = chunk_documents(documents)
    by_id: dict[str, list[DocumentChunk]] = defaultdict(list)
    for chunk in chunks:
        by_id[chunk.doc_id].append(chunk)

    for document in documents:
        doc_chunks = by_id[document.doc_id]
        assert [chunk.chunk_index for chunk in doc_chunks] == list(range(len(doc_chunks)))
        assert [chunk.chunk_id for chunk in doc_chunks] == [
            f"{document.doc_id}::chunk::{index:04d}" for index in range(len(doc_chunks))
        ]

    # Chunks appear grouped in the same order as the input documents.
    encountered: list[str] = []
    for chunk in chunks:
        if not encountered or encountered[-1] != chunk.doc_id:
            encountered.append(chunk.doc_id)
    assert encountered == [document.doc_id for document in documents]


def test_default_chunk_size_is_respected(documents: list[KnowledgeDocument]) -> None:
    chunks = chunk_documents(documents)
    assert all(len(chunk.text) <= DEFAULT_CHUNK_SIZE for chunk in chunks)
    assert all(chunk.character_count == len(chunk.text) for chunk in chunks)


def test_default_configuration_values() -> None:
    assert DEFAULT_CHUNK_SIZE == 1000
    assert DEFAULT_CHUNK_OVERLAP == 150


def test_custom_chunk_size_produces_more_chunks(documents: list[KnowledgeDocument]) -> None:
    default_chunks = chunk_documents(documents)
    small_chunks = chunk_documents(documents, chunk_size=300, chunk_overlap=50)

    assert len(small_chunks) > len(default_chunks)
    assert all(len(chunk.text) <= 300 for chunk in small_chunks)


# --------------------------------------------------------------------------- #
# Overlap
# --------------------------------------------------------------------------- #
def test_neighbouring_chunks_overlap() -> None:
    document = make_document(prose_paragraphs(20))
    chunks = chunk_document(document, chunk_size=200, chunk_overlap=40)

    assert len(chunks) > 1
    for previous, following in zip(chunks, chunks[1:], strict=False):
        overlap = longest_shared_boundary(previous.text, following.text)
        assert 0 < overlap <= 40
        # The shared text is the literal tail of the previous chunk.
        assert previous.text.endswith(following.text[:overlap])


def test_zero_overlap_disables_context_copying() -> None:
    document = make_document(prose_paragraphs(20))
    chunks = chunk_document(document, chunk_size=200, chunk_overlap=0)

    for previous, following in zip(chunks, chunks[1:], strict=False):
        assert longest_shared_boundary(previous.text, following.text) == 0


def test_overlap_never_pushes_a_chunk_over_the_limit() -> None:
    document = make_document(prose_paragraphs(30, words=30))
    chunks = chunk_document(document, chunk_size=200, chunk_overlap=150)

    assert all(len(chunk.text) <= 200 for chunk in chunks)


def test_no_excessive_tiny_chunks() -> None:
    document = make_document(prose_paragraphs(20))
    chunks = chunk_document(document, chunk_size=200, chunk_overlap=40)

    # Greedy packing keeps all but the final chunk reasonably full.
    assert all(len(chunk.text) >= 100 for chunk in chunks[:-1])
    assert len(chunks[-1].text) > 0


# --------------------------------------------------------------------------- #
# Empty and invalid input
# --------------------------------------------------------------------------- #
def test_empty_or_whitespace_only_document_produces_no_chunks() -> None:
    document = KnowledgeDocument.model_construct(
        metadata=make_metadata(),
        content="  \n\t \n",
        source_path="KB-900-synthetic.md",
    )
    assert chunk_document(document) == []
    assert chunk_documents([document]) == []


@pytest.mark.parametrize("chunk_size", [0, -1, -1000, 1000.0, "1000", True])
def test_invalid_chunk_sizes_are_rejected(chunk_size: object) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        chunk_documents([], chunk_size=chunk_size)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [
        (100, -1),
        (100, 100),
        (100, 150),
        (100, 0.5),
        (100, True),
    ],
)
def test_invalid_overlap_values_are_rejected(
    chunk_size: int, chunk_overlap: object
) -> None:
    with pytest.raises(ValueError, match="chunk_overlap"):
        chunk_documents([], chunk_size=chunk_size, chunk_overlap=chunk_overlap)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Long blocks and Markdown structure
# --------------------------------------------------------------------------- #
def test_long_paragraph_is_split_safely() -> None:
    tokens = [f"token{index:04d}" for index in range(400)]
    document = make_document(" ".join(tokens))

    chunks = chunk_document(document, chunk_size=120, chunk_overlap=20)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 120 for chunk in chunks)
    chunk_tokens = {token for chunk in chunks for token in chunk.text.split()}
    assert set(tokens) <= chunk_tokens
    # Splits happen on whitespace, so every chunk is made of whole tokens.
    assert all(set(chunk.text.split()) <= set(tokens) for chunk in chunks)


def test_oversized_code_block_is_split_without_losing_lines() -> None:
    code_lines = [f"line_{index:03d} = {index}" for index in range(200)]
    code = "```python\n" + "\n".join(code_lines) + "\n```"
    document = make_document("# Long code\n\n" + code)

    chunks = chunk_document(document, chunk_size=200, chunk_overlap=20)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)
    for line in code_lines:
        assert any(line in chunk.text for chunk in chunks)


def test_markdown_headings_are_preserved(documents: list[KnowledgeDocument]) -> None:
    for document in documents:
        headings = [
            line.strip()
            for line in document.content.splitlines()
            if line.lstrip().startswith("## ")
        ]
        chunks = chunk_document(document)
        assert headings
        for heading in headings:
            assert any(heading in chunk.text for chunk in chunks), heading


def test_fenced_code_block_stays_intact() -> None:
    code = (
        "```python\n"
        "def handler(event):\n"
        "    return event\n"
        "\n"
        "def other():\n"
        "    pass\n"
        "```"
    )
    document = make_document("# Code Sample\n\n" + code + "\n\nAfter the code.")
    chunks = chunk_document(document)

    assert any(code in chunk.text for chunk in chunks)
    assert any("```python" in chunk.text and "def other" in chunk.text for chunk in chunks)


def test_no_source_content_is_lost(documents: list[KnowledgeDocument]) -> None:
    chunks = chunk_documents(documents, chunk_size=300, chunk_overlap=50)
    by_id: dict[str, list[DocumentChunk]] = defaultdict(list)
    for chunk in chunks:
        by_id[chunk.doc_id].append(chunk)

    for document in documents:
        doc_chunks = by_id[document.doc_id]
        joined = "\n\n".join(chunk.text for chunk in doc_chunks)
        # Every meaningful token from the body must survive chunking.
        assert set(_TOKEN_RE.findall(document.content)) <= set(_TOKEN_RE.findall(joined))
        # Fence markers are never dropped (they may be duplicated by overlap).
        assert document.content.count("```") <= joined.count("```")


# --------------------------------------------------------------------------- #
# Purity, validation and input immutability
# --------------------------------------------------------------------------- #
def test_chunking_does_not_modify_documents(documents: list[KnowledgeDocument]) -> None:
    before = [document.model_dump() for document in documents]
    chunk_documents(documents)
    assert [document.model_dump() for document in documents] == before


def test_original_markdown_files_are_unchanged(documents: list[KnowledgeDocument]) -> None:
    paths = sorted(DEFAULT_KNOWLEDGE_BASE_DIR.glob("*.md"))
    before = {path: path.read_bytes() for path in paths}

    chunk_documents(documents)

    assert {path: path.read_bytes() for path in paths} == before


def test_extra_metadata_fields_are_preserved() -> None:
    document = make_document(
        "# Synthetic\n\nSome body text.",
        owner="platform-team",
        review_cycle=30,
    )
    assert document.metadata.model_extra == {"owner": "platform-team", "review_cycle": 30}

    chunk = chunk_document(document)[0]
    assert chunk.extra_metadata == {"owner": "platform-team", "review_cycle": 30}
    assert chunk.last_updated == date(2026, 11, 10)


def test_chunk_model_rejects_incorrect_character_count() -> None:
    with pytest.raises(ValidationError, match="character_count"):
        DocumentChunk(**valid_chunk_kwargs(character_count=999))


def test_chunk_model_rejects_blank_text() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        DocumentChunk(**valid_chunk_kwargs(text="   ", character_count=3))


def test_chunk_model_rejects_negative_index() -> None:
    with pytest.raises(ValidationError):
        DocumentChunk(**valid_chunk_kwargs(chunk_index=-1, chunk_id="KB-900::chunk::-001"))


def test_chunk_model_rejects_mismatched_chunk_id() -> None:
    with pytest.raises(ValidationError, match="chunk_id"):
        DocumentChunk(**valid_chunk_kwargs(chunk_id="KB-900::chunk::0001"))


def test_chunk_model_accepts_a_valid_chunk() -> None:
    chunk = DocumentChunk(**valid_chunk_kwargs())
    assert chunk.character_count == len(chunk.text)
    assert chunk.chunk_id == "KB-900::chunk::0000"


# --------------------------------------------------------------------------- #
# M1: reliable fenced-code recognition
# --------------------------------------------------------------------------- #
def test_nested_longer_fence_example_is_one_block() -> None:
    content = (
        "````markdown\n"
        "A nested example:\n\n"
        "```python\n"
        "value = 1\n"
        "```\n"
        "````"
    )
    assert _markdown_blocks(content) == [content]


def test_short_closing_fence_does_not_close_longer_opening_fence() -> None:
    content = "`````\n```python\nvalue = 1\n```\n`````"
    assert _markdown_blocks(content) == [content]


def test_closing_fence_with_trailing_text_is_content() -> None:
    content = "```\n```python\nvalue = 1\n```"
    assert _markdown_blocks(content) == [content]


def test_tilde_fence_is_not_closed_by_backtick_fence() -> None:
    content = "~~~bash\n```python\n```\n~~~"
    assert _markdown_blocks(content) == [content]


def test_deeply_indented_fence_lookalike_does_not_close_the_fence() -> None:
    content = "```text\n    ```\ncode\n```"
    assert _markdown_blocks(content) == [content]


def test_longer_closing_fence_is_valid() -> None:
    content = "```\ncode\n`````"
    assert _markdown_blocks(content) == [content]


def test_adjacent_fence_blocks_stay_separate() -> None:
    content = "```\na\n```\n\n~~~\nb\n~~~"
    assert len(_markdown_blocks(content)) == 2


def test_nested_fence_example_is_chunked_whole() -> None:
    fence = "````markdown\nExample:\n\n```python\nvalue = 1\n```\n````"
    document = make_document("# Example\n\n" + fence + "\n\nAfter the example.")

    chunks = chunk_document(document)

    assert any(fence in chunk.text for chunk in chunks)


# --------------------------------------------------------------------------- #
# M2: overlap starts on a valid word boundary without dropping a whole word
# --------------------------------------------------------------------------- #
def test_overlap_keeps_first_word_when_cut_is_already_clean() -> None:
    assert _overlap_tail("alpha beta gamma", 10) == "beta gamma"


def test_overlap_skips_only_a_partial_word_when_cut_is_mid_word() -> None:
    assert _overlap_tail("alpha beta gamma", 9) == "gamma"


def test_overlap_returns_whole_text_when_it_fits() -> None:
    assert _overlap_tail("alpha beta", 100) == "alpha beta"


def test_overlap_zero_copies_nothing() -> None:
    assert _overlap_tail("alpha beta", 0) == ""


def test_overlap_ignores_leading_whitespace_at_a_clean_cut() -> None:
    assert _overlap_tail("alpha beta gamma", 11) == "beta gamma"


# --------------------------------------------------------------------------- #
# M3: overlap uses the space that is actually available
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [(100, 99), (120, 119), (200, 150), (100, 50), (300, 50), (100, 1)],
)
def test_boundary_overlap_configurations_preserve_invariants(
    documents: list[KnowledgeDocument], chunk_size: int, chunk_overlap: int
) -> None:
    chunks = chunk_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    assert all(len(chunk.text) <= chunk_size for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)

    by_id: dict[str, list[DocumentChunk]] = defaultdict(list)
    for chunk in chunks:
        by_id[chunk.doc_id].append(chunk)

    for document in documents:
        doc_chunks = by_id[document.doc_id]
        joined = "\n\n".join(chunk.text for chunk in doc_chunks)
        assert set(_TOKEN_RE.findall(document.content)) <= set(_TOKEN_RE.findall(joined))
        for previous, following in zip(doc_chunks, doc_chunks[1:], strict=False):
            assert longest_shared_boundary(previous.text, following.text) <= chunk_overlap


def test_extreme_overlap_is_deterministic(documents: list[KnowledgeDocument]) -> None:
    first = chunk_documents(documents, chunk_size=100, chunk_overlap=99)
    second = chunk_documents(documents, chunk_size=100, chunk_overlap=99)

    assert [chunk.model_dump() for chunk in first] == [chunk.model_dump() for chunk in second]


def test_oversized_block_reserves_room_for_overlap() -> None:
    document = make_document(" ".join(f"token{index:04d}" for index in range(400)))

    chunks = chunk_document(document, chunk_size=100, chunk_overlap=99)

    assert len(chunks) > 10
    assert all(len(chunk.text) <= 100 for chunk in chunks)
    overlaps = [
        longest_shared_boundary(previous.text, following.text)
        for previous, following in zip(chunks, chunks[1:], strict=False)
    ]
    assert overlaps
    assert all(0 < overlap <= 99 for overlap in overlaps)
    assert min(overlaps) >= 40  # measured 49 with the reserved-budget split


def test_overlap_is_reduced_at_a_structural_boundary() -> None:
    # Each block fits, but a 90-character block leaves only eight characters in
    # the next chunk. The overlap is the best word-aligned fit, not the
    # configured 99, and the limit is still respected.
    document = make_document("x" * 90 + "\n\n" + "y" * 90)

    chunks = chunk_document(document, chunk_size=100, chunk_overlap=99)

    assert all(len(chunk.text) <= 100 for chunk in chunks)
    assert longest_shared_boundary(chunks[0].text, chunks[1].text) < 99


# --------------------------------------------------------------------------- #
# M4: meaningful indentation survives block-edge trimming
# --------------------------------------------------------------------------- #
def test_nested_list_indentation_is_preserved() -> None:
    content = "Intro:\n\n  - parent label\n    - nested child\n      - leaf"
    chunks = chunk_document(make_document(content))

    assert chunks[0].text == content


def test_indented_fence_inside_a_list_is_preserved() -> None:
    fence = "   ```bash\n   curl -sS http://127.0.0.1:8000/health\n   ```"
    chunks = chunk_document(make_document("1. Step one:\n\n" + fence))

    assert any(fence in chunk.text for chunk in chunks), [chunk.text for chunk in chunks]


def test_indented_code_example_is_preserved() -> None:
    code = "    def handler():\n        return 1"
    chunks = chunk_document(make_document("Example:\n\n" + code + "\n\nAfter."))

    assert any(code in chunk.text for chunk in chunks)


def test_paragraph_continuation_indentation_is_preserved() -> None:
    content = "First line.\n  Indented continuation.\n    Deeper continuation."
    chunks = chunk_document(make_document(content))

    assert chunks[0].text == content


def test_ordinary_paragraph_does_not_gain_leading_whitespace() -> None:
    document = make_document("   A plain paragraph.\n\nSecond paragraph.")
    chunks = chunk_document(document)

    assert chunks[0].text.startswith("A plain paragraph.")
    assert not chunks[0].text.startswith((" ", "\t"))


# --------------------------------------------------------------------------- #
# M6: nested metadata is deep-copied
# --------------------------------------------------------------------------- #
def test_nested_extra_metadata_is_independent_of_source_and_chunks() -> None:
    document = make_document(
        "Paragraph one.\n\nParagraph two.",
        ownership={"team": "platform", "contacts": ["oncall@example.com"]},
        review_history=[{"reviewer": "alice", "notes": ["initial review"]}],
    )
    expected = {
        "ownership": {"team": "platform", "contacts": ["oncall@example.com"]},
        "review_history": [{"reviewer": "alice", "notes": ["initial review"]}],
    }

    chunks = chunk_document(document, chunk_size=20, chunk_overlap=0)
    assert len(chunks) == 2
    chunks[0].extra_metadata["ownership"]["contacts"].append("second@example.com")
    chunks[0].extra_metadata["ownership"]["team"] = "changed"
    chunks[0].extra_metadata["review_history"][0]["notes"].append("follow-up")

    assert document.metadata.model_extra == expected
    assert chunks[1].extra_metadata == expected


# --------------------------------------------------------------------------- #
# M7: code fragments are exact; no synthetic fence wrappers
# --------------------------------------------------------------------------- #
def test_chunks_inside_a_fence_are_exact_and_not_synthetically_wrapped() -> None:
    code_lines = [f"line_{index:03d} = {index}" for index in range(60)]
    code = "```python\n" + "\n".join(code_lines) + "\n```"
    chunks = chunk_document(make_document(code), chunk_size=200, chunk_overlap=0)

    assert len(chunks) > 2
    interior = chunks[1:-1]
    assert interior
    # Interior fragments begin and end inside the fence: no invented fences.
    assert all("```" not in chunk.text for chunk in interior)
    for line in code_lines:
        assert any(line in chunk.text for chunk in chunks)


# --------------------------------------------------------------------------- #
# M8: lead-in paragraph and its list stay together when they fit
# --------------------------------------------------------------------------- #
def test_short_lead_in_and_list_are_packed_together() -> None:
    content = "Failure families:\n\n- raised exceptions\n- response validation"
    chunks = chunk_document(make_document(content))

    assert len(chunks) == 1
    assert chunks[0].text == content
