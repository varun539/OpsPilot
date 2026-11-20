"""Mocked tests for the Groq and OpenRouter Chat Completions adapters."""

from __future__ import annotations

import json
from types import SimpleNamespace

import openai
import pytest

from backend.app.llm.config import LLMConfig
from backend.app.llm.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMIncompleteOutputError,
    LLMRateLimitError,
)
from backend.app.llm.openai_compatible_provider import OpenAICompatibleProvider
from backend.app.rag.models import InvestigationReport


def _report_dict(chunk_id: str = "c1") -> dict:
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
        "recommended_actions": [],
        "missing_information": [],
        "limitations": [],
    }


def _response(content: str, *, finish_reason: str = "stop", model: str = "free-model-1"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, refusal=None),
                finish_reason=finish_reason,
            )
        ],
        model=model,
        usage=SimpleNamespace(
            prompt_tokens=7, completion_tokens=11, total_tokens=18
        ),
    )


class _Completions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class _Client:
    def __init__(self, completions: _Completions):
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    def close(self):
        self.closed = True


def _provider(provider: str = "groq", **overrides):
    default_model = (
        "meta-llama/llama-3.2-3b-instruct:free"
        if provider == "openrouter"
        else "llama-3.3-70b-versatile"
    )
    model = overrides.pop("model", default_model)
    config = LLMConfig(
        provider=provider,
        api_key="free-tier-secret",
        model=model,
        **overrides,
    )
    completions = _Completions()
    return (
        OpenAICompatibleProvider(config=config, client=_Client(completions)),
        completions,
    )


def test_groq_structured_request_and_usage() -> None:
    provider, completions = _provider("groq")
    completions.response = _response(json.dumps(_report_dict()))
    result = provider.generate_structured(
        system_instructions="Investigate from evidence.",
        user_input="Synthetic incident.",
        output_schema=InvestigationReport,
        timeout=20,
        max_output_tokens=700,
    )
    assert isinstance(result.output, InvestigationReport)
    assert result.model == "free-model-1"
    assert result.usage and result.usage.total_tokens == 18
    assert result.metadata["provider"] == "groq"
    call = completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_tokens"] == 700
    assert call["timeout"] == 20
    assert call["temperature"] == 0
    assert "root_cause_hypotheses" in call["messages"][0]["content"]
    assert call["messages"][1]["content"] == "Synthetic incident."


def test_per_call_limits_cannot_exceed_config() -> None:
    provider, completions = _provider("groq", max_output_tokens=64)
    completions.response = _response(json.dumps(_report_dict()))
    provider.generate_structured(
        system_instructions="s",
        user_input="u",
        output_schema=InvestigationReport,
        timeout=999,
        max_output_tokens=99999,
    )
    call = completions.calls[0]
    assert call["max_tokens"] == 64
    assert call["timeout"] <= 120


def test_openrouter_request_uses_free_variant() -> None:
    provider, completions = _provider("openrouter")
    completions.response = _response(json.dumps(_report_dict()))
    result = provider.generate_structured(
        system_instructions="s",
        user_input="u",
        output_schema=InvestigationReport,
    )
    assert result.metadata["zero_cost"] == "openrouter_free_variant"
    assert completions.calls[0]["model"].endswith(":free")


def test_openrouter_paid_model_is_rejected_before_any_call() -> None:
    provider, completions = _provider("openrouter", model="openai/gpt-6")
    with pytest.raises(LLMConfigurationError, match=":free"):
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )
    assert completions.calls == []


def test_groq_rate_limit_is_clear() -> None:
    error = openai.RateLimitError(
        "quota exhausted",
        response=SimpleNamespace(
            status_code=429, request=SimpleNamespace(), headers={}
        ),
        body={},
    )
    provider, completions = _provider("groq")
    completions.error = error
    with pytest.raises(LLMRateLimitError, match="quota"):
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )


def test_groq_authentication_error_hides_provider_text() -> None:
    error = openai.AuthenticationError(
        "bad key free-tier-secret",
        response=SimpleNamespace(
            status_code=401, request=SimpleNamespace(), headers={}
        ),
        body={},
    )
    provider, completions = _provider("groq")
    completions.error = error
    with pytest.raises(LLMAuthenticationError) as excinfo:
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )
    assert "free-tier-secret" not in str(excinfo.value)


def test_groq_truncated_output_is_incomplete() -> None:
    provider, completions = _provider("groq")
    completions.response = _response(
        json.dumps(_report_dict()), finish_reason="length"
    )
    with pytest.raises(LLMIncompleteOutputError):
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )


def test_groq_fenced_json_is_accepted() -> None:
    provider, completions = _provider("groq")
    completions.response = _response(
        "```json\n" + json.dumps(_report_dict()) + "\n```"
    )
    result = provider.generate_structured(
        system_instructions="s",
        user_input="u",
        output_schema=InvestigationReport,
    )
    assert isinstance(result.output, InvestigationReport)


def test_client_close_is_forwarded() -> None:
    provider, _ = _provider("groq")
    client = provider._client
    provider.close()
    assert client.closed is True
