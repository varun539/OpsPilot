"""Tests for the OpenAI provider."""

from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest

from backend.app.llm.config import LLMConfig
from backend.app.llm.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMIncompleteOutputError,
    LLMRefusalError,
    LLMTimeoutError,
)
from backend.app.llm.openai_provider import OpenAIProvider
from backend.app.rag.models import InvestigationReport


def _report():
    return InvestigationReport.model_validate(
        {
            "summary": "summary",
            "incident_category": "database",
            "affected_components": [],
            "evidence_status": "sufficient",
            "root_cause_hypotheses": [],
            "recommended_actions": [],
            "missing_information": [],
            "limitations": [],
        }
    )


class FakeUsage:
    input_tokens = 1
    output_tokens = 2
    total_tokens = 3


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, responses):
        self.responses = responses


def test_missing_api_key():
    provider = OpenAIProvider(config=LLMConfig(api_key=None))
    with pytest.raises(LLMConfigurationError):
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )


def test_lazy_client_initialization(monkeypatch):
    report = _report()
    response = SimpleNamespace(
        status="completed",
        refusal=None,
        output_parsed=report,
        model="gpt-test",
        usage=FakeUsage(),
    )
    fake_responses = FakeResponses(response=response)
    fake_client = FakeClient(fake_responses)

    def factory(**kwargs):
        return fake_client

    monkeypatch.setattr("backend.app.llm.openai_provider.openai.OpenAI", factory)
    provider = OpenAIProvider(config=LLMConfig(api_key="secret"))
    assert provider._client is None
    result = provider.generate_structured(
        system_instructions="s",
        user_input="u",
        output_schema=InvestigationReport,
    )
    assert result.output is report
    assert provider._client is fake_client
    assert fake_responses.calls


def test_structured_response_parsing():
    report = _report()
    response = SimpleNamespace(
        status="completed",
        refusal=None,
        output_parsed=report,
        model="gpt-test",
        usage=FakeUsage(),
    )
    provider = OpenAIProvider(
        config=LLMConfig(api_key="secret"),
        client=FakeClient(FakeResponses(response=response)),
    )
    result = provider.generate_structured(
        system_instructions="s",
        user_input="u",
        output_schema=InvestigationReport,
    )
    assert result.output is report
    assert result.model == "gpt-test"
    assert result.usage.total_tokens == 3


def test_refusal_handling():
    response = SimpleNamespace(
        status="completed",
        refusal="I cannot help",
        output_parsed=None,
        model="gpt-test",
        usage=None,
    )
    provider = OpenAIProvider(
        config=LLMConfig(api_key="secret"),
        client=FakeClient(FakeResponses(response=response)),
    )
    with pytest.raises(LLMRefusalError):
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )


def test_incomplete_output_handling():
    response = SimpleNamespace(
        status="incomplete",
        refusal=None,
        output_parsed=None,
        model="gpt-test",
        usage=None,
    )
    provider = OpenAIProvider(
        config=LLMConfig(api_key="secret"),
        client=FakeClient(FakeResponses(response=response)),
    )
    with pytest.raises(LLMIncompleteOutputError):
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )


def test_timeout_handling():
    provider = OpenAIProvider(
        config=LLMConfig(api_key="secret"),
        client=FakeClient(FakeResponses(error=openai.APITimeoutError("timeout"))),
    )
    with pytest.raises(LLMTimeoutError):
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )


def test_authentication_handling():
    provider = OpenAIProvider(
        config=LLMConfig(api_key="secret"),
        client=FakeClient(
            FakeResponses(
                error=openai.AuthenticationError(
                    "bad key",
                    response=SimpleNamespace(
                        status_code=401, request=SimpleNamespace(), headers={}
                    ),
                    body={},
                )
            )
        ),
    )
    with pytest.raises(LLMAuthenticationError) as excinfo:
        provider.generate_structured(
            system_instructions="s",
            user_input="u",
            output_schema=InvestigationReport,
        )
    # Provider text can echo request credentials; it must not reach clients.
    assert "bad key" not in str(excinfo.value)
    assert "sk-" not in str(excinfo.value)
