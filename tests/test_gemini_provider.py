"""Mocked tests for the native Gemini provider and report validation."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from backend.app.llm.config import LLMConfig
from backend.app.llm.exceptions import (
    LLMAuthenticationError,
    LLMIncompleteOutputError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
)
from backend.app.llm.gemini_provider import GeminiProvider
from backend.app.rag.generator import RAGService
from backend.app.rag.models import InvestigationReport


def _report_dict(chunk_id: str = "c1") -> dict[str, Any]:
    return {
        "summary": "Likely pool exhaustion.",
        "incident_category": "database",
        "affected_components": ["db"],
        "evidence_status": "sufficient",
        "root_cause_hypotheses": [
            {
                "title": "Pool exhaustion",
                "explanation": "Connections were not released.",
                "supporting_chunk_ids": [chunk_id],
                "evidence_strength": "medium",
            }
        ],
        "supporting_evidence": [{"chunk_id": chunk_id}],
        "recommended_actions": [
            {
                "description": "Inspect the pool.",
                "rationale": "The cited evidence supports inspection.",
                "supporting_chunk_ids": [chunk_id],
                "risk_level": "low",
                "requires_human_review": True,
            }
        ],
        "missing_information": [],
        "limitations": [],
    }


def _gemini_body(text: str) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 20,
            "totalTokenCount": 30,
        },
        "modelVersion": "gemini-test-001",
    }


def _provider(handler, **config_kwargs) -> GeminiProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = LLMConfig(
        provider="gemini",
        api_key="gemini-secret-key",
        model="gemini-free-test",
        **config_kwargs,
    )
    return GeminiProvider(config=config, client=client)


def test_native_gemini_request_and_local_validation() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-goog-api-key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_gemini_body(json.dumps(_report_dict())))

    provider = _provider(handler)
    try:
        result = provider.generate_structured(
            system_instructions="Investigate only from evidence.",
            user_input="Synthetic pool exhaustion incident.",
            output_schema=InvestigationReport,
            timeout=12,
            max_output_tokens=512,
        )
    finally:
        provider.close()

    assert isinstance(result.output, InvestigationReport)
    assert result.output.root_cause_hypotheses[0].supporting_chunk_ids == ["c1"]
    assert result.model == "gemini-test-001"
    assert result.usage and result.usage.total_tokens == 30
    assert result.metadata["provider"] == "gemini"
    assert "key=" not in captured["url"]
    assert captured["key"] == "gemini-secret-key"
    generation = captured["body"]["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["maxOutputTokens"] == 512
    assert generation["temperature"] == 0
    schema = generation["responseJsonSchema"]
    assert "root_cause_hypotheses" in schema["properties"]
    system_text = captured["body"]["systemInstruction"]["parts"][0]["text"]
    assert "Investigate only from evidence." in system_text


def test_gemini_request_limits_are_hard_bounded() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=_gemini_body(json.dumps(_report_dict())))

    provider = _provider(handler, max_output_tokens=64, timeout=5)
    try:
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
            timeout=999,
            max_output_tokens=99999,
        )
    finally:
        provider.close()
    assert captured["body"]["generationConfig"]["maxOutputTokens"] == 64
    timeout = captured["timeout"]
    assert timeout is not None
    assert all(value <= 120 for value in timeout.values())


def test_gemini_response_with_fenced_json_is_accepted() -> None:
    text = "```json\n" + json.dumps(_report_dict()) + "\n```"
    provider = _provider(lambda request: httpx.Response(200, json=_gemini_body(text)))
    try:
        result = provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )
    finally:
        provider.close()
    assert isinstance(result.output, InvestigationReport)


def test_gemini_invalid_schema_is_rejected_without_echoing_output() -> None:
    invalid = _report_dict()
    invalid["evidence_status"] = "insufficient"  # hypotheses are forbidden here
    body = _gemini_body(json.dumps(invalid))
    provider = _provider(lambda request: httpx.Response(200, json=body))
    try:
        with pytest.raises(LLMResponseError) as excinfo:
            provider.generate_structured(
                system_instructions="s",
                user_input="u",
                output_schema=InvestigationReport,
            )
    finally:
        provider.close()
    assert "schema validation" in str(excinfo.value)
    assert "Pool exhaustion" not in str(excinfo.value)


def test_gemini_authentication_error_is_generic() -> None:
    provider = _provider(
        lambda request: httpx.Response(
            401, json={"error": {"message": "bad key gemini-secret-key"}}
        )
    )
    try:
        with pytest.raises(LLMAuthenticationError) as excinfo:
            provider.generate_structured(
                system_instructions="s",
                user_input="u",
                output_schema=InvestigationReport,
            )
    finally:
        provider.close()
    assert "gemini-secret-key" not in str(excinfo.value)


def test_gemini_quota_error_is_clear() -> None:
    provider = _provider(lambda request: httpx.Response(429, json={"error": {}}))
    try:
        with pytest.raises(LLMRateLimitError, match="quota"):
            provider.generate_structured(
                system_instructions="s",
                user_input="u",
                output_schema=InvestigationReport,
            )
    finally:
        provider.close()


def test_gemini_timeout_is_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = _provider(handler)
    try:
        with pytest.raises(LLMTimeoutError):
            provider.generate_structured(
                system_instructions="s",
                user_input="u",
                output_schema=InvestigationReport,
            )
    finally:
        provider.close()


def test_gemini_safety_block_is_a_refusal() -> None:
    provider = _provider(
        lambda request: httpx.Response(
            200, json={"promptFeedback": {"blockReason": "SAFETY"}}
        )
    )
    try:
        with pytest.raises(LLMRefusalError):
            provider.generate_structured(
                system_instructions="s",
                user_input="u",
                output_schema=InvestigationReport,
            )
    finally:
        provider.close()


def test_gemini_truncated_output_is_incomplete() -> None:
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": '{"summary": "cut'}]},
                "finishReason": "MAX_TOKENS",
            }
        ]
    }
    provider = _provider(lambda request: httpx.Response(200, json=body))
    try:
        with pytest.raises(LLMIncompleteOutputError):
            provider.generate_structured(
                system_instructions="s",
                user_input="u",
                output_schema=InvestigationReport,
            )
    finally:
        provider.close()


class _StaticRetrieval:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results

    def search(self, question: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.results


def _evidence() -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": "c1",
            "doc_id": "KB-008",
            "title": "Connection pool",
            "source_path": "data/knowledge_base/KB-008.md",
            "chunk_text": "QueuePool exhausted under load.",
            "metadata": {},
            "final_rank": 1,
        }
    ]


def test_gemini_report_keeps_citation_validation_authoritative() -> None:
    provider = _provider(
        lambda request: httpx.Response(
            200, json=_gemini_body(json.dumps(_report_dict(chunk_id="unknown")))
        )
    )
    service = RAGService(
        retrieval_engine=_StaticRetrieval(_evidence()),
        llm_provider=provider,
    )
    try:
        response = service.investigate("Synthetic pool exhaustion", top_k=1)
    finally:
        provider.close()
    assert response.provider == "gemini"
    assert response.generation_status == "citation_validation_failed"
    assert response.citation_valid is False
    assert response.valid is False


def test_gemini_valid_report_preserves_citations_and_usage() -> None:
    provider = _provider(
        lambda request: httpx.Response(
            200, json=_gemini_body(json.dumps(_report_dict()))
        )
    )
    service = RAGService(
        retrieval_engine=_StaticRetrieval(_evidence()),
        llm_provider=provider,
    )
    try:
        response = service.investigate("Synthetic pool exhaustion", top_k=1)
    finally:
        provider.close()
    assert response.provider == "gemini"
    assert response.generation_status == "success"
    assert response.citation_valid is True
    assert response.valid is True
    assert response.source_references[0]["chunk_id"] == "c1"
    assert response.usage and response.usage["total_tokens"] == 30
