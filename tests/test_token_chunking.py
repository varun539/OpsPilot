"""Tests for the token-aware chunking layer.

Two kinds of fixtures are used:

* a deterministic *fake* fast tokenizer, so unit tests can pin down boundary,
  overlap and special-token arithmetic without depending on a model download;
* the cached ``all-MiniLM-L6-v2`` tokenizer, for integration tests against the
  real eight-document knowledge base (skipped when the tokenizer is absent).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.ingestion import ingest_knowledge_base
from backend.app.ingestion.chunker import chunk_documents
from backend.app.ingestion.models import DocumentChunk
from backend.app.ingestion.token_chunker import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TOKEN_OVERLAP,
    MIN_FILL_RATIO,
    TokenDocumentChunk,
    count_tokens,
    token_chunk_document_chunk,
    token_chunk_documents,
)

REAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DOCUMENT_COUNT = 8
HEADING_RE = re.compile(r"^#{1,6}\s")
REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Fake tokenizers
# --------------------------------------------------------------------------- #
class FakeFastTokenizer:
    """Deterministic word/punctuation tokenizer with real character offsets.

    Tokens are runs of ASCII word characters, or exactly one non-space,
    non-word character. Whitespace is never part of a token, which mirrors
    WordPiece behaviour and makes paragraph/line/word gaps observable.
    """

    is_fast = True
    supports_offsets = True

    def __init__(
        self,
        name: str = "fake-fast-tokenizer",
        special_tokens: tuple[str, ...] = ("[CLS]", "[SEP]"),
    ) -> None:
        self.name_or_path = name
        self._specials = special_tokens
        self._pattern = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")

    def __call__(
        self,
        text: str,
        add_special_tokens: bool = True,
        truncation: bool = False,
        return_offsets_mapping: bool = False,
        **_: object,
    ) -> dict[str, list[int] | list[tuple[int, int]]]:
        if return_offsets_mapping and not self.supports_offsets:
            raise NotImplementedError("this tokenizer does not support offset mapping")

        ids: list[int] = []
        offsets: list[tuple[int, int]] = []
        base = len(self._specials) if add_special_tokens else 0
        for index, match in enumerate(self._pattern.finditer(text)):
            ids.append(index + base)
            offsets.append((match.start(), match.end()))

        if add_special_tokens:
            ids = list(range(len(self._specials))) + ids
            offsets = [(0, 0)] * len(self._specials) + offsets

        return {"input_ids": ids, "offset_mapping": offsets}


class FakeSlowTokenizer(FakeFastTokenizer):
    """A slow tokenizer: exposes no offset mapping, like a PEP-8 slow class."""

    is_fast = False
    supports_offsets = False


class FakeByteLevelTokenizer(FakeFastTokenizer):
    """A byte-level BPE tokenizer: each token absorbs its leading whitespace.

    This mirrors GPT-2/Llama-style offsets, where the space in front of a word
    belongs to the token that follows it. Gaps between tokens are therefore
    empty, which is exactly what the compatibility check has to catch.
    """

    is_fast = True
    supports_offsets = True

    def __init__(self) -> None:
        super().__init__(name="fake-byte-level-tokenizer")
        self._pattern = re.compile(r"\s*\S+")


class FakeRevisionTokenizer(FakeFastTokenizer):
    """A fast tokenizer that reports an immutable revision like transformers does."""

    _commit_hash = "0123456789abcdef0123456789abcdef01234567"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def make_chunk(
    text: str,
    *,
    doc_id: str = "KB-900",
    chunk_id: str | None = None,
    chunk_index: int = 0,
    **overrides: object,
) -> DocumentChunk:
    payload: dict[str, object] = {
        "chunk_id": chunk_id or f"{doc_id}::chunk::{chunk_index:04d}",
        "doc_id": doc_id,
        "title": "Synthetic Token Chunking Document",
        "category": "testing",
        "component": "TokenChunker",
        "severity": "medium",
        "tags": ["token-chunking", "synthetic"],
        "source_type": "synthetic",
        "source_path": f"{doc_id}-synthetic.md",
        "chunk_index": chunk_index,
        "text": text,
        "character_count": len(text),
        "applies_to": "pytest",
        "last_updated": "2026-11-10",
        "related_documents": ["KB-001"],
        "extra_metadata": {"nested": {"owners": ["platform"]}},
    }
    payload.update(overrides)
    return DocumentChunk.model_validate(payload)


def words(count: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{index:03d}" for index in range(count))


def token_body(tokenizer: FakeFastTokenizer, text: str) -> int:
    return len(tokenizer(text, add_special_tokens=False)["input_ids"])


def assert_slices_match_parent(parent: DocumentChunk, chunk: TokenDocumentChunk) -> None:
    assert parent.text[chunk.source_char_start : chunk.source_char_end] == chunk.text


def is_heading_only(text: str) -> bool:
    """True when every non-empty line of ``text`` is an ATX heading."""
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(HEADING_RE.match(line.strip()) for line in lines)


def ends_with_heading(text: str) -> bool:
    """True when the last non-empty line of ``text`` is an ATX heading."""
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and HEADING_RE.match(lines[-1].strip()) is not None


def requirement_lines(path: Path) -> list[str]:
    """Return the non-comment, non-blank lines of a pip requirements file."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


