"""Shared structured-output parsing for non-OpenAI providers."""

from __future__ import annotations

import json
import re
from typing import Any

from .exceptions import LLMIncompleteOutputError, LLMResponseError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def schema_prompt(system_instructions: str, output_schema: type[Any]) -> str:
    """Append the complete JSON Schema to a provider's system instruction.

    Groq and OpenRouter accept JSON-object mode across their free model
    catalogues more broadly than server-side strict schemas. The schema is
    therefore supplied to the model and enforced again locally by Pydantic.
    """
    schema = json.dumps(
        output_schema.model_json_schema(),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return (
        f"{system_instructions}\n\n"
        "Return exactly one JSON object and no prose or markdown fences. "
        "It must satisfy this JSON Schema:\n"
        f"{schema}"
    )


def parse_structured_output(
    text: str | None,
    output_schema: type[Any],
    *,
    provider_label: str,
) -> Any:
    """Extract and validate one JSON object from provider text.

    Raw provider text and Pydantic error values are never included in the
    client-visible exception: reports persist those messages. The original
    exception remains chained for server-side diagnosis.
    """
    if not isinstance(text, str) or not text.strip():
        raise LLMIncompleteOutputError(f"{provider_label} returned no output")

    candidate = text.strip()
    fenced = _FENCE.search(candidate)
    if fenced is not None:
        candidate = fenced.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end < start:
        raise LLMResponseError(f"{provider_label} output was not a JSON object")
    candidate = candidate[start : end + 1]
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError) as exc:
        raise LLMResponseError(f"{provider_label} output was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LLMResponseError(f"{provider_label} output was not a JSON object")
    try:
        return output_schema.model_validate(payload)
    except Exception as exc:
        raise LLMResponseError(
            f"{provider_label} output failed schema validation"
        ) from exc
