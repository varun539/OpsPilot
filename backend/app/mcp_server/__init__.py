"""Controlled MCP integration layer for OpsPilot."""

from __future__ import annotations

from .dependencies import MCPDependencies, create_dependencies
from .server import create_server

__all__ = ["MCPDependencies", "create_dependencies", "create_server"]