# --------------------------------------------------------------------------- #
# Basic behaviour
# --------------------------------------------------------------------------- #
@pytest.fixture()
def tokenizer() -> FakeFastTokenizer:
    return FakeFastTokenizer()


def test_chunk_that_fits_is_returned_unchanged(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk("A short paragraph that fits easily.")

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=8)

    assert len(result) == 1
    chunk = result[0]
    assert chunk.text == parent.text
    assert chunk.source_char_start == 0
    assert chunk.source_char_end == len(parent.text)
    assert chunk.token_chunk_index == 0
    assert chunk.token_chunk_id == "KB-900::chunk::0000::tok::0000"
    assert chunk.token_count == count_tokens(parent.text, tokenizer)


def test_oversized_chunk_is_split_within_budget(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(400))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=8)

    assert len(result) > 1
    assert all(chunk.token_count <= 40 for chunk in result)
    assert all(count_tokens(chunk.text, tokenizer) <= 40 for chunk in result)
    assert [chunk.token_chunk_index for chunk in result] == list(range(len(result)))


def test_special_tokens_are_included_in_the_budget() -> None:
    tokenizer = FakeFastTokenizer(special_tokens=("[CLS]", "[SEP]", "[PAD]", "[MASK]"))
    parent = make_chunk(words(300))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=5)

    assert all(chunk.token_count <= 40 for chunk in result)
    # Four special tokens, so at most 36 content tokens may be present.
    assert all(token_body(tokenizer, chunk.text) <= 36 for chunk in result)
    assert all(
        token_body(tokenizer, chunk.text) + 4 == chunk.token_count for chunk in result
    )


def test_budget_smaller_than_special_tokens_is_rejected() -> None:
    tokenizer = FakeFastTokenizer(special_tokens=("[CLS]", "[SEP]"))
    parent = make_chunk("Some text.")

    with pytest.raises(ValueError, match="special token"):
        token_chunk_document_chunk(parent, tokenizer, max_tokens=2, token_overlap=0)


def test_zero_overlap_disables_context_copying(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(400))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=0)

    for previous, following in zip(result, result[1:], strict=False):
        assert following.source_char_start >= previous.source_char_end


def test_overlap_is_applied_between_split_chunks(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(300))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=12)

    assert len(result) > 1
    for previous, following in zip(result, result[1:], strict=False):
        assert following.source_char_start < previous.source_char_end
        shared = parent.text[following.source_char_start : previous.source_char_end]
        assert previous.text.endswith(shared)
        assert following.text.startswith(shared)
        assert token_body(tokenizer, shared) <= 12


def test_overlap_never_pushes_a_chunk_over_the_limit(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(500, prefix="segment"))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=24, token_overlap=20)

    assert all(count_tokens(chunk.text, tokenizer) <= 24 for chunk in result)
    assert all(chunk.token_count <= 24 for chunk in result)


