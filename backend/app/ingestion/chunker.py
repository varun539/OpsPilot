"""Deterministic, character-based chunking of validated knowledge documents.

Chunking is deliberately **character-based, not token-based**: ``chunk_size``
and ``chunk_overlap`` count characters (Python string lengths / code points),
not LLM tokens. Character counts are model-independent and reproducible with
standard library code, so chunk boundaries and chunk IDs never change when the
embedding or generation model changes. A later stage that needs token budgets
can translate these limits for its own model without re-chunking documents.

The algorithm is intentionally small and readable:

1. Split the Markdown body into *blocks* at blank lines, but keep a fenced code
   block (backticks or tildes) as one block even when it contains blank lines.
   A fence is only closed by a run of the *same* character that is at least as
   long as the opening run, is indented by at most three spaces, and has no
   trailing text; an opener-style line carrying an info string inside a fence
   is content, not a closing fence.
2. Merge a run of Markdown headings with the block that follows it, so a
   heading is not separated from the text it introduces (unless the merged
   block would exceed the chunk size).
3. Split any block larger than ``chunk_size`` at newline or word boundaries.
   Only blocks that do not fit are split, so normal paragraphs and code
   examples stay intact. Splitting reserves room for the configured overlap
   (up to half of ``chunk_size``) so two fragments of the same long block can
   still share context; a block that already fits is never split just to
   reserve overlap.
4. Greedily pack blocks into chunks, flushing before ``chunk_size`` would be
   exceeded. Each new chunk after the first starts with a word-aligned suffix
   of the previous chunk, up to ``chunk_overlap`` characters and up to the room
   that remains in the new chunk, so neighbouring chunks share context.
   Overlap is best-effort: a block that already fills most of the budget
   cannot share the full configured amount, and that is intentional rather
   than a silent truncation to zero.

Leading indentation is preserved on lines where Markdown attaches meaning to
it: fenced blocks (including fences indented inside a list item), indented code
examples, nested list items and continuation lines inside a block. Only the
first line of an ordinary paragraph is left-trimmed, so prose chunks do not
start with stray spaces. Whitespace at chunk boundaries may be dropped.

A chunk may start or end inside a fenced code block when the code block is
larger than ``chunk_size``. Such fragments are copied *exactly* from the source
and are not wrapped in synthetic fences: adding fence markers would invent
content that is not in the document and would break the rule that chunk text is
attributable to its source range. Consumers that need self-contained Markdown
can wrap a fragment at render time using the chunk's provenance, without the
chunker inventing markers that are not in the source.

Nothing else is removed: every block either fits inside a single chunk or is
represented by its consecutive fragments across chunks. ``chunk_documents``
preserves input (and therefore document) ordering and is deterministic: the
same documents and parameters always produce the same chunk IDs, text and
order.

Invalid configuration raises :class:`ValueError`; no chunk is ever produced for
a document with an empty or whitespace-only body.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable

from .models import DocumentChunk, KnowledgeDocument

#: Default chunk length in characters. Small enough to embed and cite
#: precisely, large enough to hold a full incident section.
DEFAULT_CHUNK_SIZE = 1000

#: Default number of characters copied from the previous chunk into the next
#: one, so a fact split across a boundary stays retrievable.
DEFAULT_CHUNK_OVERLAP = 150

# A fence opener may be indented by at most three spaces (CommonMark); four or
# more spaces starts an indented code block instead. The info string is kept
# separate so backtick fences containing a backtick there can be rejected.
_FENCE_OPEN_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>.*)$"
)
# A closing fence has the same shape but nothing after it except spaces/tabs.
_FENCE_CLOSE_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<tail>[ \t]*)$"
)
_HEADING_RE = re.compile(r"^#{1,6}\s")
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d{1,9}[.)])\s")
_WHITESPACE_RE = re.compile(r"\s")


def chunk_documents(
    documents: Iterable[KnowledgeDocument],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[DocumentChunk]:
    """Chunk every document, preserving input order.

    Parameters are in characters (see the module docstring). The returned list
    is ordered by document and then by ``chunk_index``; IDs are deterministic.
    """
    _validate_config(chunk_size, chunk_overlap)
    chunks: list[DocumentChunk] = []
    for document in documents:
        chunks.extend(chunk_document(document, chunk_size, chunk_overlap))
    return chunks


def chunk_document(
    document: KnowledgeDocument,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[DocumentChunk]:
    """Chunk one document into validated :class:`DocumentChunk` objects.

    Returns an empty list for a document whose body is empty or whitespace-only.
    The input document and its metadata are never modified.
    """
    _validate_config(chunk_size, chunk_overlap)

    if not document.content or not document.content.strip():
        return []

    pieces = _piece_document(document.content, chunk_size, chunk_overlap)
    texts = _pack_pieces(pieces, chunk_size, chunk_overlap)
    return [
        _build_chunk(document, index, text) for index, text in enumerate(texts)
    ]


# --------------------------------------------------------------------------- #
# Validation and construction
# --------------------------------------------------------------------------- #
def _validate_config(chunk_size: int, chunk_overlap: int) -> None:
    """Reject nonsensical chunking parameters with actionable messages."""
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise ValueError("chunk_size must be an integer number of characters")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0 characters")
    if isinstance(chunk_overlap, bool) or not isinstance(chunk_overlap, int):
        raise ValueError("chunk_overlap must be an integer number of characters")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be strictly smaller than chunk_size")


def _build_chunk(document: KnowledgeDocument, index: int, text: str) -> DocumentChunk:
    """Copy document metadata onto one chunk without inventing any values."""
    metadata = document.metadata
    return DocumentChunk(
        chunk_id=f"{document.doc_id}::chunk::{index:04d}",
        doc_id=document.doc_id,
        title=metadata.title,
        category=metadata.category,
        component=metadata.component,
        severity=metadata.severity,
        tags=list(metadata.tags),
        source_type=metadata.source_type,
        source_path=document.source_path,
        chunk_index=index,
        text=text,
        character_count=len(text),
        applies_to=metadata.applies_to,
        last_updated=metadata.last_updated,
        related_documents=list(metadata.related_documents),
        # Deep copy: frontmatter extras may contain nested dicts/lists, and a
        # caller mutating a chunk's metadata must never reach the source
        # KnowledgeDocument (or another chunk of the same document).
        extra_metadata=copy.deepcopy(metadata.model_extra or {}),
    )


# --------------------------------------------------------------------------- #
# Markdown-aware block extraction
# --------------------------------------------------------------------------- #
def _fence_opening(line: str) -> tuple[str, int] | None:
    """Return ``(fence_character, fence_length)`` for a fence opener line.

    Backtick fences reject an info string containing a backtick, which keeps a
    line whose info string looks like code from being mistaken for a fence.
    Tilde fences have no such restriction.
    """
    match = _FENCE_OPEN_RE.match(line)
    if match is None:
        return None
    fence = match.group("fence")
    if fence[0] == "`" and "`" in match.group("info"):
        return None
    return fence[0], len(fence)


def _is_fence_closing(line: str, character: str, length: int) -> bool:
    """True when ``line`` closes a fence of ``character`` opened at ``length``.

    The closing run must use the same character, be at least as long as the
    opener, and be followed only by spaces or tabs. Indentation of the closing
    fence is limited to three spaces, so a fence-looking line inside an
    indented code example does not end the surrounding fence.
    """
    match = _FENCE_CLOSE_RE.match(line)
    if match is None:
        return False
    fence = match.group("fence")
    return fence[0] == character and len(fence) >= length


def _keeps_leading_indent(first_line: str) -> bool:
    """True when the first line's indentation carries Markdown meaning.

    Covered: fenced blocks (their fence may be indented inside a list item),
    indented code examples (four spaces or a tab), and list items. Ordinary
    paragraph first lines are left-trimmed by the caller.
    """
    if _fence_opening(first_line) is not None:
        return True
    if first_line.startswith(("    ", "\t")):
        return True
    return _LIST_ITEM_RE.match(first_line.lstrip(" ")) is not None


def _strip_block_edges(lines: list[str]) -> str:
    """Join block lines, trimming edges without discarding meaningful indent.

    Trailing whitespace at the end of the block is removed. The leading
    whitespace of the first line is kept only when it is structural (see
    :func:`_keeps_leading_indent`); otherwise it is stripped so an ordinary
    paragraph does not gain stray leading spaces. Indentation on every interior
    line, including list continuations and code inside fences, is untouched.
    """
    block = "\n".join(lines).rstrip()
    if not block:
        return ""
    if _keeps_leading_indent(block.split("\n", 1)[0]):
        return block
    return block.lstrip()


def _markdown_blocks(text: str) -> list[str]:
    """Split Markdown text into paragraph and fenced-code blocks.

    Blank lines separate paragraphs. A fenced block is kept whole, including
    its fence lines and any blank lines inside it; an unclosed fence runs to
    the end of the document rather than being dropped. Fence recognition uses
    raw lines so indentation and fence length are respected.
    """
    blocks: list[str] = []
    current: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    def flush() -> None:
        if current:
            block = _strip_block_edges(current)
            if block:
                blocks.append(block)
            current.clear()

    for line in text.split("\n"):
        if fence_character is not None:
            current.append(line)
            if _is_fence_closing(line, fence_character, fence_length):
                flush()
                fence_character = None
            continue

        opening = _fence_opening(line)
        if opening is not None:
            flush()
            fence_character, fence_length = opening
            current.append(line)
        elif not line.strip():
            flush()
        else:
            current.append(line)

    flush()
    return blocks


def _is_heading(block: str) -> bool:
    """True for a single-line ATX heading such as ``## Symptoms``."""
    return "\n" not in block and _HEADING_RE.match(block) is not None


