"""OpenAI provider implementation using the Responses API."""

from __future__ import annotations

from typing import Any

import openai

from .base import LLMResult, LLMUsage
from .config import LLMConfig
from .exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMIncompleteOutputError,
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
)


class OpenAIProvider:
    """OpenAI-backed provider with lazy client initialization."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        client: Any = None,
        **config_kwargs: Any,
    ) -> None:
        if config is None:
            config = LLMConfig(**config_kwargs)
        self.config = config
        self.model = config.model
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.config.api_key:
            raise LLMConfigurationError("OPENAI_API_KEY is not configured")
        try:
            self._client = openai.OpenAI(
                api_key=self.config.api_key,
                timeout=self.config.timeout,
            )
        except Exception as exc:
            raise LLMConfigurationError(f"failed to initialize OpenAI client: {exc}") from exc
        return self._client

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
        request_timeout = self.config.timeout if timeout is None else timeout
        request_max_tokens = (
            self.config.max_output_tokens if max_output_tokens is None else max_output_tokens
        )

        try:
            response = client.responses.parse(
                model=self.config.model,
                instructions=system_instructions,
                input=user_input,
                text_format=output_schema,
                max_output_tokens=request_max_tokens,
                timeout=request_timeout,
            )
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError(f"OpenAI request timed out: {exc}") from exc
        except openai.AuthenticationError as exc:
            raise LLMAuthenticationError(f"OpenAI authentication failed: {exc}") from exc
        except openai.OpenAIError as exc:
            refusal_error = getattr(openai, "RefusalError", None)
            if refusal_error is not None and isinstance(exc, refusal_error):
                raise LLMRefusalError(f"OpenAI refused the request: {exc}") from exc
            if isinstance(exc, openai.APIStatusError):
                raise LLMResponseError(f"OpenAI API error: {exc}") from exc
            raise LLMResponseError(f"OpenAI request failed: {exc}") from exc

        status = getattr(response, "status", None)
        if status not in (None, "completed"):
            raise LLMIncompleteOutputError(f"OpenAI response status: {status!r}")

        refusal = getattr(response, "refusal", None)
        if refusal:
            raise LLMRefusalError(f"OpenAI refused the request: {refusal}")

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            try:
                parsed = response.output[0].content[0].parsed
            except Exception:
                parsed = None
        if parsed is None:
            raise LLMIncompleteOutputError("OpenAI response did not contain parsed output")

        if not isinstance(parsed, output_schema):
            try:
                parsed = output_schema.model_validate(parsed)
            except Exception as exc:
                raise LLMResponseError(f"OpenAI output failed schema validation: {exc}") from exc

        usage_obj = getattr(response, "usage", None)
        usage = None
        if usage_obj is not None:
            usage = LLMUsage(
                prompt_tokens=getattr(usage_obj, "input_tokens", None),
                completion_tokens=getattr(usage_obj, "output_tokens", None),
                total_tokens=getattr(usage_obj, "total_tokens", None),
                raw=_as_dict(usage_obj),
            )

        return LLMResult(
            output=parsed,
            model=getattr(response, "model", self.config.model),
            usage=usage,
            raw_response=response,
        )


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
    try:
        return {k: getattr(value, k) for k in vars(value)}
    except Exception:
        return None
