"""Token-aware re-chunking of existing character-based :class:`DocumentChunk` objects.

The character chunker in :mod:`backend.app.ingestion.chunker` is deliberately
model-independent: it targets a readable size in characters and knows nothing
about tokenizers. That keeps chunk boundaries stable when the embedding model
changes, but the embedding model still has a hard input limit, and a small
number of character chunks can exceed it.

This module is the bridge. It takes the chunks produced by ``chunk_documents``
and, for the ones that do not fit the token budget, slices them again using the
*actual* tokenizer offsets. It never guesses at token boundaries and never
truncates: the only chunks that change are the ones that must.

Design rules:

* **Fast tokenizer required.** Sub-token offsets are only meaningful when the
  tokenizer reports them (``return_offsets_mapping=True``). A slow tokenizer is
  rejected instead of falling back to a character heuristic, because a silent
  fallback would produce chunks whose measured token count is a guess.
* **Character slices, not ``decode``.** Every token chunk's ``text`` is a slice
  of its parent chunk's text, so whitespace, Markdown and code formatting are
  preserved exactly. ``tokenizer.decode()`` is never used to rebuild text.
* **Measured, not assumed.** Each chunk's ``token_count`` comes from
  ``tokenizer(text, add_special_tokens=True, truncation=False)``, so the
  special tokens (``[CLS]``/``[SEP]``, ``<s>``/``</s>``, ...) are part of the
  count the embedding model will actually see.
* **Fill-aware structural boundaries.** Cut points are chosen in this order
  (the explicit policy, also implemented in :func:`_choose_end_token`):

  1. If the rest of the parent chunk fits the budget, take all of it.
  2. Otherwise look inside the *preferred region* -- the part of the content
     window from ``MIN_FILL_RATIO`` (70%) of the window up to its maximum --
     and prefer the latest paragraph break, then the latest line break.
  3. If the preferred region holds no structural boundary, take the latest
     word boundary, then the latest raw token boundary, before the limit.

  A structural break below the preferred region is *not* accepted, which is
  what stops a short paragraph from producing a half-empty chunk when a later
  boundary could fill it. Paragraphs are never split to top up a chunk; the
  cut still lands on a real boundary. Only the final chunk of a parent may be
  genuinely short, because nothing follows it to fill with.
* **Headings stay with their body.** A cut is never made directly after an ATX
  heading line, so a heading is not separated from the section it introduces.
  The one exception is a parent chunk that itself *ends* on a heading (the
  character chunker can produce that when a section has no body); there is no
  following text inside that parent to attach it to.
* **Overlap is best effort.** Up to ``token_overlap`` tokens of context are
  copied from the previous token chunk, aligned to a word start when possible,
  and never to a partial heading line. When a boundary or the remaining budget
  prevents it, overlap is smaller; that is intentional and visible, not a
  silent truncation.
* **WordPiece offset semantics are validated, not assumed.** The boundary
  logic reads the untokenized text *between* tokens to find paragraphs and
  spaces. Tokenizers whose offsets absorb the leading whitespace (byte-level
  BPE, e.g. GPT-2/Llama style) would silently report every gap as empty, so
  they are rejected up front. This module is validated for the current
  ``BertTokenizer`` (``all-MiniLM-L6-v2`` through ``AutoTokenizer`` with
  ``use_fast=True``, i.e. WordPiece offsets); supporting another tokenizer
  family requires reviewing its offset behaviour first.
* **Revision recorded when the tokenizer exposes it.** ``tokenizer_name`` and
  ``tokenizer_revision`` are separate fields, so token counts can be traced to
  an immutable commit instead of a mutable repository name.
* **Deterministic.** The same chunks, tokenizer and parameters always produce
  the same token chunk IDs, text and order. Ids are derived from the parent
  chunk ID plus a per-parent index, never from a global counter.

Invalid configuration (non-integer sizes, overlap >= budget, a budget smaller
than the model's special tokens) and invalid tokenizers raise ``ValueError``
with a message naming the problem.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    CHUNK_ID_PATTERN,
    DOC_ID_PATTERN,
    DocumentChunk,
    Severity,
    SourceType,
)

#: Default maximum number of tokens per chunk, *including* special tokens.
DEFAULT_MAX_TOKENS = 240

#: Default number of tokens copied from the previous token chunk as context.
#: Measured in content tokens; special tokens are not part of the overlap.
DEFAULT_TOKEN_OVERLAP = 32

#: Fraction of the content window a structural cut must fill to be preferred
#: over a later word/token boundary. A paragraph break earlier than this is
#: skipped rather than accepted, so chunks are not left half empty.
MIN_FILL_RATIO = 0.7

#: Token chunk IDs extend the parent chunk ID, e.g.
#: ``KB-001::chunk::0000::tok::0003``.
TOKEN_CHUNK_ID_PATTERN = r"^KB-\d{3}::chunk::\d{4,}::tok::\d{4,}$"

#: ATX heading line, used to avoid closing a chunk on a heading that would then
#: be separated from the paragraph it introduces.
_HEADING_RE = re.compile(r"^#{1,6}\s")

#: Probe used to check that a tokenizer's offsets leave whitespace *between*
#: tokens (WordPiece semantics). Byte-level BPE offsets absorb the leading
#: space, which would make every gap look empty.
_OFFSET_PROBE = "alpha   beta\n\ngamma delta"

#: ``<cache>/models--org--name/snapshots/<commit>/<file>``, from which the
#: immutable revision can be recovered when transformers does not record it.
_SNAPSHOT_RE = re.compile(r"[\\/]snapshots[\\/]([0-9a-f]{40})(?:[\\/]|$)")


class TokenDocumentChunk(BaseModel):
    """One token-budgeted slice of a parent :class:`DocumentChunk`.

    All document-level metadata is copied from the parent chunk. The two
    indices are deliberately separate fields: ``parent_chunk_index`` is the
    position of the original character-based chunk within its document, while
    ``token_chunk_index`` is this chunk's position within that parent. Neither
    is derived from the other, so a citation can always be traced back through
    both levels of chunking.

    ``source_char_start``/``source_char_end`` are offsets into the parent
    chunk's ``text`` (half-open). They make provenance checkable and let a
    caller rebuild the exact parent text span without re-tokenizing.
    """

    model_config = ConfigDict(extra="forbid")

    token_chunk_id: str = Field(
        pattern=TOKEN_CHUNK_ID_PATTERN,
        description="Deterministic ID, e.g. 'KB-001::chunk::0000::tok::0003'.",
    )
    parent_chunk_id: str = Field(
        pattern=CHUNK_ID_PATTERN,
        description="ID of the character-based chunk this slice came from.",
    )
    parent_chunk_index: int = Field(
        ge=0, description="Character chunk index within the source document."
    )
    doc_id: str = Field(pattern=DOC_ID_PATTERN, description="Source document ID.")
    title: str = Field(min_length=1, description="Source document title.")
    category: str = Field(min_length=1, description="Source document category.")
    component: str = Field(min_length=1, description="Source document component.")
    severity: Severity = Field(description="Source document severity.")
    tags: list[str] = Field(min_length=1, description="Source document tags.")
    source_type: SourceType = Field(description="Source document provenance marker.")
    source_path: str = Field(min_length=1, description="Source document path.")

    tokenizer_name: str = Field(
        min_length=1,
        description="Tokenizer identity the token counts were measured with.",
    )
    tokenizer_revision: str | None = Field(
        default=None,
        description=(
            "Immutable tokenizer revision (Hugging Face commit) the counts were "
            "measured with; None when the tokenizer does not expose one."
        ),
    )
    token_chunk_index: int = Field(
        ge=0, description="Zero-based position within the parent character chunk."
    )
    text: str = Field(min_length=1, description="Exact slice of the parent chunk text.")
    token_count: int = Field(
        gt=0,
        description="Token count including special tokens, as the model will see it.",
    )
    max_tokens: int = Field(
        gt=0,
        description="Token budget used to produce this chunk, including special tokens.",
    )
    token_overlap: int = Field(
        ge=0,
        description="Configured overlap in content tokens (special tokens excluded).",
    )
    source_char_start: int = Field(
        ge=0, description="Start offset of this slice within the parent chunk text."
    )
    source_char_end: int = Field(
        gt=0, description="End offset (exclusive) within the parent chunk text."
    )

    applies_to: str | None = None
    last_updated: date | None = None
    related_documents: list[str] = Field(default_factory=list)
    extra_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional source frontmatter keys, preserved verbatim.",
    )

    @model_validator(mode="after")
    def _validate_derived_fields(self) -> TokenDocumentChunk:
        """Reject chunks whose identity, offsets or counts contradict themselves."""
        if not self.text.strip():
            raise ValueError("token chunk text must not be blank")
        if self.source_char_end - self.source_char_start != len(self.text):
            raise ValueError(
                "source_char_start/source_char_end must span exactly the chunk text "
                f"({self.source_char_end - self.source_char_start} != {len(self.text)})"
            )
        expected_id = f"{self.parent_chunk_id}::tok::{self.token_chunk_index:04d}"
        if self.token_chunk_id != expected_id:
            raise ValueError(
                f"token_chunk_id must be {expected_id!r}, got {self.token_chunk_id!r}"
            )
        if self.token_overlap >= self.max_tokens:
            raise ValueError(
                "token_overlap must be strictly smaller than max_tokens "
                f"({self.token_overlap} >= {self.max_tokens})"
            )
        if self.token_count > self.max_tokens:
            raise ValueError(
                f"token_count ({self.token_count}) exceeds max_tokens ({self.max_tokens})"
            )
        return self


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def count_tokens(text: str, tokenizer: Any) -> int:
    """Return the token count of ``text`` *including* special tokens.

    This is the single measurement used for every reported ``token_count`` and
    for the final budget check, so tests and audit scripts measure exactly what
    the embedding model will receive.
    """
    encoded = tokenizer(text, add_special_tokens=True, truncation=False)
    try:
        return len(encoded["input_ids"])
    except (KeyError, TypeError) as exc:  # pragma: no cover - defensive
        raise ValueError(
            "tokenizer call must return a mapping with 'input_ids'; "
            f"got {type(encoded).__name__}"
        ) from exc


def token_chunk_documents(
    chunks: Iterable[DocumentChunk],
    tokenizer: Any,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    token_overlap: int = DEFAULT_TOKEN_OVERLAP,
) -> list[TokenDocumentChunk]:
    """Re-chunk every character chunk, preserving document and parent order.

    Args:
        chunks: the output of :func:`backend.app.ingestion.chunk_documents`.
        tokenizer: a *fast* Hugging Face tokenizer (or a compatible object).
        max_tokens: hard per-chunk budget, including special tokens.
        token_overlap: maximum tokens of context copied between split chunks.

    Returns:
        Token chunks ordered by parent chunk and then by ``token_chunk_index``.
        Chunks that already fit are returned as a single unchanged text.
    """
    _validate_config(max_tokens, token_overlap)
    _require_supported_tokenizer(tokenizer)

    token_chunks: list[TokenDocumentChunk] = []
    for chunk in chunks:
        token_chunks.extend(
            token_chunk_document_chunk(chunk, tokenizer, max_tokens, token_overlap)
        )
    return token_chunks


def token_chunk_document_chunk(
    chunk: DocumentChunk,
    tokenizer: Any,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    token_overlap: int = DEFAULT_TOKEN_OVERLAP,
) -> list[TokenDocumentChunk]:
    """Token-chunk a single :class:`DocumentChunk`.

    Returns an empty list only when the tokenizer reports no tokens for the
    parent text (for example a whitespace-only body). The parent chunk and its
    metadata are never modified.
    """
    _validate_config(max_tokens, token_overlap)
    _require_supported_tokenizer(tokenizer)

    tokenizer_name = _tokenizer_name(tokenizer)
    tokenizer_revision = _tokenizer_revision(tokenizer)
    offsets = _token_offsets(chunk.text, tokenizer)
    if not offsets:
        return []

    max_tokens = int(max_tokens)
    token_overlap = int(token_overlap)
    body_budget = max_tokens - _special_token_overhead(tokenizer)
    if body_budget < 1:
        raise ValueError(
            f"max_tokens={max_tokens} does not leave room for content after the "
            f"tokenizer's {max_tokens - body_budget} special token(s); "
            f"use max_tokens > {max_tokens - body_budget}"
        )

    # Fast path: the chunk already fits, so keep it byte-for-byte unchanged.
    if count_tokens(chunk.text, tokenizer) <= max_tokens:
        return [
            _build_token_chunk(
                chunk,
                tokenizer_name=tokenizer_name,
                tokenizer_revision=tokenizer_revision,
                max_tokens=max_tokens,
                token_overlap=token_overlap,
                index=0,
                text=chunk.text,
                token_count=count_tokens(chunk.text, tokenizer),
                start=0,
                end=len(chunk.text),
            )
        ]

    total_tokens = len(offsets)
    results: list[TokenDocumentChunk] = []
    token_start = 0

    while token_start < total_tokens:
        # Every chunk after the first reserves room for its overlap, so adding
        # context can never push it over the budget. The last chunk still gets
        # the full budget because it stops at the end of the text anyway.
        content_budget = body_budget
        if results and token_overlap:
            content_budget = max(1, body_budget - token_overlap)

        window_end = min(token_start + content_budget, total_tokens)
        end_token = (
            total_tokens
            if window_end >= total_tokens
            else _choose_end_token(chunk.text, offsets, token_start, window_end)
        )

        start_token = token_start
        if results and token_overlap > 0:
            start_token = _overlap_start_token(
                chunk.text, offsets, token_start, end_token, token_overlap, body_budget
            )

        start_char = offsets[start_token][0]
        end_char = offsets[end_token - 1][1]
        text = chunk.text[start_char:end_char]
        token_count = count_tokens(text, tokenizer)

        # Retokenizing a mid-word slice can yield a token more than the source
        # range counted, so the measured count is authoritative: give up overlap
        # first, then shrink the content window, never truncate the text.
        while token_count > max_tokens and start_token < token_start:
            start_token += 1
            start_char = offsets[start_token][0]
            text = chunk.text[start_char:end_char]
            token_count = count_tokens(text, tokenizer)

        while token_count > max_tokens and end_token > token_start + 1:
            end_token -= 1
            # Keep the heading rule intact even on this safety path: a shrink
            # must not leave a heading stranded at the end of the chunk.
            while end_token > token_start + 1 and _is_heading_cut(
                chunk.text, offsets, end_token
            ):
                end_token -= 1
            end_char = offsets[end_token - 1][1]
            text = chunk.text[start_char:end_char]
            token_count = count_tokens(text, tokenizer)

        if token_count > max_tokens:  # pragma: no cover - guarded by body_budget
            raise ValueError(
                f"{chunk.chunk_id}: a single token of {chunk.source_path} cannot fit "
                f"in max_tokens={max_tokens} including special tokens"
            )

        results.append(
            _build_token_chunk(
                chunk,
                tokenizer_name=tokenizer_name,
                tokenizer_revision=tokenizer_revision,
                max_tokens=max_tokens,
                token_overlap=token_overlap,
                index=len(results),
                text=text,
                token_count=token_count,
                start=start_char,
                end=end_char,
            )
        )
        token_start = end_token

    return results


# --------------------------------------------------------------------------- #
# Validation and construction
# --------------------------------------------------------------------------- #
def _validate_config(max_tokens: int, token_overlap: int) -> None:
    """Reject nonsensical token budgets with actionable messages."""
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise ValueError("max_tokens must be an integer number of tokens")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than 0 tokens")
    if isinstance(token_overlap, bool) or not isinstance(token_overlap, int):
        raise ValueError("token_overlap must be an integer number of tokens")
    if token_overlap < 0:
        raise ValueError("token_overlap must be non-negative")
    if token_overlap >= max_tokens:
        raise ValueError("token_overlap must be strictly smaller than max_tokens")


def _require_fast_tokenizer(tokenizer: Any) -> None:
    """Reject slow tokenizers, which cannot report sub-token character offsets."""
    if not getattr(tokenizer, "is_fast", False):
        raise ValueError(
            "tokenizer must be a fast tokenizer with offset mapping support "
            "(tokenizer.is_fast is not True); load it with use_fast=True"
        )


def _require_supported_tokenizer(tokenizer: Any) -> None:
    """Reject tokenizers whose offset semantics break boundary detection.

    The splitter infers paragraph and word boundaries from the untokenized text
    *between* two tokens. That only works when tokens do not absorb the
    whitespace in front of them. Byte-level BPE tokenizers (GPT-2, Llama) report
    an offset that includes the leading space, so every gap would look empty and
    the chunker would silently fall back to raw token cuts. This is validated on
    a probe string rather than assumed: unsupported tokenizers fail loudly.
    """
    _require_fast_tokenizer(tokenizer)
    offsets = _token_offsets(_OFFSET_PROBE, tokenizer)
    if len(offsets) < 2:  # pragma: no cover - defensive
        raise ValueError(
            "tokenizer offsets are not usable for token chunking: the probe text "
            f"{_OFFSET_PROBE!r} produced {len(offsets)} token(s)"
        )
    gaps = [
        _OFFSET_PROBE[offsets[index][1] : offsets[index + 1][0]]
        for index in range(len(offsets) - 1)
    ]
    has_paragraph_gap = any("\n\n" in gap for gap in gaps)
    has_space_gap = any(" " in gap for gap in gaps)
    if not (has_paragraph_gap and has_space_gap):
        raise ValueError(
            "tokenizer offset semantics are not supported by token_chunker: tokens "
            "absorb the whitespace between them, so paragraph and word boundaries "
            "cannot be detected. This implementation is validated for WordPiece "
            "offsets (BertTokenizer with use_fast=True, e.g. all-MiniLM-L6-v2); "
            "review the new tokenizer's offset behaviour before using it."
        )


def _tokenizer_name(tokenizer: Any) -> str:
    """Return a stable identity string for provenance."""
    name = getattr(tokenizer, "name_or_path", None)
    if isinstance(name, str) and name:
        return name
    return type(tokenizer).__name__


def _tokenizer_revision(tokenizer: Any) -> str | None:
    """Return the immutable tokenizer revision, or ``None`` when unavailable.

    transformers 5.x does not populate ``_commit_hash`` for a tokenizer loaded
    from the local cache, so the revision is recovered from the resolved file
    path: files inside a Hugging Face cache live under
    ``models--org--name/snapshots/<commit>/``, where ``<commit>`` *is* the
    commit the files were fetched from. Nothing is guessed: if no such path and
    no recorded commit hash is present (for example a tokenizer loaded from a
    plain local directory), the revision stays ``None`` and the fact is visible
    in the metadata instead of being invented.
    """
    candidates: list[Any] = [
        getattr(tokenizer, "_commit_hash", None),
        getattr(tokenizer, "commit_hash", None),
    ]
    init_kwargs = getattr(tokenizer, "init_kwargs", None)
    if isinstance(init_kwargs, dict):
        candidates.append(init_kwargs.get("_commit_hash"))
        candidates.extend(
            value for value in init_kwargs.values() if isinstance(value, str)
        )
    candidates.append(getattr(tokenizer, "vocab_file", None))
    candidates.append(getattr(tokenizer, "tokenizer_file", None))
    candidates.append(getattr(tokenizer, "name_or_path", None))

    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        match = _SNAPSHOT_RE.search(candidate)
        if match:
            return match.group(1)
        # A bare 40-character hex string is an explicit commit, not a path.
        if len(candidate) == 40 and all(
            character in "0123456789abcdef" for character in candidate
        ):
            return candidate
    return None


def _special_token_overhead(tokenizer: Any) -> int:
    """Number of special tokens the tokenizer adds to every encode call."""
    try:
        encoded = tokenizer("", add_special_tokens=True, truncation=False)
        return len(encoded["input_ids"])
    except (KeyError, TypeError, NotImplementedError) as exc:  # pragma: no cover
        raise ValueError(f"tokenizer could not report its special tokens: {exc}") from exc


def _token_offsets(text: str, tokenizer: Any) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character offsets for ``text``'s tokens.

    Special tokens are excluded: the offsets describe content only, which is
    what the splitting code needs. They are re-added when counting.
    """
    try:
        encoded = tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            return_offsets_mapping=True,
        )
    except (NotImplementedError, TypeError) as exc:
        raise ValueError(
            "tokenizer must support return_offsets_mapping=True to token-chunk "
            "without guessing at token boundaries"
        ) from exc

    try:
        input_ids = list(encoded["input_ids"])
        raw_offsets = list(encoded["offset_mapping"])
    except (KeyError, TypeError) as exc:  # pragma: no cover - defensive
        raise ValueError(
            "tokenizer must return both 'input_ids' and 'offset_mapping' when "
            "return_offsets_mapping=True"
        ) from exc

    if len(input_ids) != len(raw_offsets):  # pragma: no cover - defensive
        raise ValueError(
            f"tokenizer returned {len(input_ids)} ids but {len(raw_offsets)} offsets"
        )

    offsets: list[tuple[int, int]] = []
    for pair in raw_offsets:
        start, end = pair
        start, end = int(start), int(end)
        if start < 0 or end < start or end > len(text):  # pragma: no cover
            raise ValueError(f"tokenizer returned an invalid offset {(start, end)}")
        offsets.append((start, end))
    return [(start, end) for start, end in offsets if end > start]


