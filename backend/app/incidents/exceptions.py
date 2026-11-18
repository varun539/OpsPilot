"""Exceptions for OpsPilot incident management."""


class IncidentError(Exception):
    """Base class for incident-management failures."""


class IncidentNotFoundError(IncidentError):
    """Raised when an incident cannot be found."""


class TicketNotFoundError(IncidentError):
    """Raised when a ticket cannot be found."""


class InvestigationNotFoundError(IncidentError):
    """Raised when an investigation cannot be found."""


class ApprovalNotFoundError(IncidentError):
    """Raised when an approval record cannot be found."""


class ResolutionNotFoundError(IncidentError):
    """Raised when a resolution record cannot be found."""


class InvalidStateTransitionError(IncidentError):
    """Raised when a lifecycle transition is not allowed."""


class InvalidInputError(IncidentError):
    """Raised when input data is invalid."""


class DuplicateRecordError(IncidentError):
    """Raised when a unique constraint is violated."""
