"""Select exactly one zero-cost LLM provider from configuration.

There is no fallback chain. A provider error surfaces as ``provider_error`` in
the investigation; the runtime never retries against a different model or a
paid endpoint.
"""

from __future__ import annotations

from typing import Any

from .config import LLMConfig
from .exceptions import LLMConfigurationError
from .gemini_provider import GeminiProvider
from .openai_compatible_provider import OpenAICompatibleProvider


def create_llm_provider(config: LLMConfig) -> Any:
    """Create the explicitly configured provider.

    Raises:
        LLMConfigurationError: the provider, model, key or free-model guard is
            not satisfied. Callers must fail instead of substituting a model.
    """
    if not config.model:
        raise LLMConfigurationError(
            "OPSPILOT_LLM_MODEL is required when OPSPILOT_AI_MODE=live"
        )
    if not config.api_key:
        raise LLMConfigurationError(
            f"{config.api_key_hint} is required when OPSPILOT_AI_MODE=live"
        )
    if config.provider == "openrouter" and not config.model.endswith(":free"):
        raise LLMConfigurationError(
            "OpenRouter models must end with ':free'; paid model routing is disabled"
        )

    if config.provider == "gemini":
        return GeminiProvider(config)
    if config.provider in {"groq", "openrouter"}:
        return OpenAICompatibleProvider(config)
    # LLMConfig validates this, but keep the factory fail-closed if constructed
    # through an unexpected path.
    raise LLMConfigurationError(
        f"unsupported LLM provider {config.provider!r}; no fallback is attempted"
    )