def _build_token_chunk(
    chunk: DocumentChunk,
    *,
    tokenizer_name: str,
    tokenizer_revision: str | None,
    max_tokens: int,
    token_overlap: int,
    index: int,
    text: str,
    token_count: int,
    start: int,
    end: int,
) -> TokenDocumentChunk:
    """Copy parent metadata onto one token chunk without inventing values."""
    return TokenDocumentChunk(
        token_chunk_id=f"{chunk.chunk_id}::tok::{index:04d}",
        parent_chunk_id=chunk.chunk_id,
        parent_chunk_index=chunk.chunk_index,
        doc_id=chunk.doc_id,
        title=chunk.title,
        category=chunk.category,
        component=chunk.component,
        severity=chunk.severity,
        tags=list(chunk.tags),
        source_type=chunk.source_type,
        source_path=chunk.source_path,
        tokenizer_name=tokenizer_name,
        tokenizer_revision=tokenizer_revision,
        token_chunk_index=index,
        text=text,
        token_count=token_count,
        max_tokens=max_tokens,
        token_overlap=token_overlap,
        source_char_start=start,
        source_char_end=end,
        applies_to=chunk.applies_to,
        last_updated=chunk.last_updated,
        related_documents=list(chunk.related_documents),
        # Deep copy: extras can hold nested dicts/lists, and a caller mutating a
        # token chunk's metadata must never reach the source DocumentChunk.
        extra_metadata=copy.deepcopy(chunk.extra_metadata),
    )