# --------------------------------------------------------------------------- #
# Identity, determinism and metadata
# --------------------------------------------------------------------------- #
def test_ids_are_unique_and_deterministic(tokenizer: FakeFastTokenizer) -> None:
    parents = [make_chunk(words(200), chunk_index=0), make_chunk(words(200), chunk_index=1)]

    first = token_chunk_documents(parents, tokenizer, max_tokens=40, token_overlap=8)
    second = token_chunk_documents(parents, tokenizer, max_tokens=40, token_overlap=8)

    ids = [chunk.token_chunk_id for chunk in first]
    assert len(ids) == len(set(ids))
    assert [chunk.model_dump() for chunk in first] == [chunk.model_dump() for chunk in second]


def test_parent_chunk_provenance_is_preserved(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(300), chunk_index=4, chunk_id="KB-900::chunk::0004")
    before = parent.model_dump()

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=8)

    assert all(chunk.parent_chunk_id == "KB-900::chunk::0004" for chunk in result)
    assert all(chunk.parent_chunk_index == 4 for chunk in result)
    assert [chunk.token_chunk_index for chunk in result] == list(range(len(result)))
    assert parent.model_dump() == before


def test_metadata_is_preserved_with_deep_copies(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(300), chunk_index=2)

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=8)

    for chunk in result:
        assert chunk.doc_id == parent.doc_id
        assert chunk.title == parent.title
        assert chunk.category == parent.category
        assert chunk.component == parent.component
        assert chunk.severity == parent.severity
        assert chunk.tags == parent.tags
        assert chunk.source_type == parent.source_type
        assert chunk.source_path == parent.source_path
        assert chunk.applies_to == parent.applies_to
        assert chunk.last_updated == parent.last_updated
        assert chunk.related_documents == parent.related_documents
        assert chunk.extra_metadata == parent.extra_metadata
        assert chunk.tokenizer_name == tokenizer.name_or_path
        assert_slices_match_parent(parent, chunk)

    result[0].tags.append("mutated")
    result[0].related_documents.append("KB-002")
    result[0].extra_metadata["nested"]["owners"].append("mutated")

    assert parent.tags == ["token-chunking", "synthetic"]
    assert parent.related_documents == ["KB-001"]
    assert parent.extra_metadata == {"nested": {"owners": ["platform"]}}


def test_parent_order_and_ordering_are_preserved(tokenizer: FakeFastTokenizer) -> None:
    parents = [
        make_chunk(words(250), chunk_index=0),
        make_chunk("Tiny chunk that fits.", chunk_index=1),
        make_chunk(words(250), chunk_index=2),
    ]

    result = token_chunk_documents(parents, tokenizer, max_tokens=40, token_overlap=8)

    encountered: list[str] = []
    for chunk in result:
        if not encountered or encountered[-1] != chunk.parent_chunk_id:
            encountered.append(chunk.parent_chunk_id)
    assert encountered == [parent.chunk_id for parent in parents]


# --------------------------------------------------------------------------- #
# Content preservation
# --------------------------------------------------------------------------- #
def test_empty_input_produces_no_chunks(tokenizer: FakeFastTokenizer) -> None:
    assert token_chunk_documents([], tokenizer, max_tokens=40, token_overlap=8) == []


def test_ordered_content_is_preserved(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(320))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=8)

    covered = [False] * len(parent.text)
    for chunk in result:
        for index in range(chunk.source_char_start, chunk.source_char_end):
            covered[index] = True

    for index, character in enumerate(parent.text):
        if not character.isspace():
            assert covered[index], f"character {index} was dropped"


def test_exact_reconstruction_after_removing_overlap(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(300, prefix="token"))
    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=36, token_overlap=10)

    pieces: list[str] = []
    previous_end = 0
    for chunk in result:
        overlap = max(0, previous_end - chunk.source_char_start)
        pieces.append(chunk.text[overlap:])
        previous_end = chunk.source_char_end

    reconstructed = "".join(pieces)
    assert re.sub(r"\s+", "", reconstructed) == re.sub(r"\s+", "", parent.text)


