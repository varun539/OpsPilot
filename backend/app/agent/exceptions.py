"""Exceptions for the OpsPilot LangGraph agent."""


class AgentError(Exception):
    """Base class for agent failures."""


class AgentConfigurationError(AgentError):
    """Raised when agent configuration is invalid."""


class AgentValidationError(AgentError):
    """Raised when an investigation request or plan is invalid."""


class AgentPlanningError(AgentError):
    """Raised when investigation planning fails."""


class AgentExecutionError(AgentError):
    """Raised when graph execution fails."""


class AgentApprovalError(AgentError):
    """Raised when an approval decision is invalid."""


class AgentCheckpointError(AgentError):
    """Raised when checkpoint persistence or recovery fails."""
