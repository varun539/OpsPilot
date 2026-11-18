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
                provider=self._provider_name(),
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
                provider=self._provider_name(),
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
            provider=self._provider_name(),
            model=getattr(llm_result, "model", getattr(self.llm_provider, "model", "")),
            usage=self._usage_to_dict(getattr(llm_result, "usage", None)),
            generation_status="success",
            valid=True,
            citation_valid=True,
            validation_errors=[],
        )

    def _provider_name(self) -> str:
        return str(
            getattr(
                self.llm_provider,
                "provider_name",
                type(self.llm_provider).__name__,
            )
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
            provider=self._provider_name(),
            model=getattr(self.llm_provider, "model", ""),
            usage=None,
            generation_status=status,
            valid=False,
            citation_valid=False,
            validation_errors=errors,
        )

    def investigate_with_evidence(
        self,
        question: str,
        evidence: Any,
        *,
        diagnostic_findings: dict[str, Any] | None = None,
        correlation_results: dict[str, Any] | None = None,
        filters: Any = None,
        top_k: int | None = None,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> RAGResponse:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        question = question.strip()

        evidence_items: list[RetrievedEvidence] = []
        seen: set[str] = set()
        for item in evidence or []:
            converted = self._to_retrieved_evidence(item)
            if converted.chunk_id and converted.chunk_id in seen:
                continue
            if converted.chunk_id:
                seen.add(converted.chunk_id)
            evidence_items.append(converted)

        diagnostic_text = self._format_diagnostic_findings(
            diagnostic_findings or {}, correlation_results
        )

        if not evidence_items and not diagnostic_text:
            report = self._insufficient_report(
                "No retrieved evidence or diagnostic findings were available."
            )
            return RAGResponse(
                question=question,
                report=report,
                retrieved_evidence=[],
                source_references=[],
                retrieval_metadata={
                    "retrieved_count": 0,
                    "filters": filters,
                    "top_k": top_k,
                },
                provider=self._provider_name(),
                model=getattr(self.llm_provider, "model", ""),
                usage=None,
                generation_status="insufficient_evidence",
                valid=True,
                citation_valid=True,
                validation_errors=[],
            )

        if not evidence_items:
            # Keep diagnostic findings without fabricating knowledge citations.
            report = self._insufficient_report("No relevant knowledge-base evidence was retrieved.")
            # Preserve diagnostic recommendations in missing information.
            if diagnostic_text:
                report.missing_information = ["Relevant knowledge-base evidence for this incident."]
            return RAGResponse(
                question=question,
                report=report,
                retrieved_evidence=[],
                source_references=[],
                retrieval_metadata={
                    "retrieved_count": 0,
                    "filters": filters,
                    "top_k": top_k,
                },
                provider=self._provider_name(),
                model=getattr(self.llm_provider, "model", ""),
                usage=None,
                generation_status="insufficient_evidence",
                valid=True,
                citation_valid=True,
                validation_errors=[],
            )

        context = self.context_builder.build(evidence_items)
        evidence_for_llm = context.items
        user_prompt = build_user_prompt(question, context.formatted_context)
        if diagnostic_text:
            user_prompt += "\n\nDiagnostic findings (deterministic):\n" + diagnostic_text

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
                evidence=evidence_for_llm,
                metadata={
                    "retrieved_count": len(evidence_for_llm),
                    "filters": filters,
                    "top_k": top_k,
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
                    evidence=evidence_for_llm,
                    metadata={
                        "retrieved_count": len(evidence_for_llm),
                        "filters": filters,
                        "top_k": top_k,
                        "context_truncated": context.truncated,
                    },
                )

        validation = self.citation_validator.validate(report, evidence_for_llm)
        source_references = [
            {
                "chunk_id": item.chunk_id,
                "doc_id": item.doc_id,
                "title": item.title,
                "source_path": item.source_path,
            }
            for item in evidence_for_llm
        ]

        if not validation.valid:
            return RAGResponse(
                question=question,
                report=report,
                retrieved_evidence=evidence_for_llm,
                source_references=source_references,
                retrieval_metadata={
                    "retrieved_count": len(evidence_for_llm),
                    "filters": filters,
                    "top_k": top_k,
                    "context_truncated": context.truncated,
                },
                provider=self._provider_name(),
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
            retrieved_evidence=evidence_for_llm,
            source_references=source_references,
            retrieval_metadata={
                "retrieved_count": len(evidence_for_llm),
                "filters": filters,
                "top_k": top_k,
                "context_truncated": context.truncated,
            },
            provider=self._provider_name(),
            model=getattr(llm_result, "model", getattr(self.llm_provider, "model", "")),
            usage=self._usage_to_dict(getattr(llm_result, "usage", None)),
            generation_status="success",
            valid=True,
            citation_valid=True,
            validation_errors=[],
        )

    @staticmethod
    def _to_retrieved_evidence(item: Any) -> RetrievedEvidence:
        if isinstance(item, RetrievedEvidence):
            return item
        if isinstance(item, dict):
            return RetrievedEvidence(
                chunk_id=str(item.get("chunk_id", "")),
                doc_id=str(item.get("doc_id", "") or ""),
                title=str(item.get("title", "") or ""),
                source_path=str(item.get("source_path", "") or ""),
                chunk_text=str(
                    item.get("chunk_text", item.get("text", item.get("document", ""))) or ""
                ),
                metadata=dict(item.get("metadata", {}) or {}),
            )
        return RetrievedEvidence(
            chunk_id=str(getattr(item, "chunk_id", "")),
            doc_id=str(getattr(item, "doc_id", "") or ""),
            title=str(getattr(item, "title", "") or ""),
            source_path=str(getattr(item, "source_path", "") or ""),
            chunk_text=str(
                getattr(item, "chunk_text", getattr(item, "text", getattr(item, "document", "")))
            ),
            metadata=dict(getattr(item, "metadata", {}) or {}),
        )

    @staticmethod
    def _format_diagnostic_findings(
        diagnostic_results: dict[str, Any], correlation_results: dict[str, Any] | None
    ) -> str:
        lines: list[str] = []
        for tool, result in (diagnostic_results or {}).items():
            findings = result.get("findings", []) if isinstance(result, dict) else []
            lines.append(f"[{tool}]")
            if isinstance(result, dict):
                lines.append(f"status: {result.get('status', 'unknown')}")
                for finding in findings[:10]:
                    code = (
                        finding.get("code", "")
                        if isinstance(finding, dict)
                        else getattr(finding, "code", "")
                    )
                    title = (
                        finding.get("title", "")
                        if isinstance(finding, dict)
                        else getattr(finding, "title", "")
                    )
                    lines.append(f"- {code}: {title}")
        if correlation_results:
            lines.append("[correlation]")
            groups = (
                correlation_results.get("correlation_groups", [])
                if isinstance(correlation_results, dict)
                else []
            )
            for group in groups[:5]:
                reason = (
                    group.get("correlation_reason", "")
                    if isinstance(group, dict)
                    else getattr(group, "correlation_reason", "")
                )
                lines.append(f"- {reason}")
        return "\n".join(lines).strip()

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
