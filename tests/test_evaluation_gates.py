"""Gate semantics: ranking, integrity and abstention are separate decisions.

These tests pin the contract that Phase 23 introduced. The previous behaviour -
one exit code for "any case failed" - mixed three different questions and made a
no-evidence query returning candidates look like a ranking regression.
"""

from __future__ import annotations

import pytest

from backend.app.evaluation import cli
from backend.app.evaluation.abstention_evaluator import abstention_metrics
from backend.app.evaluation.models import CaseResult
from backend.app.evaluation.retrieval_evaluator import (
    RetrievalEvaluationError,
    build_retrieval_runtime,
    retrieval_gates,
)


def _case(
    case_id: str,
    *,
    method: str = "hybrid",
    ranking_ok: bool = True,
    abstention: bool | None = None,
    abstention_ok: bool = True,
    integrity_ok: bool = True,
) -> CaseResult:
    metrics = {
        "method": method,
        "ranking_scored": abstention is None,
        "ranking_ok": ranking_ok,
        "abstention_checked": abstention is not None,
        "abstention_ok": abstention_ok,
        "integrity_ok": integrity_ok,
    }
    return CaseResult(
        case_id=case_id,
        suite="retrieval",
        passed=ranking_ok and abstention_ok and integrity_ok,
        metrics=metrics,
    )


def _metrics(**overrides):
    base = {
        "empty_collection_ok": True,
        "metadata_filter_checks": 2,
        "metadata_filter_passes": 2,
    }
    base.update(overrides)
    return base


# 1. Normal labelled queries: ranking passes, nothing else is implicated.
def test_labelled_queries_rank_and_pass_ranking_gate():
    cases = [
        _case("q1:hybrid"),
        _case("q2:hybrid"),
        _case("q3:bm25", method="bm25"),
    ]
    gates = retrieval_gates(_metrics(), cases, mode="local")

    assert gates["ranking"]["status"] == "pass"
    assert gates["ranking"]["passed"] == 3
    assert gates["ranking"]["total"] == 3
    assert gates["abstention"]["status"] == "not_applicable"
    assert gates["integrity"]["status"] == "pass"


# 2. No-evidence queries returning candidates is an abstention finding only.
def test_no_evidence_queries_fail_abstention_but_not_ranking():
    cases = [
        _case("q1:hybrid"),
        _case("neg1:hybrid", abstention=True, abstention_ok=False),
        _case("neg2:hybrid", abstention=True, abstention_ok=False),
    ]
    gates = retrieval_gates(_metrics(), cases, mode="local")

    assert gates["ranking"]["status"] == "pass"
    assert gates["abstention"]["status"] == "fail"
    assert gates["abstention"]["passed"] == 0
    assert gates["abstention"]["total"] == 2
    assert gates["abstention"]["blocking"] is False
    assert gates["integrity"]["status"] == "pass"


# 3. A mixed corpus keeps the two populations separate.
def test_mixed_dataset_scores_ranking_and_abstention_independently():
    cases = [
        _case("q1:semantic", method="semantic", ranking_ok=False),
        _case("q2:semantic", method="semantic"),
        _case("neg1:semantic", method="semantic", abstention=True, abstention_ok=True),
    ]
    gates = retrieval_gates(_metrics(), cases, mode="offline")

    # Offline semantic ranking is advisory: one miss does not block CI.
    assert gates["ranking"]["status"] == "pass"
    assert gates["ranking"]["advisory_failure_methods"] == ["semantic"]
    assert gates["ranking"]["blocking_failure_methods"] == []
    assert gates["ranking"]["total"] == 2
    assert gates["abstention"]["status"] == "pass"


# 4. An empty result set satisfies abstention and must still fail ranking.
def test_empty_corpus_cannot_pass_by_refusing_everything():
    cases = [_case(f"q{i}:hybrid", ranking_ok=False) for i in range(4)]
    cases.append(_case("neg1:hybrid", abstention=True, abstention_ok=True))
    gates = retrieval_gates(_metrics(), cases, mode="local")

    assert gates["abstention"]["status"] == "pass"
    assert gates["ranking"]["status"] == "fail"
    assert gates["ranking"]["blocking_failure_methods"] == ["hybrid"]


# 5. A genuine ranking regression blocks the run.
def test_genuine_ranking_regression_is_blocking():
    cases = [_case("q1:bm25", method="bm25", ranking_ok=False), _case("q2:bm25", method="bm25")]
    gates = retrieval_gates(_metrics(), cases, mode="offline")

    assert gates["ranking"]["status"] == "fail"
    assert gates["ranking"]["blocking_failure_methods"] == ["bm25"]
    assert gates["ranking"]["methods"]["hybrid"]["status"] == "not_applicable"