def test_unicode_is_split_without_losing_characters(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(("naïve café — 日本語のテキスト 🚀 " * 60).strip())

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=30, token_overlap=6)

    assert len(result) > 1
    assert all(chunk.token_count <= 30 for chunk in result)
    pieces: list[str] = []
    previous_end = 0
    for chunk in result:
        assert_slices_match_parent(parent, chunk)
        assert chunk.text.encode("utf-8").decode("utf-8") == chunk.text
        overlap = max(0, previous_end - chunk.source_char_start)
        pieces.append(chunk.text[overlap:])
        previous_end = chunk.source_char_end

    joined = "".join(pieces)
    assert "🚀" in joined
    assert re.sub(r"\s+", "", joined) == re.sub(r"\s+", "", parent.text)


def test_markdown_headings_are_not_separated_from_their_body() -> None:
    tokenizer = FakeFastTokenizer()
    sections = [
        f"# Heading {index}\n\n{words(20, prefix=f'section{index}_')}" for index in range(6)
    ]
    parent = make_chunk("\n\n".join(sections))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=70, token_overlap=0)

    assert len(result) > 1
    for chunk in result:
        assert not is_heading_only(chunk.text), chunk.text[:60]
        # No chunk may close on a heading: the heading belongs with the text
        # that follows it, which lives in the next chunk.
        assert not ends_with_heading(chunk.text), chunk.text[-60:]
        assert_slices_match_parent(parent, chunk)


def test_fenced_code_lines_are_kept_whole() -> None:
    tokenizer = FakeFastTokenizer()
    code_lines = [f"value_{index:03d} = compute({index})" for index in range(60)]
    code = "```python\n" + "\n".join(code_lines) + "\n```"
    parent = make_chunk("## Example\n\n" + code)

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=0)

    source_lines = parent.text.splitlines()
    assert len(result) > 1
    for chunk in result:
        for line in chunk.text.splitlines():
            assert line in source_lines, f"line split mid-way: {line!r}"


def test_long_identifiers_and_urls_are_split_at_token_boundaries() -> None:
    tokenizer = FakeFastTokenizer()
    identifier = "some_extremely_long_identifier_" + "_".join(
        f"segment{index:02d}" for index in range(30)
    )
    url = "https://example.com/api/v1/resources?filter=" + "&".join(
        f"key{index}=value{index}" for index in range(12)
    )
    parent = make_chunk(f"{identifier}\n\n{url}")

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=24, token_overlap=0)

    assert len(result) > 1
    assert all(chunk.token_count <= 24 for chunk in result)
    assert re.sub(r"\s+", "", "".join(chunk.text for chunk in result)) == re.sub(
        r"\s+", "", parent.text
    )


# --------------------------------------------------------------------------- #
# Invalid configuration and tokenizers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("max_tokens", [0, -1, 10.0, "10", True])
def test_invalid_max_tokens_is_rejected(max_tokens: object, tokenizer: FakeFastTokenizer) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        token_chunk_documents([], tokenizer, max_tokens=max_tokens)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("max_tokens", "token_overlap"),
    [(40, -1), (40, 40), (40, 80), (40, 1.5), (40, True)],
)
def test_invalid_overlap_is_rejected(
    max_tokens: int, token_overlap: object, tokenizer: FakeFastTokenizer
) -> None:
    with pytest.raises(ValueError, match="token_overlap"):
        token_chunk_documents([], tokenizer, max_tokens=max_tokens, token_overlap=token_overlap)  # type: ignore[arg-type]


def test_slow_tokenizer_is_rejected() -> None:
    parent = make_chunk("Some text that would otherwise be chunked.")

    with pytest.raises(ValueError, match="fast tokenizer"):
        token_chunk_document_chunk(parent, FakeSlowTokenizer())


def test_tokenizer_without_offset_mapping_is_rejected() -> None:
    tokenizer = FakeFastTokenizer()
    tokenizer.supports_offsets = False
    parent = make_chunk("Some text that would otherwise be chunked.")

    with pytest.raises(ValueError, match="return_offsets_mapping"):
        token_chunk_document_chunk(parent, tokenizer)


