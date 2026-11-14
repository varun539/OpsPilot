"""RAG orchestration service for OpsPilot."""

from __future__ import annotations

from typing import Any

from backend.app.llm.base import LLMProvider

from .citation_validator import CitationValidator
from .context_builder import ContextBuilder
from .models import InvestigationReport, RAGResponse, RetrievedEvidence
from .prompts import SYSTEM_PROMPT, build_user_prompt


class RAGService:
    """Coordinate retrieval, context construction, generation, and validation."""

    def __init__(
        self,
        retrieval_engine: Any,
        llm_provider: LLMProvider,
        *,
        context_builder: ContextBuilder | None = None,
        citation_validator: CitationValidator | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.retrieval_engine = retrieval_engine
        self.llm_provider = llm_provider
        self.context_builder = context_builder or ContextBuilder()
        self.citation_validator = citation_validator or CitationValidator()
        self.system_prompt = system_prompt

    def investigate(
        self,
        question: str,
        *,
        filters: Any = None,
        top_k: int | None = None,
        k: int | None = None,
        n_results: int | None = None,
        limit: int | None = None,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> RAGResponse:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        question = question.strip()

        if top_k is None:
            top_k = k
        if top_k is None:
            top_k = n_results
        if top_k is None:
            top_k = limit
        if top_k is None:
            top_k = 5
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 5
        if top_k < 1:
            top_k = 1

        merged_filters = filters
        if isinstance(merged_filters, dict) and kwargs:
            merged_filters = {**merged_filters, **kwargs}
        elif merged_filters is None and kwargs:
            merged_filters = dict(kwargs)

        try:
            search_kwargs: dict[str, Any] = {"top_k": top_k}
            if merged_filters is not None:
                search_kwargs["filters"] = merged_filters
            results = self.retrieval_engine.search(question, **search_kwargs)
        except Exception as exc:
            return self._error_response(
                question=question,
                status="retrieval_error",
                errors=[f"Retrieval failed: {exc}"],
            )

        context = self.context_builder.build(results)
        evidence_items = context.items

        if not evidence_items:
            report = self._insufficient_report(
                "No retrieved evidence matched the incident question."
            )
            return RAGResponse(
                question=question,
                report=report,
                retrieved_evidence=[],
                source_references=[],
                retrieval_metadata={
                    "top_k": top_k,
                    "filters": merged_filters,
                    "retrieved_count": 0,
                    "context_truncated": context.truncated,
                },
                provider=type(self.llm_provider).__name__,
                model=getattr(self.llm_provider, "model", ""),
                usage=None,
                generation_status="insufficient_evidence",
                valid=True,
                citation_valid=True,
                validation_errors=[],
            )

        user_prompt = build_user_prompt(question, context.formatted_context)
        try:
            llm_result = self.llm_provider.generate_structured(
                system_instructions=self.system_prompt,
                user_input=user_prompt,
                output_schema=InvestigationReport,
                timeout=timeout,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:
            return self._error_response(
                question=question,
                status="provider_error",
                errors=[str(exc)],
                evidence=evidence_items,
                metadata={
                    "top_k": top_k,
                    "filters": merged_filters,
                    "retrieved_count": len(evidence_items),
                    "context_truncated": context.truncated,
                },
            )

        report = llm_result.output
        if not isinstance(report, InvestigationReport):
            try:
                report = InvestigationReport.model_validate(report)
            except Exception as exc:
                return self._error_response(
                    question=question,
                    status="provider_error",
                    errors=[f"Invalid provider output: {exc}"],
                    evidence=evidence_items,
                    metadata={
                        "top_k": top_k,
                        "filters": merged_filters,
                        "retrieved_count": len(evidence_items),
                        "context_truncated": context.truncated,
                    },
                )

        validation = self.citation_validator.validate(report, evidence_items)
        source_references = [
            {
                "chunk_id": item.chunk_id,
                "doc_id": item.doc_id,
                "title": item.title,
                "source_path": item.source_path,
            }
            for item in evidence_items
        ]

        if not validation.valid:
            return RAGResponse(
                question=question,
                report=report,
                retrieved_evidence=evidence_items,
                source_references=source_references,
                retrieval_metadata={
                    "top_k": top_k,
                    "filters": merged_filters,
                    "retrieved_count": len(evidence_items),
                    "context_truncated": context.truncated,
                },
                provider=type(self.llm_provider).__name__,
                model=getattr(llm_result, "model", getattr(self.llm_provider, "model", "")),
                usage=self._usage_to_dict(getattr(llm_result, "usage", None)),
                generation_status="citation_validation_failed",
                valid=False,
                citation_valid=False,
                validation_errors=validation.errors,
            )

        return RAGResponse(
            question=question,
            report=report,
            retrieved_evidence=evidence_items,
            source_references=source_references,
            retrieval_metadata={
                "top_k": top_k,
                "filters": merged_filters,
                "retrieved_count": len(evidence_items),
                "context_truncated": context.truncated,
            },
            provider=type(self.llm_provider).__name__,
            model=getattr(llm_result, "model", getattr(self.llm_provider, "model", "")),
            usage=self._usage_to_dict(getattr(llm_result, "usage", None)),
            generation_status="success",
            valid=True,
            citation_valid=True,
            validation_errors=[],
        )

    @staticmethod
    def _insufficient_report(reason: str) -> InvestigationReport:
        return InvestigationReport(
            summary=reason,
            incident_category="insufficient_evidence",
            affected_components=[],
            evidence_status="insufficient",
            root_cause_hypotheses=[],
            supporting_evidence=[],
            recommended_actions=[],
            missing_information=[
                "Relevant knowledge-base evidence for this incident.",
                "Logs, metrics, timestamps, and affected component details.",
            ],
            limitations=[reason],
        )

    def _error_response(
        self,
        *,
        question: str,
        status: str,
        errors: list[str],
        evidence: list[RetrievedEvidence] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RAGResponse:
        report = self._insufficient_report("; ".join(errors))
        return RAGResponse(
            question=question,
            report=report,
            retrieved_evidence=evidence or [],
            source_references=[
                {
                    "chunk_id": item.chunk_id,
                    "doc_id": item.doc_id,
                    "title": item.title,
                    "source_path": item.source_path,
                }
                for item in (evidence or [])
            ],
            retrieval_metadata=metadata or {},
            provider=type(self.llm_provider).__name__,
            model=getattr(self.llm_provider, "model", ""),
            usage=None,
            generation_status=status,
            valid=False,
            citation_valid=False,
            validation_errors=errors,
        )

    @staticmethod
    def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage
        result: dict[str, Any] = {}
        for field_name in ("prompt_tokens", "completion_tokens", "total_tokens", "raw"):
            value = getattr(usage, field_name, None)
            if value is not None:
                result[field_name] = value
        return result or None

