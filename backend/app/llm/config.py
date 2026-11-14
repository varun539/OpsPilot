"""Configuration for OpsPilot LLM providers."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer configuration value: {value!r}") from exc


def _float_or_none(value: str | None, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid float configuration value: {value!r}") from exc


@dataclass
class LLMConfig:
    """Runtime configuration for an LLM provider."""

    api_key: str | None = None
    model: str = "gpt-6-astra"
    timeout: float = 30.0
    max_output_tokens: int | None = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Build configuration from environment variables."""
        return cls(
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPSPILOT_LLM_MODEL", "gpt-6-astra"),
            timeout=_float_or_none(os.getenv("OPSPILOT_LLM_TIMEOUT"), 30.0),
            max_output_tokens=_int_or_none(os.getenv("OPSPILOT_LLM_MAX_OUTPUT_TOKENS")),
        )