# --------------------------------------------------------------------------- #
# Integration with the cached real tokenizer
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real_tokenizer():
    transformers = pytest.importorskip("transformers")
    try:
        return transformers.AutoTokenizer.from_pretrained(
            REAL_MODEL, use_fast=True, local_files_only=True
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cached tokenizer {REAL_MODEL} unavailable: {exc}")


def test_real_tokenizer_is_fast(real_tokenizer: object) -> None:
    assert getattr(real_tokenizer, "is_fast", False) is True


def test_real_corpus_respects_the_token_budget(real_tokenizer: object) -> None:
    documents = ingest_knowledge_base()
    assert len(documents) == EXPECTED_DOCUMENT_COUNT
    parents = chunk_documents(documents)

    result = token_chunk_documents(parents, real_tokenizer)

    assert len(result) > len(parents)
    assert all(chunk.token_count <= DEFAULT_MAX_TOKENS for chunk in result)
    assert all(count_tokens(chunk.text, real_tokenizer) <= DEFAULT_MAX_TOKENS for chunk in result)
    assert all(chunk.token_count == count_tokens(chunk.text, real_tokenizer) for chunk in result)


def test_heading_followed_by_a_long_paragraph_keeps_body_with_the_heading() -> None:
    tokenizer = FakeFastTokenizer()
    parent = make_chunk("## Symptoms\n\n" + words(400))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=8)

    assert len(result) > 1
    first = result[0]
    heading_tokens = token_body(tokenizer, "## Symptoms")
    assert first.text.startswith("## Symptoms")
    assert "word000" in first.text, "heading chunk contains no body content"
    assert token_body(tokenizer, first.text) > heading_tokens
    assert all(not is_heading_only(chunk.text) for chunk in result)
    # The heading is not duplicated into the next chunk as a fragment.
    assert all("## Symptoms" not in chunk.text for chunk in result[1:])


def test_heading_with_several_sections_never_produces_a_heading_only_chunk() -> None:
    tokenizer = FakeFastTokenizer()
    body = "\n\n".join(
        f"## Section {index}\n\n{words(120, prefix=f'sec{index}_')}" for index in range(5)
    )
    parent = make_chunk("# Title\n\n" + body)

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=60, token_overlap=10)

    assert len(result) > 3
    assert all(not is_heading_only(chunk.text) for chunk in result)
    assert all(chunk.token_count <= 60 for chunk in result)


def test_parent_that_ends_on_a_heading_keeps_the_heading_text() -> None:
    tokenizer = FakeFastTokenizer()
    parent = make_chunk(words(200) + "\n\n## 7. Prevention Strategies")

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=8)

    assert any("## 7. Prevention Strategies" in chunk.text for chunk in result)
    assert not any(is_heading_only(chunk.text) for chunk in result[:-1])
    previous_end = 0
    for chunk in result:
        assert_slices_match_parent(parent, chunk)
        previous_end = chunk.source_char_end
    assert previous_end == len(parent.text)


# --------------------------------------------------------------------------- #
# Fill policy
# --------------------------------------------------------------------------- #
def test_paragraph_break_below_the_preferred_region_is_skipped(
    tokenizer: FakeFastTokenizer,
) -> None:
    # 38 content tokens available; the break after 10 tokens fills only 26%, so
    # the chunk is filled up to the window instead of closing on that break.
    parent = make_chunk(words(10) + "\n\n" + words(200, prefix="body"))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=0)

    assert token_body(tokenizer, result[0].text) == 38
    # It filled through the break into the second paragraph instead of ending
    # on the short first one (10 + 28 = 38 content tokens).
    assert result[0].text.endswith("body027")


def test_paragraph_break_inside_the_preferred_region_is_used(
    tokenizer: FakeFastTokenizer,
) -> None:
    parent = make_chunk(words(30) + "\n\n" + words(200, prefix="body"))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=0)

    assert result[0].text == words(30)
    assert result[1].text.startswith("body000")


