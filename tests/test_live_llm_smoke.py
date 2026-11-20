"""Opt-in live smoke test for the explicitly configured free-tier provider.

Skipped unless both conditions hold:

* ``OPSPILOT_LIVE_LLM_TESTS=1``
* a configured server-side provider key is present

This is the only test allowed to make a real provider call. It uses a synthetic
health-check prompt and never prints or asserts on the key value. Do not run it
with confidential incident evidence.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from backend.app.llm.config import (
    API_KEY_ENV,
    PROVIDER_KEY_ENVS,
    LLMConfig,
)
from backend.app.llm.factory import create_llm_provider


class SmokeAnswer(BaseModel):
    status: str = "ok"


def _configured_key() -> str | None:
    provider = os.getenv("OPSPILOT_LLM_PROVIDER", "gemini").strip().lower()
    names = [API_KEY_ENV]
    provider_key = PROVIDER_KEY_ENVS.get(provider)
    if provider_key:
        names.append(provider_key)
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


pytestmark = pytest.mark.skipif(
    os.getenv("OPSPILOT_LIVE_LLM_TESTS") != "1" or not _configured_key(),
    reason=(
        "set OPSPILOT_LIVE_LLM_TESTS=1, a free provider key and "
        "OPSPILOT_LLM_MODEL to run"
    ),
)


def test_live_structured_generation() -> None:
    config = LLMConfig.from_env()
    provider = create_llm_provider(config)
    result = provider.generate_structured(
        system_instructions="Return a JSON object with status set to 'ok'.",
        user_input="Synthetic health check. Do not include incident data.",
        output_schema=SmokeAnswer,
        timeout=30.0,
        max_output_tokens=32,
    )
    assert isinstance(result.output, SmokeAnswer)
    assert result.output.status
    assert result.model
    assert result.metadata["provider"] == config.provider
