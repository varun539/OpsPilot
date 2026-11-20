"""Explicit configuration for the production AI runtime.

``OPSPILOT_AI_MODE`` selects between two states only:

* ``disabled`` - incident/ticket management and read-only surfaces stay
  available; investigation execution returns a service-unavailable response.
* ``live`` - the real OpenAI provider, embedding model, persistent ChromaDB,
  hybrid retrieval engine and RAG service are assembled at startup.

There is intentionally no third "fallback" mode. A live runtime that cannot be
assembled fails startup instead of silently producing deterministic reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from backend.app.llm.config import LLMConfig
from backend.app.vectorstore.constants import DEFAULT_COLLECTION_NAME

AIMode = Literal["disabled", "live"]
VALID_MODES: frozenset[str] = frozenset({"disabled", "live"})

AI_MODE_ENV = "OPSPILOT_AI_MODE"
DEFAULT_AI_MODE: AIMode = "disabled"


class AIRuntimeConfigurationError(RuntimeError):
    """Raised when the configured AI runtime cannot be assembled safely."""


@dataclass(frozen=True)
class AIRuntimeConfig:
    """Validated runtime configuration derived from the process environment."""

    mode: AIMode
    llm: LLMConfig
    collection_name: str = DEFAULT_COLLECTION_NAME
    chroma_directory: str | None = None

    @property
    def live(self) -> bool:
        return self.mode == "live"

    @classmethod
    def from_env(cls) -> "AIRuntimeConfig":
        """Read and validate ``OPSPILOT_AI_MODE`` and the LLM settings.

        Unknown modes are rejected rather than coerced: a typo must not
        silently disable AI in production or silently enable it in tests.
        """
        raw = os.getenv(AI_MODE_ENV, "").strip().lower()
        mode: AIMode = raw or DEFAULT_AI_MODE  # type: ignore[assignment]
        if mode not in VALID_MODES:
            raise AIRuntimeConfigurationError(
                f"{AI_MODE_ENV} must be 'disabled' or 'live' (got {raw!r})"
            )
        try:
            llm = LLMConfig.from_env()
        except ValueError as exc:
            raise AIRuntimeConfigurationError(f"invalid LLM configuration: {exc}") from exc
        chroma_directory = os.getenv("OPSPILOT_CHROMA_DIR", "").strip() or None
        return cls(
            mode=mode,
            llm=llm,
            chroma_directory=chroma_directory,
        )
