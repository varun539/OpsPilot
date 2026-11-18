"""Exceptions for OpsPilot investigation orchestration."""


class OrchestrationError(Exception):
    """Base class for orchestration failures."""


class InvestigationStartError(OrchestrationError):
    """Raised when an investigation cannot be started."""


class InvestigationResumeError(OrchestrationError):
    """Raised when a workflow cannot be resumed."""


class InvestigationNotFoundError(OrchestrationError):
    """Raised when an investigation cannot be found."""


class AIServiceUnavailableError(OrchestrationError):
    """Raised when investigations cannot run because the AI runtime is not ready."""
