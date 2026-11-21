"""Durable regression for the held-out vs development retrieval datasets.

The held-out split is *partially* overlapping with ``retrieval_eval.json``:
three answerable held-out queries (``hq16b``, ``hq17``, ``hq18``) duplicate
three development queries (``q01``, ``q03``, ``q04``) with identical
relevant-document labels. The remaining 16 queries are unanswerable and
independent. This test fails if that overlap set changes unexpectedly.
"""

from __future__ import annotations

import json
from pathlib import Path

HELDOUT_PATH = Path("tests/data/retrieval_heldout_v1.json")
EVAL_PATH = Path("tests/data/retrieval_eval.json")

EXPECTED_OVERLAPS = {
    "hq16b": "q01",
    "hq17": "q03",
    "hq18": "q04",
}


def _normalize_query_text(text: str) -> str:
    """Normalize query text for overlap detection.

    Lowercases, drops the 'internal server error' qualifier and the
    'after (an) unhandled exception' suffix, then collapses whitespace.
    This makes hq16b ("...HTTP 500 after unhandled exception") and
    q01 ("...HTTP 500 Internal Server Error after an unhandled exception")
    compare equal while keeping other queries distinct.
    """
    normalized = text.lower()
    normalized = normalized.replace("internal server error", " ")
    normalized = normalized.replace("after an unhandled exception", " ")
    normalized = normalized.replace("after unhandled exception", " ")
    return " ".join(normalized.split())


def _load() -> tuple[dict, dict]:
    with HELDOUT_PATH.open() as f:
        heldout = json.load(f)
    with EVAL_PATH.open() as f:
        eval_data = json.load(f)
    return heldout, eval_data


def test_heldout_dataset_counts() -> None:
    heldout, _ = _load()
    queries = heldout["queries"]
    assert len(queries) == 19, f"Expected 19 held-out queries, got {len(queries)}"

    unanswerable = [q for q in queries if not q["relevant_doc_ids"]]
    answerable = [q for q in queries if q["relevant_doc_ids"]]
    assert len(unanswerable) == 16, (
        f"Expected 16 unanswerable queries, got {len(unanswerable)}"
    )
    assert len(answerable) == 3, f"Expected 3 answerable queries, got {len(answerable)}"


def test_dataset_is_documented_as_partially_overlapping() -> None:
    heldout, _ = _load()
    description = heldout.get("description", "")
    assert "PARTIALLY OVERLAPS" in description, (
        "Held-out dataset description must state that it partially overlaps "
        "retrieval_eval.json"
    )


def test_exactly_three_documented_overlaps() -> None:
    heldout, eval_data = _load()
    eval_by_norm = {_normalize_query_text(q["query"]): q for q in eval_data["queries"]}

    overlaps: list[tuple[str, str]] = []
    for hq in heldout["queries"]:
        matched = eval_by_norm.get(_normalize_query_text(hq["query"]))
        if matched is not None:
            overlaps.append((hq["id"], matched["id"]))
            # Matching normalized query text.
            assert _normalize_query_text(hq["query"]) == _normalize_query_text(
                matched["query"]
            )
            # Matching relevant document labels.
            assert list(hq["relevant_doc_ids"]) == list(matched["relevant_doc_ids"]), (
                f"Relevant doc label mismatch for {hq['id']} -> {matched['id']}"
            )
            assert hq["relevant_doc_ids"], f"No relevant doc labels for {hq['id']}"

    actual = {hq_id: eval_id for hq_id, eval_id in overlaps}
    assert len(overlaps) == 3, (
        f"Expected exactly 3 overlapping queries, got {len(overlaps)}: {overlaps}"
    )
    assert actual == EXPECTED_OVERLAPS, (
        f"Expected overlap pairs {EXPECTED_OVERLAPS}, got {actual}"
    )
    assert set(actual) == {"hq16b", "hq17", "hq18"}


def test_no_unexpected_overlap_beyond_documented_pairs() -> None:
    heldout, eval_data = _load()
    documented = set(EXPECTED_OVERLAPS.items())
    found = set()
    for hq in heldout["queries"]:
        for eq in eval_data["queries"]:
            if _normalize_query_text(hq["query"]) == _normalize_query_text(eq["query"]):
                found.add((hq["id"], eq["id"]))
    assert found == documented, (
        f"Overlap set changed: expected {documented}, found {found}"
    )