"""Deterministic metric calculations used by every evaluation suite."""

from __future__ import annotations

import math
from typing import Any, Iterable


def unique_preserving_order(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def recall_at_k(retrieved: Iterable[Any], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = set(unique_preserving_order(retrieved)[: max(0, k)])
    return len(top & relevant) / len(relevant)


def precision_at_k(retrieved: Iterable[Any], relevant: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top = unique_preserving_order(retrieved)[:k]
    if not top:
        return 0.0
    return len(set(top) & relevant) / k


def reciprocal_rank_at_k(retrieved: Iterable[Any], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    for rank, value in enumerate(unique_preserving_order(retrieved)[:k], start=1):
        if value in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Iterable[Any], relevant: set[str], k: int) -> float:
    if not relevant or k <= 0:
        return 0.0
    top = unique_preserving_order(retrieved)[:k]
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, value in enumerate(top, start=1)
        if value in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def average(values: Iterable[float]) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def finding_counts(expected: set[str], actual: set[str]) -> dict[str, int]:
    return {
        "true_positive": len(expected & actual),
        "false_positive": len(actual - expected),
        "false_negative": len(expected - actual),
        "true_negative": 0,  # Finding sets have no meaningful negative class.
    }


def precision_recall_f1(counts: dict[str, int]) -> dict[str, float]:
    tp = counts.get("true_positive", 0)
    fp = counts.get("false_positive", 0)
    fn = counts.get("false_negative", 0)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def abstention_counts(expected: Iterable[bool], actual: Iterable[bool]) -> dict[str, int]:
    pairs = list(zip(expected, actual, strict=True))
    return {
        "true_positive": sum(1 for exp, act in pairs if exp and act),
        "false_positive": sum(1 for exp, act in pairs if not exp and act),
        "false_negative": sum(1 for exp, act in pairs if exp and not act),
        "true_negative": sum(1 for exp, act in pairs if not exp and not act),
        "total": len(pairs),
    }


def accuracy(expected: Iterable[Any], actual: Iterable[Any]) -> float:
    pairs = list(zip(expected, actual, strict=True))
    return sum(1 for exp, act in pairs if exp == act) / len(pairs) if pairs else 0.0


def pass_rate(results: Iterable[Any]) -> float:
    rows = list(results)
    return sum(1 for row in rows if getattr(row, "passed", False)) / len(rows) if rows else 0.0


def aggregate_ranking(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate only queries that have labeled relevant documents."""
    labeled = [row for row in rows if row.get("has_relevant")]
    keys = [
        "recall@3",
        "recall@5",
        "precision@3",
        "precision@5",
        "mrr@5",
        "ndcg@3",
        "ndcg@5",
    ]
    return {key: average(float(row.get(key, 0.0)) for row in labeled) for key in keys} | {
        "labeled_queries": float(len(labeled)),
        "total_queries": float(len(rows)),
    }
