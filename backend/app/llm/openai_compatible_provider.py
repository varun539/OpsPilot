"""Groq and OpenRouter provider using their OpenAI-compatible Chat API.

Both services expose the Chat Completions surface rather than the Responses API
used by :class:`OpenAIProvider`. Requests use JSON-object mode; the complete
JSON Schema is included in the system instruction and every response is
validated locally with Pydantic before it can become a report.
"""

from __future__ import annotations

from typing import Any

import openai

from .base import LLMResult, LLMUsage
from .config import LLMConfig, bounded_max_output_tokens, bounded_timeout
from .exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMIncompleteOutputError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
)
from .parsing import parse_structured_output, schema_prompt

COMPATIBLE_PROVIDERS = frozenset({"groq", "openrouter"})


class OpenAICompatibleProvider:
    """Structured generation for Groq and OpenRouter free models."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        client: Any = None,
        **config_kwargs: Any,
    ) -> None:
        if config is None:
            config = LLMConfig(**config_kwargs)
        if config.provider not in COMPATIBLE_PROVIDERS:
            raise LLMConfigurationError(
                "OpenAICompatibleProvider requires a groq or openrouter configuration"
            )
        self.config = config
        self.provider_name = config.provider
        self.model = config.model
        self._client = client

    def _ensure_client(self) -> Any:
        if not self.config.api_key:
            raise LLMConfigurationError(
                f"{self.config.api_key_hint} is not configured"
            )
        if not self.config.model:
            raise LLMConfigurationError(
                "OPSPILOT_LLM_MODEL is required for this provider"
            )
        if self.provider_name == "openrouter" and not self.config.model.endswith(":free"):
            raise LLMConfigurationError(
                "OpenRouter live models must end with ':free'; paid model routing "
                "is disabled"
            )
        if self._client is None:
            try:
                self._client = openai.OpenAI(
                    api_key=self.config.api_key,
                    base_url=self.config.effective_base_url,
                    timeout=self.config.timeout,
                    max_retries=0,
                )
            except Exception as exc:
                raise LLMConfigurationError(
                    f"failed to initialize the {self.provider_name} client"
                ) from exc
        return self._client

    def close(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()

    def generate_structured(
        self,
        *,
        system_instructions: str,
        user_input: str,
        output_schema: type[Any],
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        client = self._ensure_client()
        request_timeout = bounded_timeout(self.config, timeout)
        request_max_tokens = bounded_max_output_tokens(
            self.config, max_output_tokens
        )
        try:
            response = client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {
                        "role": "system",
                        "content": schema_prompt(system_instructions, output_schema),
                    },
                    {"role": "user", "content": user_input},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=request_max_tokens,
                timeout=request_timeout,
            )
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError(f"{self._label} request timed out") from exc
        except openai.AuthenticationError as exc:
            raise LLMAuthenticationError(f"{self._label} authentication failed") from exc
        except openai.RateLimitError as exc:
            raise LLMRateLimitError(
                f"{self._label} free-tier rate limit or quota exceeded; retry later"
            ) from exc
        except openai.APIStatusError as exc:
            status_code = getattr(exc, "status_code", None)
            suffix = f" (status {status_code})" if status_code else ""
            raise LLMResponseError(f"{self._label} API request failed{suffix}") from exc
        except openai.OpenAIError as exc:
            raise LLMResponseError(f"{self._label} request failed") from exc

        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMIncompleteOutputError(f"{self._label} returned no choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise LLMRefusalError(f"{self._label} declined to produce a report")
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise LLMIncompleteOutputError(
                f"{self._label} output was truncated at the token limit"
            )
        if finish_reason == "content_filter":
            raise LLMRefusalError(f"{self._label} filtered the response")

        parsed = parse_structured_output(
            _message_text(getattr(message, "content", None)),
            output_schema,
            provider_label=self._label,
        )
        usage_obj = getattr(response, "usage", None)
        usage = None
        if usage_obj is not None:
            usage = LLMUsage(
                prompt_tokens=getattr(usage_obj, "prompt_tokens", None),
                completion_tokens=getattr(usage_obj, "completion_tokens", None),
                total_tokens=getattr(usage_obj, "total_tokens", None),
                raw=_as_dict(usage_obj),
            )
        return LLMResult(
            output=parsed,
            model=str(getattr(response, "model", None) or self.config.model),
            usage=usage,
            raw_response=response,
            metadata={
                "provider": self.provider_name,
                "requested_model": self.config.model,
                "zero_cost": (
                    "openrouter_free_variant"
                    if self.provider_name == "openrouter"
                    else "configured_free_tier"
                ),
            },
        )

    @property
    def _label(self) -> str:
        return "Groq" if self.provider_name == "groq" else "OpenRouter"


def _message_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    return None


def _as_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    for attr in ("model_dump", "to_dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                result = method()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
    return None
