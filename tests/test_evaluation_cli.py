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


def test_unknown_subcommand_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus", "--suite", "reliability", "--output", str(tmp_path)])
    assert excinfo.value.code == 2
