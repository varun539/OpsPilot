"""Authentication and authorization exceptions."""


class AuthError(Exception):
    """Base class for authentication failures."""


class InvalidTokenError(AuthError):
    """Raised when a bearer token cannot be verified."""


class AuthConfigurationError(AuthError):
    """Raised when required authentication configuration is missing."""


class AuthorizationError(Exception):
    """Raised when an authenticated principal lacks required permissions."""
