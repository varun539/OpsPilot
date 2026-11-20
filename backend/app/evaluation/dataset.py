"""Loading and validation for versioned evaluation datasets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .models import (
    DiagnosticCase,
    IncidentCase,
    NegativeDataset,
    PlannerScenario,
    ReportCase,
    RetrievalDataset,
)

DATASET_DIR = Path(__file__).resolve().parent / "datasets"
INCIDENTS_PATH = DATASET_DIR / "incidents_v1.jsonl"
DIAGNOSTICS_PATH = DATASET_DIR / "diagnostics_v1.jsonl"
RETRIEVAL_PATH = DATASET_DIR / "retrieval_v1.json"
NEGATIVES_PATH = DATASET_DIR / "retrieval_negatives_v1.json"
PLANNER_PATH = DATASET_DIR / "planner_v1.jsonl"
REPORTS_PATH = DATASET_DIR / "reports_v1.jsonl"
INCIDENTS_VERSION = "incidents_v1"
DIAGNOSTICS_VERSION = "diagnostics_v1"
PLANNER_VERSION = "planner_v1"
REPORTS_VERSION = "reports_v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_BASE_DIR = REPO_ROOT / "data" / "knowledge_base"


class DatasetError(ValueError):
    """Raised when a dataset is missing, malformed or internally inconsistent."""


T = TypeVar("T", bound=BaseModel)


def _load_jsonl(path: Path, model: type[T], expected_version: str) -> list[T]:
    if not path.is_file():
        raise DatasetError(f"evaluation dataset not found: {path}")
    records: list[T] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path.name}:{line_number}: invalid JSON: {exc.msg}") from exc
        try:
            record = model.model_validate(payload)
        except ValidationError as exc:
            raise DatasetError(
                f"{path.name}:{line_number}: invalid evaluation case: {exc}"
            ) from exc
        if record.dataset_version != expected_version:
            raise DatasetError(
                f"{path.name}:{line_number}: expected dataset_version "
                f"{expected_version!r}, got {record.dataset_version!r}"
            )
        case_id = getattr(record, "case_id")
        if case_id in seen:
            raise DatasetError(f"{path.name}:{line_number}: duplicate case id {case_id!r}")
        seen.add(case_id)
        records.append(record)
    if not records:
        raise DatasetError(f"evaluation dataset is empty: {path}")
    return records


def available_doc_ids(knowledge_base_dir: Path | str | None = None) -> set[str]:
    """Extract KB document IDs (e.g. "KB-001") from knowledge base file names."""
    directory = Path(knowledge_base_dir) if knowledge_base_dir is not None else KNOWLEDGE_BASE_DIR
    if not directory.is_dir():
        return set()
    ids: set[str] = set()
    pattern = re.compile(r"^KB-\d{3}")
    for path in directory.glob("KB-*.md"):
        m = pattern.match(path.name)
        if m:
            ids.add(m.group(0))
    return ids


def validate_incident_references(
    cases: list[IncidentCase], *, known_doc_ids: set[str] | None = None
) -> None:
    known = available_doc_ids() if known_doc_ids is None else set(known_doc_ids)
    if not known:
        return
    for case in cases:
        missing = sorted(set(case.expected.relevant_doc_ids) - known)
        if missing:
            raise DatasetError(
                f"{case.case_id}: expected documents do not exist: {missing}"
            )


def load_incidents(
    path: Path | str | None = None, *, validate_references: bool = True
) -> list[IncidentCase]:
    cases = _load_jsonl(
        Path(path) if path is not None else INCIDENTS_PATH,
        IncidentCase,
        INCIDENTS_VERSION,
    )
    if validate_references:
        validate_incident_references(cases)
    return cases


def load_diagnostics(path: Path | str | None = None) -> list[DiagnosticCase]:
    return _load_jsonl(
        Path(path) if path is not None else DIAGNOSTICS_PATH,
        DiagnosticCase,
        DIAGNOSTICS_VERSION,
    )


def load_retrieval(path: Path | str | None = None) -> RetrievalDataset:
    dataset_path = Path(path) if path is not None else RETRIEVAL_PATH
    if not dataset_path.is_file():
        raise DatasetError(f"retrieval dataset not found: {dataset_path}")
    try:
        return RetrievalDataset.model_validate_json(
            dataset_path.read_text(encoding="utf-8")
        )
    except (ValidationError, ValueError) as exc:
        raise DatasetError(f"{dataset_path.name}: invalid retrieval dataset: {exc}") from exc


def load_retrieval_negatives(path: Path | str | None = None) -> NegativeDataset:
    dataset_path = Path(path) if path is not None else NEGATIVES_PATH
    if not dataset_path.is_file():
        raise DatasetError(f"negative-query dataset not found: {dataset_path}")
    try:
        return NegativeDataset.model_validate_json(
            dataset_path.read_text(encoding="utf-8")
        )
    except (ValidationError, ValueError) as exc:
        raise DatasetError(
            f"{dataset_path.name}: invalid negative-query dataset: {exc}"
        ) from exc


def load_planner(path: Path | str | None = None) -> list[PlannerScenario]:
    return _load_jsonl(
        Path(path) if path is not None else PLANNER_PATH,
        PlannerScenario,
        PLANNER_VERSION,
    )


def load_reports(path: Path | str | None = None) -> list[ReportCase]:
    return _load_jsonl(
        Path(path) if path is not None else REPORTS_PATH,
        ReportCase,
        REPORTS_VERSION,
    )


def dataset_counts() -> dict[str, int]:
    return {
        "incidents": len(load_incidents()),
        "diagnostics": len(load_diagnostics()),
        "retrieval": len(load_retrieval().queries),
        "retrieval_negatives": len(load_retrieval_negatives().queries),
        "planner": len(load_planner()),
        "reports": len(load_reports()),
    }
