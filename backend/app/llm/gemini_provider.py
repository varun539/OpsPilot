"""Native Google Gemini Developer API provider.

Gemini's OpenAI-compatible endpoint does not expose the Responses API
structured parsing used by :class:`OpenAIProvider`, so this adapter calls the
native ``generateContent`` method with JSON MIME type and a full JSON Schema.
The response is still validated locally with the expected Pydantic model.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

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

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_SAFETY_FINISH_REASONS = {
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
}


class GeminiProvider:
    """Gemini-backed provider with a lazy HTTP client and local validation."""

    provider_name = "gemini"

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        client: httpx.Client | None = None,
        **config_kwargs: Any,
    ) -> None:
        if config is None:
            config = LLMConfig(**config_kwargs)
        if config.provider != "gemini":
            raise LLMConfigurationError("GeminiProvider requires a gemini configuration")
        self.config = config
        self.model = config.model
        self._client = client

    def _ensure_client(self) -> httpx.Client:
        if not self.config.api_key:
            raise LLMConfigurationError(
                f"{self.config.api_key_hint} is not configured"
            )
        if not self.config.model:
            raise LLMConfigurationError(
                "OPSPILOT_LLM_MODEL is required for the Gemini provider"
            )
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.config.timeout,
                follow_redirects=False,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

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
        url = (
            f"{GEMINI_BASE_URL}/models/"
            f"{quote(self.config.model, safe='')}:generateContent"
        )
        payload = {
            "systemInstruction": {
                "parts": [{"text": schema_prompt(system_instructions, output_schema)}]
            },
            "contents": [{"role": "user", "parts": [{"text": user_input}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": request_max_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": output_schema.model_json_schema(),
            },
        }
        try:
            response = client.post(
                url,
                headers={"x-goog-api-key": self.config.api_key or ""},
                json=payload,
                timeout=request_timeout,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Gemini request timed out") from exc
        except httpx.RequestError as exc:
            raise LLMResponseError("Gemini request failed") from exc

        # Never include response text, request URLs or headers in client-visible
        # errors: the API key travels in a header and provider bodies can echo
        # request content.
        if response.status_code in (401, 403):
            raise LLMAuthenticationError("Gemini authentication failed")
        if response.status_code == 429:
            raise LLMRateLimitError(
                "Gemini free-tier rate limit or quota exceeded; retry later"
            )
        if response.status_code in (408, 504):
            raise LLMTimeoutError("Gemini request timed out")
        if response.status_code >= 400:
            raise LLMResponseError(
                f"Gemini API request failed (status {response.status_code})"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMResponseError("Gemini returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise LLMResponseError("Gemini returned an unexpected response")

        feedback = data.get("promptFeedback") or {}
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise LLMRefusalError("Gemini blocked the request")

        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            raise LLMIncompleteOutputError("Gemini returned no candidate output")
        candidate = candidates[0]
        finish_reason = str(candidate.get("finishReason") or "")
        if finish_reason in _SAFETY_FINISH_REASONS:
            raise LLMRefusalError("Gemini declined to produce a report")
        if finish_reason == "MAX_TOKENS":
            raise LLMIncompleteOutputError("Gemini output was truncated at the token limit")
        if finish_reason and finish_reason != "STOP":
            raise LLMIncompleteOutputError(
                f"Gemini returned finish reason {finish_reason!r}"
            )

        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        )
        parsed = parse_structured_output(
            text,
            output_schema,
            provider_label="Gemini",
        )

        usage = _gemini_usage(data.get("usageMetadata"))
        return LLMResult(
            output=parsed,
            model=str(data.get("modelVersion") or self.config.model),
            usage=usage,
            raw_response=data,
            metadata={
                "provider": self.provider_name,
                "requested_model": self.config.model,
                "zero_cost": "free_tier",
            },
        )


def _gemini_usage(value: Any) -> LLMUsage | None:
    if not isinstance(value, dict):
        return None
    prompt = value.get("promptTokenCount")
    completion = value.get("candidatesTokenCount")
    total = value.get("totalTokenCount")
    raw = {
        key: value.get(key)
        for key in (
            "promptTokenCount",
            "candidatesTokenCount",
            "totalTokenCount",
            "cachedContentTokenCount",
            "thoughtsTokenCount",
        )
        if value.get(key) is not None
    }
    if prompt is None and completion is None and total is None and not raw:
        return None
    return LLMUsage(
        prompt_tokens=prompt if isinstance(prompt, int) else None,
        completion_tokens=completion if isinstance(completion, int) else None,
        total_tokens=total if isinstance(total, int) else None,
        raw=raw or None,
    )
