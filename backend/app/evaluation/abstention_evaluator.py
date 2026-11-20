"""Stage-explicit abstention evaluation for the OpsPilot retrieval layer.

Why this module exists
----------------------
Phase 22 reported two numbers that looked contradictory:

* retrieval ``abstention_clean_rate`` = 0.0
* end-to-end ``abstention_accuracy`` = 1.0

They measure different stages and are both correct:

1. ``retrieval_candidate_presence`` - does the vector/BM25/RRF layer return any
   candidate at all? An approximate index returns *something* for every query,
   so this is 0 on no-evidence queries unless a score threshold is applied.
2. ``evidence_relevance`` - are the returned candidates actually relevant? This
   is what recall/precision/nDCG measure, and it is only defined for labeled
   queries.
3. ``agent_abstention`` - does the agent/report layer refuse to state a root
   cause when the evidence does not support one? Measured in the agent suite.
4. ``report_outcome`` - is the persisted report outcome correct? Measured in the
   report suite.

Only stage 1 is measured here. Stages 3 and 4 are reported by their own suites,
and this module records the separation explicitly so no one reconciles the
numbers by averaging them together.

No threshold is baked into the product: this module reports a sweep of candidate
thresholds and the trade-off at each, and recommends whether more labelled data
is required before anything is promoted to production.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Sequence

from .dataset import load_retrieval, load_retrieval_negatives
from .models import CaseResult, SuiteResult
from .retrieval_evaluator import DEFAULT_K, build_retrieval_runtime

# Names used consistently across the suites and the documentation.
STAGE_CANDIDATE_PRESENCE = "retrieval_candidate_presence"
STAGE_EVIDENCE_RELEVANCE = "evidence_relevance"
STAGE_AGENT_ABSTENTION = "agent_abstention"
STAGE_REPORT_OUTCOME = "report_outcome"

STAGE_OWNERS = {
    STAGE_CANDIDATE_PRESENCE: "abstention suite (this module)",
    STAGE_EVIDENCE_RELEVANCE: "retrieval suite ranking gate",
    STAGE_AGENT_ABSTENTION: "agent suite abstention_counts",
    STAGE_REPORT_OUTCOME: "report suite outcome_accuracy",
}

DEFAULT_THRESHOLDS: tuple[float, ...] = tuple(
    round(0.10 + 0.05 * step, 2) for step in range(17)
)  # 0.10 .. 0.90


def abstention_metrics(
    expected_abstain: Iterable[bool], predicted_abstain: Iterable[bool]
) -> dict[str, Any]:
    """Confusion-matrix metrics for a binary abstention decision.

    ``expected_abstain`` is the dataset label; ``predicted_abstain`` is the
    system's decision. A false answer is a negative query that produced an
    answer; a false abstention is an answerable query the system refused.
    """
    pairs = list(zip(expected_abstain, predicted_abstain, strict=True))
    true_positive = sum(1 for exp, pred in pairs if exp and pred)
    false_positive = sum(1 for exp, pred in pairs if not exp and pred)
    false_negative = sum(1 for exp, pred in pairs if exp and not pred)
    true_negative = sum(1 for exp, pred in pairs if not exp and not pred)
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    f1 = (
        2 * precision * recall / (precision + recall) if precision + recall else 0.0
    )
    negatives = true_positive + false_negative
    positives = true_negative + false_positive
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "total": len(pairs),
        "negatives": negatives,
        "positives": positives,
        "abstention_precision": precision,
        "abstention_recall": recall,
        "abstention_f1": f1,
        # Fraction of unanswerable queries that still received an answer.
        "false_answer_rate": false_negative / negatives if negatives else 0.0,
        # Fraction of answerable queries that were wrongly refused.
        "false_abstention_rate": false_positive / positives if positives else 0.0,
        "abstention_accuracy": (
            (true_positive + true_negative) / len(pairs) if pairs else 0.0
        ),
    }


def threshold_sweep(
    positive_scores: Sequence[float | None],
    negative_scores: Sequence[float | None],
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> list[dict[str, Any]]:
    """Abstain when ``top_score < threshold``; report the trade-off at each point.

    A query with no candidates at all is always an abstention, regardless of
    threshold, which keeps the sweep consistent with the candidate-presence
    operating point.
    """
    rows: list[dict[str, Any]] = []
    for threshold in thresholds:
        expected: list[bool] = [False] * len(positive_scores) + [True] * len(
            negative_scores
        )
        predicted: list[bool] = []
        for score in list(positive_scores) + list(negative_scores):
            predicted.append(score is None or float(score) < threshold)
        metrics = abstention_metrics(expected, predicted)
        rows.append({"threshold": float(threshold), **metrics})
    return rows


def _top_score(result: Any) -> float | None:
    if isinstance(result, dict):
        value = result.get("score")
    else:
        value = getattr(result, "score", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _probe(search: Any, query: str, k: int = DEFAULT_K) -> tuple[int, float | None]:
    results = list(search(query, top_k=k) or [])
    if not results:
        return 0, None
    scores = [score for score in (_top_score(item) for item in results) if score is not None]
    return len(results), (max(scores) if scores else None)


def evaluate_abstention(
    *,
    mode: str = "offline",
    runtime: Any = None,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    sweep_semantic_thresholds: bool = True,
) -> SuiteResult:
    """Measure abstention at the retrieval candidate-presence stage."""
    started = time.perf_counter()
    positives = load_retrieval()
    negatives = load_retrieval_negatives()
    owns_runtime = runtime is None
    runtime = runtime or build_retrieval_runtime(mode)
    try:
        engines = {
            "semantic": runtime.semantic.search,
            "bm25": runtime.bm25.search,
            "hybrid": runtime.hybrid.search,
        }
        cases: list[CaseResult] = []
        method_metrics: dict[str, Any] = {}
        sweeps: dict[str, Any] = {}
        for name, search in engines.items():
            positive_scores: list[float | None] = []
            negative_scores: list[float | None] = []
            expected: list[bool] = []
            predicted_candidates: list[bool] = []
            category_failures: dict[str, list[str]] = {}

            for query in positives.queries:
                count, score = _probe(search, query.query)
                positive_scores.append(score)
                expected.append(False)
                predicted_candidates.append(count == 0)
                cases.append(
                    CaseResult(
                        case_id=f"pos:{query.id}:{name}",
                        suite="abstention",
                        passed=count > 0,
                        metrics={
                            "method": name,
                            "query_id": query.id,
                            "expected_abstain": False,
                            "predicted_abstain": count == 0,
                            "result_count": count,
                            "top_score": score,
                        },
                        failures=[] if count else ["answerable query returned no candidates"],
                    )
                )

            for query in negatives.queries:
                count, score = _probe(search, query.query)
                negative_scores.append(score)
                expected.append(True)
                predicted_candidates.append(count == 0)
                if count:
                    category_failures.setdefault(query.category, []).append(query.id)
                cases.append(
                    CaseResult(
                        case_id=f"neg:{query.id}:{name}",
                        suite="abstention",
                        passed=count == 0,
                        metrics={
                            "method": name,
                            "query_id": query.id,
                            "category": query.category,
                            "expected_abstain": True,
                            "predicted_abstain": count == 0,
                            "result_count": count,
                            "top_score": score,
                        },
                        failures=(
                            ["no-evidence query returned candidates"] if count else []
                        ),
                    )
                )

            metrics = abstention_metrics(expected, predicted_candidates)
            metrics["candidate_presence_negatives"] = sum(
                1 for count in predicted_candidates[len(positives.queries) :] if not count
            )
            metrics["false_answer_categories"] = {
                category: ids for category, ids in sorted(category_failures.items())
            }
            method_metrics[name] = metrics

            if name == "semantic" and sweep_semantic_thresholds:
                sweeps[name] = threshold_sweep(
                    positive_scores, negative_scores, thresholds
                )

        # A threshold sweep is only interpretable on the real embedding model;
        # offline semantic scores come from a lexical hashing stand-in, so no
        # threshold is recommended from them even if one scores well.
        recommended = (
            _recommend_threshold(sweeps.get("semantic", []))
            if mode == "local"
            else None
        )
        empty_corpus_abstains = _empty_corpus_abstains()

        return SuiteResult(
            suite="abstention",
            mode=mode,
            dataset_version=str(negatives.version),
            cases=cases,
            metrics={
                "stage": STAGE_CANDIDATE_PRESENCE,
                "stage_owners": STAGE_OWNERS,
                "predicted_abstain_definition": (
                    "the retrieval engine returned zero candidates; no score "
                    "threshold is applied at the operating point"
                ),
                "labeled_queries": len(positives.queries),
                "no_evidence_queries": len(negatives.queries),
                "category_counts": _category_counts(negatives),
                "methods": method_metrics,
                "threshold_sweeps": sweeps,
                "threshold_sweep_interpretable": mode == "local",
                "recommended_threshold": recommended,
                "empty_corpus_abstains": empty_corpus_abstains,
                "thresholds_evaluated": list(thresholds),
            },
            limitations=[
                "Only the retrieval candidate-presence stage is measured; agent "
                "and report abstention are measured by the agent and report suites.",
                f"Only {len(negatives.queries)} negative queries; a threshold "
                "chosen from them would not generalize and is not applied in production.",
                "Labels are single-annotator judgements against an eight-document corpus.",
            ],
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    finally:
        if owns_runtime:
            runtime.close()


def _recommend_threshold(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the best-F1 sweep row, but never as a production recommendation."""
    usable = [row for row in rows if row.get("negatives") and row.get("positives")]
    if not usable:
        return None
    best = max(usable, key=lambda row: (row["abstention_f1"], row["threshold"]))
    return {
        "threshold": best["threshold"],
        "abstention_f1": best["abstention_f1"],
        "abstention_precision": best["abstention_precision"],
        "abstention_recall": best["abstention_recall"],
        "false_answer_rate": best["false_answer_rate"],
        "false_abstention_rate": best["false_abstention_rate"],
        "status": "experimental_only",
    }