# 6. A broken benchmark configuration fails integrity rather than ranking.
def test_invalid_benchmark_configuration_fails_integrity():
    cases = [_case("q1:hybrid")]
    gates = retrieval_gates(
        _metrics(metadata_filter_checks=3, metadata_filter_passes=1),
        cases,
        mode="local",
    )
    assert gates["integrity"]["status"] == "fail"
    assert gates["integrity"]["blocking"] is True
    assert gates["ranking"]["status"] == "pass"

    duplicate = [_case("q1:hybrid", integrity_ok=False)]
    gates = retrieval_gates(_metrics(), duplicate, mode="local")
    assert gates["integrity"]["status"] == "fail"
    assert gates["integrity"]["passed"] == 0


def test_unsupported_retrieval_mode_raises_rather_than_silently_passing():
    with pytest.raises(RetrievalEvaluationError):
        build_retrieval_runtime("live")


# 7. CI exit-code behaviour.
def _retrieval_payload(gates: dict) -> dict:
    failed = sum(
        1
        for gate in gates.values()
        if gate.get("status") == "fail"
    )
    return {
        "suite": "retrieval",
        "mode": "offline",
        "cases": int(gates["integrity"]["total"]),
        "passed": 0,
        "failed": failed,
        "pass_rate": 0.0,
        "gates": gates,
        "methods": {},
    }


def _run_with(monkeypatch, tmp_path, gates, argv):
    monkeypatch.setattr(cli, "_run_retrieval_suite", lambda mode: _retrieval_payload(gates))
    return cli.main(["run", "--suite", "retrieval", "--output", str(tmp_path), *argv])


def test_ci_exits_zero_when_only_abstention_is_advisory(monkeypatch, tmp_path, capsys):
    gates = retrieval_gates(
        _metrics(),
        [_case("q1:hybrid"), _case("neg1:hybrid", abstention=True, abstention_ok=False)],
        mode="local",
    )
    code = _run_with(monkeypatch, tmp_path, gates, [])

    assert code == 0
    assert "advisory" in capsys.readouterr().err


def test_ci_exits_one_when_abstention_is_made_blocking(monkeypatch, tmp_path):
    gates = retrieval_gates(
        _metrics(),
        [_case("q1:hybrid"), _case("neg1:hybrid", abstention=True, abstention_ok=False)],
        mode="local",
    )
    code = _run_with(monkeypatch, tmp_path, gates, ["--require-abstention"])

    assert code == 1


def test_ci_exits_one_on_ranking_regression(monkeypatch, tmp_path):
    gates = retrieval_gates(
        _metrics(),
        [_case("q1:hybrid", ranking_ok=False)],
        mode="local",
    )
    code = _run_with(monkeypatch, tmp_path, gates, [])

    assert code == 1


# 8. Abstention metric maths.
def test_abstention_metrics_are_a_confusion_matrix():
    # 2 unanswerable queries, one correctly abstained; 2 answerable, one refused.
    metrics = abstention_metrics(
        expected_abstain=[True, True, False, False],
        predicted_abstain=[True, False, True, False],
    )

    assert metrics["true_positive"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["abstention_precision"] == pytest.approx(0.5)
    assert metrics["abstention_recall"] == pytest.approx(0.5)
    assert metrics["abstention_f1"] == pytest.approx(0.5)
    assert metrics["false_answer_rate"] == pytest.approx(0.5)
    assert metrics["false_abstention_rate"] == pytest.approx(0.5)
    assert metrics["abstention_accuracy"] == pytest.approx(0.5)


def test_abstention_metrics_handle_the_degenerate_cases():
    never = abstention_metrics(
        expected_abstain=[True, False], predicted_abstain=[False, False]
    )
    assert never["abstention_precision"] == 0.0
    assert never["abstention_recall"] == 0.0
    assert never["abstention_f1"] == 0.0
    assert never["false_answer_rate"] == 1.0

    always = abstention_metrics(
        expected_abstain=[True, False], predicted_abstain=[True, True]
    )
    assert always["abstention_recall"] == 1.0
    assert always["false_abstention_rate"] == 1.0


def test_abstention_metrics_reject_mismatched_lengths():
    with pytest.raises(ValueError):
        abstention_metrics(expected_abstain=[True], predicted_abstain=[True, False])
