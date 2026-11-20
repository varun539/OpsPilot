"""Configuration for OpsPilot LLM providers.

Production selection is restricted to providers with a usable free tier. The
OpenAI adapter is retained for interface compatibility and tests, but it is not
selectable through this configuration and therefore cannot make paid calls from
the production runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

PROVIDER_ENV = "OPSPILOT_LLM_PROVIDER"
MODEL_ENV = "OPSPILOT_LLM_MODEL"
API_KEY_ENV = "OPSPILOT_LLM_API_KEY"
BASE_URL_ENV = "OPSPILOT_LLM_BASE_URL"

DEFAULT_PROVIDER = "gemini"
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"gemini", "groq", "openrouter"})
PROVIDER_KEY_ENVS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
DEFAULT_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0
DEFAULT_MAX_OUTPUT_TOKENS = 1200
MAX_OUTPUT_TOKENS = 4096
MIN_TIMEOUT = 0.1


def _int_or_none(value: str | None, *, name: str) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer for {name}: {value!r}") from exc


def _float_or_none(value: str | None, default: float, *, name: str) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number for {name}: {value!r}") from exc


def bounded_timeout(config: "LLMConfig", value: float | None) -> float:
    """Clamp a per-call timeout to the configured hard bound."""
    if value is None:
        return config.timeout
    try:
        requested = float(value)
    except (TypeError, ValueError):
        return config.timeout
    if requested <= 0:
        return config.timeout
    return min(requested, MAX_TIMEOUT)


def bounded_max_output_tokens(config: "LLMConfig", value: int | None) -> int:
    """Clamp a per-call output limit to the configured hard bound."""
    if value is None:
        return config.max_output_tokens
    try:
        requested = int(value)
    except (TypeError, ValueError):
        return config.max_output_tokens
    if requested < 1:
        return config.max_output_tokens
    return min(requested, config.max_output_tokens, MAX_OUTPUT_TOKENS)


@dataclass
class LLMConfig:
    """Runtime configuration for a selected LLM provider.

    ``api_key`` is excluded from ``repr`` so accidental logging of a config
    object cannot disclose it.
    """

    provider: str = DEFAULT_PROVIDER
    api_key: str | None = field(default=None, repr=False)
    model: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    base_url: str | None = None

    def __post_init__(self) -> None:
        self.provider = (self.provider or DEFAULT_PROVIDER).strip().lower()
        self.model = (self.model or "").strip()
        if self.provider not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(
                f"{PROVIDER_ENV} must be one of {supported} (got {self.provider!r}); "
                "the paid OpenAI provider is not available in the live runtime"
            )
        self.timeout = float(self.timeout)
        if not MIN_TIMEOUT <= self.timeout <= MAX_TIMEOUT:
            raise ValueError(
                f"OPSPILOT_LLM_TIMEOUT must be greater than 0 and at most {MAX_TIMEOUT:g}"
            )
        if self.max_output_tokens is None:
            self.max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
        self.max_output_tokens = int(self.max_output_tokens)
        if not 1 <= self.max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError(
                "OPSPILOT_LLM_MAX_OUTPUT_TOKENS must be between 1 and "
                f"{MAX_OUTPUT_TOKENS}"
            )
        if self.base_url is not None:
            self.base_url = self.base_url.strip() or None
        if self.base_url and self.provider == "gemini":
            raise ValueError(
                f"{BASE_URL_ENV} applies only to groq and openrouter; the native "
                "Gemini endpoint is fixed"
            )
        if self.base_url and not self.base_url.startswith("https://"):
            raise ValueError(f"{BASE_URL_ENV} must use https")

    @property
    def provider_key_env(self) -> str:
        return PROVIDER_KEY_ENVS[self.provider]

    @property
    def api_key_hint(self) -> str:
        return f"{self.provider_key_env} or {API_KEY_ENV}"

    @property
    def effective_base_url(self) -> str | None:
        if self.provider == "gemini":
            return None
        return self.base_url or DEFAULT_BASE_URLS[self.provider]

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Build configuration from server-side environment variables.

        The provider-specific key is read first from ``OPSPILOT_LLM_API_KEY``
        and then from that provider's conventional key variable. OpenAI's key
        variable is intentionally never consulted.
        """
        provider = (os.getenv(PROVIDER_ENV, "") or DEFAULT_PROVIDER).strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(
                f"{PROVIDER_ENV} must be one of {supported} (got {provider!r})"
            )
        provider_key_env = PROVIDER_KEY_ENVS[provider]
        api_key = (
            os.getenv(API_KEY_ENV, "").strip()
            or os.getenv(provider_key_env, "").strip()
            or None
        )
        max_output_tokens = _int_or_none(
            os.getenv("OPSPILOT_LLM_MAX_OUTPUT_TOKENS"),
            name="OPSPILOT_LLM_MAX_OUTPUT_TOKENS",
        )
        if max_output_tokens is None:
            max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
        base_url = os.getenv(BASE_URL_ENV, "").strip() or None
        return cls(
            provider=provider,
            api_key=api_key,
            model=os.getenv(MODEL_ENV, ""),
            timeout=_float_or_none(
                os.getenv("OPSPILOT_LLM_TIMEOUT"),
                DEFAULT_TIMEOUT,
                name="OPSPILOT_LLM_TIMEOUT",
            ),
            max_output_tokens=max_output_tokens,
            base_url=base_url,
        )
