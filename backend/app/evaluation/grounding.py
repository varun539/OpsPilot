"""Deterministic evidence-grounding and citation metrics.

Reference existence alone is not treated as semantic correctness: labels from
the evaluation case are used to decide whether a cited document is relevant.
"""

from __future__ import annotations

from typing import Any

from backend.app.rag.citation_validator import CitationValidator
from backend.app.rag.models import InvestigationReport, RetrievedEvidence


def _report_payload(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (outer response, report), accepting nested or flattened shapes."""
    if not isinstance(value, dict):
        return {}, {}
    if isinstance(value.get("report"), dict):
        return value, value["report"]
    return value, value


def _evidence(value: Any) -> list[RetrievedEvidence]:
    rows: list[RetrievedEvidence] = []
    for item in value or []:
        if isinstance(item, RetrievedEvidence):
            rows.append(item)
            continue
        if isinstance(item, dict):
            try:
                rows.append(
                    RetrievedEvidence(
                        chunk_id=str(item.get("chunk_id") or ""),
                        doc_id=str(item.get("doc_id") or ""),
                        title=str(item.get("title") or ""),
                        source_path=str(item.get("source_path") or ""),
                        chunk_text=str(item.get("chunk_text") or item.get("text") or ""),
                        metadata=dict(item.get("metadata") or {}),
                    )
                )
            except Exception:
                continue
    return rows


def _cited_ids(report: dict[str, Any]) -> set[str]:
    try:
        model = InvestigationReport.model_validate(report)
    except Exception:
        return set()
    return CitationValidator().extract_chunk_ids(model)


def evaluate_grounding(
    response: Any,
    evidence_items: Any,
    *,
    expected_doc_ids: list[str],
    expected_chunk_ids: list[str],
    expected_category: str,
    expected_abstain: bool,
) -> dict[str, Any]:
    outer, report = _report_payload(response)
    evidence = _evidence(evidence_items)
    allowed_chunks = {item.chunk_id for item in evidence if item.chunk_id}
    chunk_to_doc = {item.chunk_id: item.doc_id for item in evidence}
    cited = _cited_ids(report)
    cited_docs = {
        chunk_to_doc.get(chunk_id, "")
        for chunk_id in cited
        if chunk_to_doc.get(chunk_id)
    }
    expected_docs = set(expected_doc_ids)
    expected_chunks = set(expected_chunk_ids)

    try:
        report_model = InvestigationReport.model_validate(report)
        schema_valid = True
    except Exception:
        report_model = None
        schema_valid = False

    abstained = bool(
        report_model is not None
        and report_model.evidence_status.value == "insufficient"
        and not report_model.root_cause_hypotheses
    )
    category = report_model.incident_category if report_model else ""
    category_correct = category == expected_category if expected_category else None

    citation_precision = (
        sum(1 for chunk_id in cited if chunk_to_doc.get(chunk_id) in expected_docs)
        / len(cited)
        if cited
        else 0.0
    )
    citation_recall = (
        len(cited_docs & expected_docs) / len(expected_docs) if expected_docs else 0.0
    )
    chunk_citation_recall = (
        len(cited & expected_chunks) / len(expected_chunks) if expected_chunks else 0.0
    )
    unknown = sorted(cited - allowed_chunks)
    source_integrity = not unknown
    citation_valid = bool(outer.get("citation_valid", source_integrity))
    valid = bool(outer.get("valid", schema_valid)) and schema_valid

    unsupported_conclusion = False
    if expected_abstain and report_model is not None:
        unsupported_conclusion = bool(report_model.root_cause_hypotheses)
    abstain_condition = (
        expected_abstain
        and report_model is not None
        and report_model.evidence_status.value == "sufficient"
    )
    if abstain_condition:
        unsupported_conclusion = True

    return {
        "schema_valid": schema_valid,
        "report_valid": valid,
        "citation_valid": citation_valid,
        "source_integrity": source_integrity,
        "unknown_citations": unknown,
        "cited_chunk_ids": sorted(cited),
        "cited_doc_ids": sorted(cited_docs),
        "citation_precision": citation_precision,
        "citation_recall": citation_recall,
        "chunk_citation_recall": chunk_citation_recall,
        "category": category,
        "category_correct": category_correct,
        "abstained": abstained,
        "abstention_correct": abstained == expected_abstain,
        "unsupported_conclusion": unsupported_conclusion,
        "evidence_status": (
            report_model.evidence_status.value if report_model else ""
        ),
        "supporting_evidence_count": len(cited),
        "retrieved_document_ids": sorted(
            {item.doc_id for item in evidence if item.doc_id}
        ),
    }


def finding_coverage(
    diagnostic_results: dict[str, Any], expected_codes: list[str]
) -> dict[str, Any]:
    actual: set[str] = set()
    for payload in (diagnostic_results or {}).values():
        if not isinstance(payload, dict):
            continue
        for finding in payload.get("findings") or []:
            if isinstance(finding, dict) and finding.get("code"):
                actual.add(str(finding["code"]))
    expected = set(expected_codes)
    covered = expected & actual
    return {
        "expected_count": len(expected),
        "covered_count": len(covered),
        "coverage": len(covered) / len(expected) if expected else 1.0,
        "missing": sorted(expected - actual),
        "actual": sorted(actual),
    }