def _merge_headings(blocks: list[str], limit: int) -> list[str]:
    """Attach each heading run to the following block when it still fits.

    This keeps a heading in the same chunk as the text it introduces, which
    matters both for retrieval context and for citation readability. A heading
    followed by a block too large to merge is emitted on its own and the large
    block is split later.
    """
    merged: list[str] = []
    pending: list[str] = []

    def flush_pending() -> None:
        if pending:
            merged.append("\n\n".join(pending))
            pending.clear()

    for block in blocks:
        if _is_heading(block):
            pending.append(block)
            continue
        if pending:
            candidate = "\n\n".join([*pending, block])
            if len(candidate) <= limit:
                merged.append(candidate)
                pending.clear()
            else:
                flush_pending()
                merged.append(block)
        else:
            merged.append(block)

    flush_pending()
    return merged


def _split_oversized_block(block: str, limit: int, reserve: int = 0) -> list[str]:
    """Split a block longer than ``limit`` at newline or word boundaries.

    Splitting only happens when a single paragraph or code block cannot fit in
    one chunk. Newlines are preferred (safe for code), then spaces; a single
    unbroken run longer than the fragment budget is hard-split as a last
    resort. The concatenation of the fragments retains all non-boundary
    content.

    ``reserve`` leaves that many characters of the ``limit`` unused by a
    fragment. Packing can then prepend overlap when a fragment starts a new
    chunk, instead of that overlap being squeezed to nothing by a fragment
    that consumes the whole budget. The reserve is applied only to blocks that
    must be split anyway; a block that already fits is returned untouched, so
    no extra chunk is created just to reserve space.
    """
    if len(block) <= limit:
        return [block]

    fragment_limit = limit - reserve
    if fragment_limit <= 0:
        # Defensive: configuration validation keeps overlap < chunk_size, but
        # never let a reserve collapse the content budget to zero.
        fragment_limit = limit

    fragments: list[str] = []
    remaining = block
    while len(remaining) > fragment_limit:
        window = remaining[:fragment_limit]
        split_at = window.rfind("\n")
        if split_at <= 0:
            split_at = max(window.rfind(" "), window.rfind("\t"))
        if split_at <= 0:
            # No boundary inside the reserved budget. Look ahead for the next
            # newline/space, as long as the fragment still fits the hard chunk
            # limit; this keeps a long identifier or URL in one fragment rather
            # than cutting it in half just because overlap space was reserved.
            lookahead = remaining[:limit]
            candidates = [
                position
                for position in (
                    lookahead.find("\n", fragment_limit),
                    lookahead.find(" ", fragment_limit),
                    lookahead.find("\t", fragment_limit),
                )
                if position > 0
            ]
            split_at = min(candidates) if candidates else fragment_limit

        fragment = remaining[:split_at].rstrip()
        if fragment:
            fragments.append(fragment)

        remaining = remaining[split_at:]
        # Drop exactly the separator character. Stripping all whitespace here
        # would eat the indentation of the next line (and with it code or list
        # structure), so leave any further whitespace in place.
        if remaining[:1] in ("\n", " ", "\t"):
            remaining = remaining[1:]

    tail = remaining.rstrip()
    if tail:
        fragments.append(tail)
    return fragments