def _category_counts(negatives: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for query in negatives.queries:
        counts[query.category] = counts.get(query.category, 0) + 1
    return dict(sorted(counts.items()))


def _empty_corpus_abstains() -> bool:
    """All engines must return nothing for every query on an empty corpus."""
    from backend.app.retrieval import (
        BM25SearchEngine,
        HybridSearchEngine,
        SemanticSearchEngine,
    )

    from .offline import FixtureVectorStore, LexicalEmbeddingModel

    store = FixtureVectorStore()
    model = LexicalEmbeddingModel()
    semantic = SemanticSearchEngine(store, model=model, expected_dimension=384)
    bm25 = BM25SearchEngine(store)
    bm25.build_index()
    hybrid = HybridSearchEngine(semantic_engine=semantic, bm25_engine=bm25)
    probes = ("anything", "database connection pool exhausted", "HTTP 500 error")
    return all(
        engine.search(text, top_k=3) == []
        for engine in (semantic, bm25, hybrid)
        for text in probes
    )


def summarize_abstention(metrics: dict[str, Any]) -> str:
    """One-line summary used by the CLI report."""
    hybrid = (metrics.get("methods") or {}).get("hybrid", {})
    return (
        "stage={stage} negatives={neg} false_answer_rate={fa:.4f} "
        "false_abstention_rate={fb:.4f} accuracy={acc:.4f}"
    ).format(
        stage=metrics.get("stage"),
        neg=metrics.get("no_evidence_queries"),
        fa=float(hybrid.get("false_answer_rate", 0.0)),
        fb=float(hybrid.get("false_abstention_rate", 0.0)),
        acc=float(hybrid.get("abstention_accuracy", 0.0)),
    )
