"""Text normalisation for Markdown bodies.

Documents arrive from different operating systems and editors, so the same file
can contain Windows line endings, a UTF-8 byte-order mark, or a pile of blank
lines at the top and bottom. Those differences would make identical documents
produce different text (and therefore different future embeddings), so they are
removed here.

Deliberately conservative: this module touches *only* line endings, a leading
BOM, and blank lines at the very start/end of the document. Indentation inside
code blocks, trailing spaces used as Markdown hard line breaks, headings, lists
and fence markers are left exactly as written.
"""

from __future__ import annotations

BOM = "\ufeff"


def strip_bom(text: str) -> str:
    """Remove a leading UTF-8 byte-order mark.

    Only a *leading* BOM is removed: U+FEFF inside the text is meaningful
    content (for example in a zero-width no-break space demonstration) and must
    stay untouched.
    """
    return text.lstrip(BOM)


def normalize_newlines(text: str) -> str:
    """Convert Windows (``\\r\\n``) and legacy Mac (``\\r``) endings to ``\\n``.

    ``\\r\\n`` is replaced first; otherwise the remaining ``\\r`` would be
    handled separately and produce ``\\n\\n``.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def clean_document_text(text: str) -> str:
    """Normalise a Markdown body and trim blank lines at its boundaries.

    Blank lines *inside* the document are preserved because they separate
    paragraphs, lists and code blocks. Trailing newlines are removed so two
    identical documents always compare equal.
    """
    lines = normalize_newlines(strip_bom(text)).split("\n")

    # Drop whitespace-only lines at the top of the document.
    while lines and not lines[0].strip():
        lines.pop(0)

    # ...and at the bottom.
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)
