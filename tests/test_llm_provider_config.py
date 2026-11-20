"""Configuration and zero-cost provider-selection tests."""

from __future__ import annotations

import pytest

from backend.app.llm.config import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    LLMConfig,
)
from backend.app.llm.exceptions import LLMConfigurationError
from backend.app.llm.factory import create_llm_provider
from backend.app.llm.gemini_provider import GeminiProvider
from backend.app.llm.openai_compatible_provider import OpenAICompatibleProvider

_ENV_NAMES = (
    "OPSPILOT_LLM_PROVIDER",
    "OPSPILOT_LLM_MODEL",
    "OPSPILOT_LLM_API_KEY",
    "OPSPILOT_LLM_BASE_URL",
    "OPSPILOT_LLM_TIMEOUT",
    "OPSPILOT_LLM_MAX_OUTPUT_TOKENS",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_gemini_and_bounded() -> None:
    config = LLMConfig.from_env()
    assert config.provider == "gemini"
    assert config.model == ""
    assert config.api_key is None
    assert config.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert config.timeout == 30


def test_provider_specific_key_and_model_are_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPSPILOT_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("OPSPILOT_LLM_MODEL", "llama-3.3-70b-versatile")
    config = LLMConfig.from_env()
    assert config.provider == "groq"
    assert config.api_key == "groq-key"
    assert config.model == "llama-3.3-70b-versatile"


def test_neutral_key_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSPILOT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPSPILOT_LLM_API_KEY", "neutral-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "provider-key")
    config = LLMConfig.from_env()
    assert config.api_key == "neutral-key"


def test_openai_key_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "paid-key")
    config = LLMConfig.from_env()
    assert config.api_key is None


def test_unknown_and_paid_providers_are_rejected() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        LLMConfig(provider="openai")
    with pytest.raises(ValueError, match="must be one of"):
        LLMConfig(provider="anthropic")


def test_missing_model_and_key_are_named() -> None:
    with pytest.raises(LLMConfigurationError, match="OPSPILOT_LLM_MODEL"):
        create_llm_provider(LLMConfig(provider="gemini", model="", api_key="x"))
    with pytest.raises(LLMConfigurationError, match="GEMINI_API_KEY"):
        create_llm_provider(
            LLMConfig(provider="gemini", model="gemini-free-test", api_key=None)
        )


def test_factory_selects_native_gemini_and_compatible_others() -> None:
    gemini = create_llm_provider(
        LLMConfig(provider="gemini", model="gemini-free-test", api_key="key")
    )
    groq = create_llm_provider(
        LLMConfig(provider="groq", model="llama-3.3-70b-versatile", api_key="key")
    )
    assert isinstance(gemini, GeminiProvider)
    assert isinstance(groq, OpenAICompatibleProvider)
    assert gemini.provider_name == "gemini"
    assert groq.provider_name == "groq"


def test_openrouter_requires_free_variant() -> None:
    with pytest.raises(LLMConfigurationError, match=":free"):
        create_llm_provider(
            LLMConfig(
                provider="openrouter",
                model="openai/gpt-6",
                api_key="key",
            )
        )
    provider = create_llm_provider(
        LLMConfig(
            provider="openrouter",
            model="meta-llama/llama-3.2-3b-instruct:free",
            api_key="key",
        )
    )
    assert isinstance(provider, OpenAICompatibleProvider)


def test_base_url_defaults_and_validation() -> None:
    groq = LLMConfig(provider="groq", model="m", api_key="k")
    assert groq.effective_base_url == "https://api.groq.com/openai/v1"
    router = LLMConfig(provider="openrouter", model="m:free", api_key="k")
    assert router.effective_base_url == "https://openrouter.ai/api/v1"
    override = LLMConfig(
        provider="groq",
        model="m",
        api_key="k",
        base_url="https://proxy.example.test/v1",
    )
    assert override.effective_base_url == "https://proxy.example.test/v1"
    with pytest.raises(ValueError, match="only to groq"):
        LLMConfig(provider="gemini", model="m", base_url="https://x.test")
    with pytest.raises(ValueError, match="https"):
        LLMConfig(provider="groq", model="m", base_url="http://x.test")


def test_env_base_url_is_rejected_for_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPSPILOT_LLM_BASE_URL", "https://proxy.example.test")
    with pytest.raises(ValueError, match="only to groq"):
        LLMConfig.from_env()


def test_timeout_and_output_bounds() -> None:
    with pytest.raises(ValueError, match="OPSPILOT_LLM_TIMEOUT"):
        LLMConfig(timeout=0)
    with pytest.raises(ValueError, match="MAX_OUTPUT_TOKENS"):
        LLMConfig(max_output_tokens=0)
    with pytest.raises(ValueError, match="MAX_OUTPUT_TOKENS"):
        LLMConfig(max_output_tokens=5000)


def test_invalid_timeout_env_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPSPILOT_LLM_TIMEOUT", "abc")
    with pytest.raises(ValueError, match="invalid number for OPSPILOT_LLM_TIMEOUT"):
        LLMConfig.from_env()

    monkeypatch.setenv("OPSPILOT_LLM_TIMEOUT", "0")
    with pytest.raises(ValueError, match="OPSPILOT_LLM_TIMEOUT"):
        LLMConfig.from_env()

    monkeypatch.setenv("OPSPILOT_LLM_TIMEOUT", "500")
    with pytest.raises(ValueError, match="OPSPILOT_LLM_TIMEOUT"):
        LLMConfig.from_env()

    monkeypatch.setenv("OPSPILOT_LLM_TIMEOUT", "90")
    assert LLMConfig.from_env().timeout == 90.0


def test_api_key_is_not_in_repr() -> None:
    config = LLMConfig(provider="gemini", api_key="super-secret", model="m")
    assert "super-secret" not in repr(config)
    assert "api_key" not in repr(config)
