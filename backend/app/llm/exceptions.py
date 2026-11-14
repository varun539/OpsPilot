"""Exceptions raised by OpsPilot LLM providers."""


class LLMError(Exception):
    """Base class for provider failures."""


class LLMConfigurationError(LLMError):
    """Raised when provider configuration is missing or invalid."""


class LLMTimeoutError(LLMError):
    """Raised when a provider request times out."""


class LLMAuthenticationError(LLMError):
    """Raised when provider authentication fails."""


class LLMRefusalError(LLMError):
    """Raised when the provider refuses to produce output."""


class LLMIncompleteOutputError(LLMError):
    """Raised when the provider response is incomplete or unparsed."""


class LLMResponseError(LLMError):
    """Raised when provider output cannot be interpreted."""
