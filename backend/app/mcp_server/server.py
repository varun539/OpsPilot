"""Standalone OpsPilot MCP server."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from .dependencies import MCPDependencies, create_dependencies
from .tools import register_tools

logger = logging.getLogger("opspilot.mcp")


def create_server(deps: MCPDependencies | None = None) -> MCPServer:
    """Create and configure the OpsPilot MCP server."""
    if deps is None:
        deps = create_dependencies()
    server = MCPServer(
        name="opspilot",
        title="OpsPilot MCP Server",
        description="Controlled OpsPilot diagnostic, retrieval, and incident tools.",
        instructions=(
            "Use these tools to search OpsPilot knowledge, run deterministic "
            "diagnostics, and manage incidents and tickets through controlled "
            "service operations."
        ),
        log_level="WARNING",
    )
    register_tools(server, deps)
    return server


def main() -> None:
    """Run the MCP server over stdio."""
    # Keep application logs on stderr so stdout remains protocol-safe.
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