# --------------------------------------------------------------------------- #
# Boundary selection
# --------------------------------------------------------------------------- #
def _cut_priority(text: str, offsets: list[tuple[int, int]], end_token: int) -> int:
    """Score how good it is to cut *after* token ``end_token - 1``.

    3 = paragraph break, 2 = line break, 1 = word boundary, 0 = token boundary.
    The gap searched is the untokenized text between two tokens, which is where
    Markdown structure and whitespace live.
    """
    end_char = offsets[end_token - 1][1]
    next_start = offsets[end_token][0] if end_token < len(offsets) else len(text)
    gap = text[end_char:next_start]

    if gap.count("\n") >= 2:
        return 3
    if "\n" in gap:
        return 2
    if gap and any(character.isspace() for character in gap):
        return 1
    return 0


def _ends_with_heading(text: str, end_char: int) -> bool:
    """True when the line ending at ``end_char`` is an ATX heading."""
    line_start = text.rfind("\n", 0, end_char) + 1
    line = text[line_start:end_char].strip()
    return bool(line) and _HEADING_RE.match(line) is not None


def _is_heading_cut(text: str, offsets: list[tuple[int, int]], end_token: int) -> bool:
    """True when cutting after ``end_token - 1`` would end a chunk on a heading."""
    return _ends_with_heading(text, offsets[end_token - 1][1])