def test_paragraph_preferred_over_line_break_in_the_preferred_region(
    tokenizer: FakeFastTokenizer,
) -> None:
    # Line break at 32 tokens (fills 84%, inside the region) and a paragraph
    # break at 28 tokens (74%, also inside): the paragraph break wins.
    text = words(28) + "\n\n" + words(4, prefix="line")
    parent = make_chunk(text + "\n" + words(200, prefix="after"))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=0)

    assert result[0].text == words(28)


@pytest.mark.parametrize(("max_tokens", "token_overlap"), [(40, 0), (40, 8), (70, 16), (100, 24)])
def test_split_chunks_fill_at_least_the_minimum_ratio(
    max_tokens: int, token_overlap: int, tokenizer: FakeFastTokenizer
) -> None:
    parent = make_chunk(
        "\n\n".join(words(40, prefix=f"para{index}_") for index in range(6))
    )

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens, token_overlap)

    assert len(result) > 1
    floor = math.ceil(max_tokens * MIN_FILL_RATIO)
    assert all(chunk.token_count >= floor for chunk in result[:-1])
    assert all(chunk.token_count <= max_tokens for chunk in result)


# --------------------------------------------------------------------------- #
# Configuration provenance
# --------------------------------------------------------------------------- #
def test_chunk_records_the_configuration_used(tokenizer: FakeFastTokenizer) -> None:
    parent = make_chunk(words(300))

    result = token_chunk_document_chunk(parent, tokenizer, max_tokens=40, token_overlap=12)

    assert {(chunk.max_tokens, chunk.token_overlap) for chunk in result} == {(40, 12)}
    assert all(chunk.token_count <= chunk.max_tokens for chunk in result)


def test_budget_fields_are_validated(tokenizer: FakeFastTokenizer) -> None:
    chunk = token_chunk_document_chunk(make_chunk(words(60)), tokenizer, 40, 8)[0]
    payload = chunk.model_dump()

    over_budget = dict(payload, token_count=41)
    with pytest.raises(ValidationError, match="token_count"):
        TokenDocumentChunk.model_validate(over_budget)

    bad_overlap = dict(payload, token_overlap=40)
    with pytest.raises(ValidationError, match="token_overlap"):
        TokenDocumentChunk.model_validate(bad_overlap)

    negative_overlap = dict(payload, token_overlap=-1)
    with pytest.raises(ValidationError):
        TokenDocumentChunk.model_validate(negative_overlap)

    zero_budget = dict(payload, max_tokens=0)
    with pytest.raises(ValidationError):
        TokenDocumentChunk.model_validate(zero_budget)


# --------------------------------------------------------------------------- #
# Tokenizer identity and offset compatibility
# --------------------------------------------------------------------------- #
def test_tokenizer_revision_is_recorded_when_available() -> None:
    tokenizer = FakeRevisionTokenizer()

    chunk = token_chunk_document_chunk(make_chunk("A short paragraph."), tokenizer)[0]

    assert chunk.tokenizer_revision == FakeRevisionTokenizer._commit_hash
    assert chunk.tokenizer_name == tokenizer.name_or_path
    assert chunk.tokenizer_name != chunk.tokenizer_revision


def test_tokenizer_revision_is_unknown_without_metadata(tokenizer: FakeFastTokenizer) -> None:
    chunk = token_chunk_document_chunk(make_chunk("A short paragraph."), tokenizer)[0]

    assert chunk.tokenizer_revision is None


def test_tokenizer_revision_is_recovered_from_a_cache_snapshot_path() -> None:
    revision = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    tokenizer = FakeFastTokenizer()
    tokenizer.vocab_file = (
        "/home/user/.cache/huggingface/hub/"
        f"models--sentence-transformers--all-MiniLM-L6-v2/snapshots/{revision}/vocab.txt"
    )

    chunk = token_chunk_document_chunk(make_chunk("A short paragraph."), tokenizer)[0]

    assert chunk.tokenizer_revision == revision


