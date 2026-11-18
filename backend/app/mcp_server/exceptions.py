"""MCP-specific error types.

These are thin, client-safe error wrappers. They intentionally do not expose
internal details, credentials, or stack traces.
"""


class MCPToolError(ValueError):
    """Raised when an MCP tool receives invalid or unsupported input."""