def _choose_end_token(
    text: str, offsets: list[tuple[int, int]], start_token: int, max_end_token: int
) -> int:
    """Pick the best cut point between ``start_token + 1`` and ``max_end_token``.

    Policy (see the module docstring): a structural cut is only accepted once
    the chunk fills at least ``MIN_FILL_RATIO`` of the content window, with
    paragraph breaks preferred over line breaks. Otherwise the latest word
    boundary is used, and only then the latest raw token boundary. Cuts that
    would leave a heading at the end of the chunk are never chosen.
    """
    window = max_end_token - start_token
    min_fill_token = start_token + math.ceil(window * MIN_FILL_RATIO)
    best_line: int | None = None
    best_word: int | None = None
    best_token: int | None = None

    for end_token in range(max_end_token, start_token, -1):
        if _is_heading_cut(text, offsets, end_token):
            continue
        priority = _cut_priority(text, offsets, end_token)
        if end_token >= min_fill_token:
            if priority == 3:
                # Latest paragraph break inside the preferred region: done.
                return end_token
            if priority == 2 and best_line is None:
                best_line = end_token
        if priority == 1 and best_word is None:
            best_word = end_token
        elif priority == 0 and best_token is None:
            best_token = end_token

    if best_line is not None:
        return best_line
    if best_word is not None:
        return best_word
    if best_token is not None:
        return best_token
    # Only heading cuts were available: keeping the heading would require an
    # empty chunk, so accept the window end rather than looping forever.
    return max_end_token  # pragma: no cover - needs a single-heading window