def test_byte_level_offset_semantics_are_rejected() -> None:
    parent = make_chunk("Some text that would otherwise be chunked.")

    with pytest.raises(ValueError, match="offset semantics"):
        token_chunk_document_chunk(parent, FakeByteLevelTokenizer())


# --------------------------------------------------------------------------- #
# Dependency constraints
# --------------------------------------------------------------------------- #
def test_constraints_pin_the_cpu_torch_build() -> None:
    lines = requirement_lines(REPO_ROOT / "constraints-embeddings-cpu.txt")

    assert lines == ["torch==2.15.0+cpu"]


def test_embedding_requirements_pull_in_the_cpu_constraints() -> None:
    lines = requirement_lines(REPO_ROOT / "requirements-embeddings.txt")

    assert "-c constraints-embeddings-cpu.txt" in lines
    assert "sentence-transformers==6.3.0" in lines
    assert "transformers==5.22.2" in lines
    assert "tokenizers==0.23.3" in lines
    # torch stays a constraint, never a direct requirement: a plain `torch`
    # requirement would let pip pick the default (CUDA) wheel.
    assert not any(line.startswith("torch") for line in lines)


def test_real_corpus_preserves_ids_and_provenance(real_tokenizer: object) -> None:
    parents = chunk_documents(ingest_knowledge_base())
    by_id = {parent.chunk_id: parent for parent in parents}

    result = token_chunk_documents(parents, real_tokenizer)

    assert len({chunk.doc_id for chunk in result}) == EXPECTED_DOCUMENT_COUNT
    assert len({chunk.token_chunk_id for chunk in result}) == len(result)
    assert {chunk.parent_chunk_id for chunk in result} == set(by_id)
    for chunk in result:
        parent = by_id[chunk.parent_chunk_id]
        assert parent.text[chunk.source_char_start : chunk.source_char_end] == chunk.text
        assert chunk.doc_id == parent.doc_id
        assert chunk.parent_chunk_index == parent.chunk_index
        assert (chunk.max_tokens, chunk.token_overlap) == (
            DEFAULT_MAX_TOKENS,
            DEFAULT_TOKEN_OVERLAP,
        )


def test_real_corpus_never_produces_a_heading_only_chunk(real_tokenizer: object) -> None:
    parents = chunk_documents(ingest_knowledge_base())
    by_id = {parent.chunk_id: parent for parent in parents}

    result = token_chunk_documents(parents, real_tokenizer)

    assert not [chunk for chunk in result if is_heading_only(chunk.text)]
    for chunk in result:
        if ends_with_heading(chunk.text):
            # Only allowed when the parent chunk itself ends on that heading;
            # there is no following text inside the parent to attach it to.
            assert chunk.source_char_end == len(by_id[chunk.parent_chunk_id].text)


def test_real_corpus_split_chunks_are_well_filled(real_tokenizer: object) -> None:
    parents = chunk_documents(ingest_knowledge_base())
    result = token_chunk_documents(parents, real_tokenizer)

    by_parent: dict[str, list[TokenDocumentChunk]] = {}
    for chunk in result:
        by_parent.setdefault(chunk.parent_chunk_id, []).append(chunk)

    floor = math.ceil(DEFAULT_MAX_TOKENS * MIN_FILL_RATIO)
    for parent_id, children in by_parent.items():
        assert [chunk.token_chunk_index for chunk in children] == list(range(len(children)))
        for chunk in children[:-1]:
            assert chunk.token_count >= floor, (parent_id, chunk.token_count)


def test_real_corpus_records_one_tokenizer_identity(real_tokenizer: object) -> None:
    result = token_chunk_documents(chunk_documents(ingest_knowledge_base()), real_tokenizer)

    assert {chunk.tokenizer_name for chunk in result} == {REAL_MODEL}
    revisions = {chunk.tokenizer_revision for chunk in result}
    assert len(revisions) == 1
    revision = revisions.pop()
    assert revision is None or re.fullmatch(r"[0-9a-f]{40}", revision), revision
    # Whatever is recorded must come from the tokenizer, not from a constant.
    if revision is not None:
        assert revision in str(getattr(real_tokenizer, "vocab_file", ""))
