"""Abstention is measured at four independent stages, not one blended score.

Phase 22's ``retrieval abstention clean rate = 0.0`` and end-to-end
``abstention accuracy = 1.0`` were both correct because they answered different
questions. These tests pin the separation so a future change cannot quietly fold
no-evidence retrieval into a ranking metric or compare an agent decision with a
retrieval result count.
"""

from __future__ import annotations

import re

from backend.app.evaluation.abstention_evaluator import (
    STAGE_AGENT_ABSTENTION,
    STAGE_CANDIDATE_PRESENCE,
    STAGE_EVIDENCE_RELEVANCE,
    STAGE_REPORT_OUTCOME,
    evaluate_abstention,
)
from backend.app.evaluation.agent_evaluator import evaluate_agent
from backend.app.evaluation.dataset import load_retrieval, load_retrieval_negatives
from backend.app.evaluation.report_evaluator import evaluate_reports

EXPECTED_CATEGORIES = {
    "missing_incident_context",
    "unrelated_technical",
    "missing_log_evidence",
    "unsupported_infrastructure_claim",
    "contradictory_evidence",
    "outside_knowledge_base",
}


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def test_negative_dataset_is_large_and_covers_every_category() -> None:
    dataset = load_retrieval_negatives()

    assert dataset.version == "retrieval_negatives_v1"
    # Phase 22 had two no-evidence queries, which cannot support a threshold
    # claim. The expanded benchmark deliberately exceeds that by an order of
    # magnitude of usefulness, while still being small enough to audit by hand.
    assert len(dataset.queries) == 16
    assert {query.category for query in dataset.queries} == EXPECTED_CATEGORIES

    counts: dict[str, int] = {}
    for query in dataset.queries:
        counts[query.category] = counts.get(query.category, 0) + 1
    assert min(counts.values()) >= 2
    assert len({query.id for query in dataset.queries}) == len(dataset.queries)

    # Every label is auditable and the generation method is documented.
    assert dataset.methodology
    assert dataset.methodology.get("generation")
    assert all(query.notes.strip() for query in dataset.queries)


def test_negative_queries_do_not_duplicate_answerable_queries() -> None:
    positives = {_normalise(query.query) for query in load_retrieval().queries}
    negatives = {
        _normalise(query.query) for query in load_retrieval_negatives().queries
    }

    assert positives.isdisjoint(negatives)


def test_retrieval_candidate_presence_stage_uses_its_own_denominator() -> None:
    result = evaluate_abstention(mode="offline")
    metrics = result.metrics

    assert metrics["stage"] == STAGE_CANDIDATE_PRESENCE
    assert set(metrics["stage_owners"]) == {
        STAGE_CANDIDATE_PRESENCE,
        STAGE_EVIDENCE_RELEVANCE,
        STAGE_AGENT_ABSTENTION,
        STAGE_REPORT_OUTCOME,
    }
    assert metrics["labeled_queries"] == len(load_retrieval().queries)
    assert metrics["no_evidence_queries"] == len(load_retrieval_negatives().queries)
    assert metrics["empty_corpus_abstains"] is True

    for name in ("semantic", "bm25", "hybrid"):
        row = metrics["methods"][name]
        # Confusion-matrix denominators prove no-evidence queries stayed out of
        # the answerable population and vice versa.
        assert row["negatives"] == metrics["no_evidence_queries"]
        assert row["positives"] == metrics["labeled_queries"]
        assert row["total"] == (
            metrics["no_evidence_queries"] + metrics["labeled_queries"]
        )
        assert 0.0 <= row["false_answer_rate"] <= 1.0
        assert 0.0 <= row["false_abstention_rate"] <= 1.0


def test_offline_threshold_sweep_is_experimental_only() -> None:
    result = evaluate_abstention(mode="offline")
    metrics = result.metrics

    assert metrics["threshold_sweep_interpretable"] is False
    assert metrics["recommended_threshold"] is None
    sweep = metrics["threshold_sweeps"]["semantic"]
    assert len(sweep) == len(metrics["thresholds_evaluated"])
    assert all(0.0 <= row["abstention_accuracy"] <= 1.0 for row in sweep)
    assert all(
        {"threshold", "abstention_precision", "abstention_recall"} <= set(row)
        for row in sweep
    )
    # A best-F1 point in a lexical stand-in must not be promoted implicitly.
    assert metrics["recommended_threshold"] is None


def test_agent_abstention_is_measured_separately_from_retrieval() -> None:
    agent = evaluate_agent(mode="offline")
    metrics = agent.metrics

    # The agent suite decides whether the workflow refused a conclusion. It is
    # not derived from the retrieval engine's candidate count.
    assert metrics["abstention_counts"]["total"] == len(agent.cases)
    assert 0.0 <= metrics["abstention_precision"] <= 1.0
    assert 0.0 <= metrics["abstention_recall"] <= 1.0
    assert 0.0 <= metrics["abstention_accuracy"] <= 1.0
    assert any(case.metrics.get("abstained") for case in agent.cases)


def test_report_outcome_is_measured_separately_from_agent_abstention() -> None:
    reports = evaluate_reports()
    metrics = reports.metrics

    # Report scoring judges the persisted outcome against its label. It does not
    # reuse the agent decision or the retrieval result count.
    assert metrics["outcome_accuracy"] == 1.0
    assert metrics["outcome_counts"]["insufficient_evidence"] >= 1
    assert metrics["cases"] == len(reports.cases)
