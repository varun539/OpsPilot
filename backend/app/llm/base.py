"""Base types for OpsPilot LLM providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMUsage:
    """Token usage metadata returned by a provider."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, Any] | None = None


@dataclass
class LLMResult:
    """Result of a structured generation request."""

    output: Any
    model: str
    usage: LLMUsage | None = None
    raw_response: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    """Protocol implemented by all LLM providers."""

    model: str

    def generate_structured(
        self,
        *,
        system_instructions: str,
        user_input: str,
        output_schema: type[Any],
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        """Generate a structured response matching ``output_schema``."""
        ...


