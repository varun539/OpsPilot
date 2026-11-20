"""Regression tests for the documented evaluation CLI invocation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.evaluation import cli


def test_documented_run_subcommand_is_accepted(tmp_path: Path) -> None:
    """`python -m ...cli run --suite ...` is the documented form and must work."""
    exit_code = cli.main(
        ["run", "--suite", "reliability", "--mode", "offline", "--output", str(tmp_path)]
    )
    assert exit_code == 0
    report = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    assert report["summary"]["mode"] == "offline"
    assert "reliability" in report["summary"]


def test_bare_invocation_without_subcommand_still_works(tmp_path: Path) -> None:
    exit_code = cli.main(
        ["--suite", "reliability", "--mode", "offline", "--output", str(tmp_path)]
    )
    assert exit_code == 0
    assert (tmp_path / "evaluation.json").is_file()


def test_planner_and_report_suites_are_exposed(tmp_path: Path) -> None:
    assert cli.main(["run", "--suite", "planner", "--output", str(tmp_path / "p")]) == 0
    assert cli.main(["run", "--suite", "report", "--output", str(tmp_path / "r")]) == 0

    planner = json.loads((tmp_path / "p" / "evaluation.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "r" / "evaluation.json").read_text(encoding="utf-8"))
    assert planner["summary"]["planner"]["unauthorized_tool_execution_count"] == 0
    assert report["summary"]["report"]["outcome_accuracy"] == 1.0


def test_retrieval_report_surfaces_every_method_metric(tmp_path: Path) -> None:
    """The generated artifact must not drop precision, nDCG, MRR, diversity or
    the abstention group; Step 3 requires them to be reportable."""
    cli.main(["run", "--suite", "retrieval", "--mode", "offline", "--output", str(tmp_path)])
    report = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    retrieval = report["summary"]["retrieval"]

    assert set(retrieval["methods"]) == {"semantic", "bm25", "hybrid"}
    for method in retrieval["methods"].values():
        for key in (
            "recall@5",
            "precision@5",
            "ndcg@5",
            "mrr@5",
            "relevant_doc_coverage@5",
            "distinct_documents@5",
            "max_chunks_per_document@5",
            "chunk_concentration@5",
            "abstention_queries",
            "abstention_returned_results",
            "abstention_clean_rate",
        ):
            assert key in method

    markdown = (tmp_path / "evaluation.md").read_text(encoding="utf-8")
    assert "Retrieval metrics by method" in markdown
    assert "Abstention group" in markdown


def test_unknown_subcommand_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus", "--suite", "reliability", "--output", str(tmp_path)])
    assert excinfo.value.code == 2