def _piece_document(
    text: str, chunk_size: int, chunk_overlap: int = 0
) -> list[tuple[int, str]]:
    """Return ``(block_index, fragment)`` pairs for one document body.

    The block index lets packing join fragments from the same paragraph or code
    block with a single newline, while separate blocks keep a blank line.

    Oversized blocks reserve some of their budget for overlap. The reserve is
    capped at half the chunk size and at the configured overlap, so extreme
    settings such as ``chunk_size=100, chunk_overlap=99`` still leave a usable
    amount of room for new content instead of producing one-character chunks.
    """
    reserve = min(chunk_overlap, chunk_size // 2)
    pieces: list[tuple[int, str]] = []
    for block_index, block in enumerate(_merge_headings(_markdown_blocks(text), chunk_size)):
        for fragment in _split_oversized_block(block, chunk_size, reserve):
            pieces.append((block_index, fragment))
    return pieces


# --------------------------------------------------------------------------- #
# Overlap and packing
# --------------------------------------------------------------------------- #
def _trim_to_word_start(fragment: str) -> str:
    """Drop a leading partial word from a fragment known to start mid-word.

    Callers must only use this when the fragment really starts inside a word.
    Using it on a fragment that already starts at a word boundary would discard
    a complete word, which is exactly the behaviour this helper used to cause
    for overlap.
    """
    fragment = fragment.lstrip()
    match = _WHITESPACE_RE.search(fragment)
    if match is None:
        return fragment
    return fragment[match.start() :].lstrip()


def _overlap_tail(text: str, overlap: int) -> str:
    """Return up to ``overlap`` trailing characters of ``text``.

    The cut is moved forward to the next word boundary only when it lands
    *inside* a word. If the requested tail already starts at the beginning of a
    word (or at the beginning of ``text``), the complete first word is kept.
    The result may be shorter than requested when a word boundary dictates it;
    it is never longer. With ``overlap == 0`` no context is copied.
    """
    if overlap <= 0 or not text:
        return ""

    start = max(len(text) - overlap, 0)
    tail = text[start:]
    if start > 0 and not text[start - 1].isspace() and not text[start].isspace():
        # The cut split a word: skip the partial leading token.
        return _trim_to_word_start(tail)
    return tail.lstrip()


def _pack_pieces(
    pieces: list[tuple[int, str]], chunk_size: int, chunk_overlap: int
) -> list[str]:
    """Greedily pack fragments into overlapping chunks of at most ``chunk_size``.

    Overlap is selected from the actual space left in the new chunk, not from a
    fixed slice that is later truncated. When the next fragment already fills
    the budget, no overlap fits and the chunk starts with the fragment alone;
    reserving room while splitting long blocks makes that case rare for content
    that has to be split.
    """
    if not pieces:
        return []

    chunks: list[str] = []
    current = ""
    current_block: int | None = None

    for block_index, piece in pieces:
        if not current:
            current = piece
            current_block = block_index
            continue

        separator = "\n" if block_index == current_block else "\n\n"
        if len(current) + len(separator) + len(piece) <= chunk_size:
            current += separator + piece
            current_block = block_index
            continue

        chunks.append(current)
        # Two characters are reserved for the blank line between overlap and
        # the fragment. If that leaves no room, the fragment needs the whole
        # budget and overlap is necessarily dropped for this boundary.
        room = chunk_size - len(piece) - 2
        prefix = _overlap_tail(current, min(chunk_overlap, room)) if room > 0 else ""
        separator = "\n\n" if prefix else ""

        current = f"{prefix}{separator}{piece}" if prefix else piece
        current_block = block_index

    if current.strip():
        chunks.append(current)
    return chunks
