"""CLI: index the knowledge base into the persistent ChromaDB collection.

Examples::

    python -m backend.app.indexing --dry-run
    python -m backend.app.indexing
    python -m backend.app.indexing --knowledge-base /knowledge --local-files-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .indexer import index_knowledge_base


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.indexing",
        description=(
            "Index knowledge-base Markdown into the persistent ChromaDB "
            "collection. Idempotent: repeated runs upsert the same chunk IDs."
        ),
    )
    parser.add_argument(
        "--knowledge-base",
        type=Path,
        default=None,
        help="knowledge-base directory (default: data/knowledge_base)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse, clean and chunk only; do not embed or write",
    )
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="never download model or tokenizer files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = index_knowledge_base(
            directory=args.knowledge_base,
            dry_run=args.dry_run,
            local_files_only=args.local_files_only,
        )
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        print(f"indexing failed: {message[:500]}", file=sys.stderr)
        return 1
    print(json.dumps(result.summary(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