def _overlap_start_token(
    text: str,
    offsets: list[tuple[int, int]],
    token_start: int,
    end_token: int,
    token_overlap: int,
    body_budget: int,
) -> int:
    """Choose the first token to copy from the previous chunk as context.

    Starts ``token_overlap`` tokens back (never before the previous content
    start, never so far back that the new chunk would exceed its budget) and
    advances to the next word start so the overlap does not begin mid-word.
    """
    start_token = max(0, token_start - token_overlap)
    start_token = max(start_token, end_token - body_budget)
    start_token = min(start_token, token_start)

    while start_token < token_start:
        if start_token == 0:
            break
        previous_char = offsets[start_token - 1][1]
        gap = text[previous_char : offsets[start_token][0]]
        if gap and any(character.isspace() for character in gap):
            break
        start_token += 1

    # Never let the copied context start in the middle of a heading line: that
    # would duplicate a heading fragment ("Symptoms") without its markers.
    # Advancing to the line end keeps the heading itself intact in the previous
    # chunk and merely shortens the overlap.
    heading_end = _heading_line_end(text, offsets[start_token][0])
    if heading_end is not None:
        while start_token < token_start and offsets[start_token][0] < heading_end:
            start_token += 1

    return start_token


def _heading_line_end(text: str, char_index: int) -> int | None:
    """Return the newline offset of the heading line ``char_index`` falls inside.

    ``None`` when ``char_index`` is not strictly inside an ATX heading line's
    text (a token at the very start of the heading is not "mid-heading").
    """
    line_start = text.rfind("\n", 0, char_index) + 1
    line_end = text.find("\n", line_start)
    if line_end == -1:
        line_end = len(text)
    raw = text[line_start:line_end]
    stripped = raw.strip()
    if not stripped or _HEADING_RE.match(stripped) is None:
        return None
    indent = len(raw) - len(raw.lstrip())
    if char_index <= line_start + indent:
        return None
    return line_end
