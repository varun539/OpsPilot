"""Dependency construction for the OpsPilot MCP server.

Dependencies are created when a server is constructed, not when this module is
imported. This keeps import side effects minimal and allows tests to inject
isolated databases and retrieval engines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..incidents import IncidentService


@dataclass
class MCPDependencies:
    """Shared dependencies for MCP tool adapters."""

    incident_service: IncidentService
    search_engine: Any = None
    search_factory: Callable[[], Any] | None = None
    _search_cache: Any = field(default=None, init=False, repr=False)

    def get_search_engine(self) -> Any:
        """Return the configured retrieval engine, creating it lazily."""
        if self.search_engine is not None:
            return self.search_engine
        if self._search_cache is not None:
            return self._search_cache
        if self.search_factory is not None:
            engine = self.search_factory()
            self._search_cache = engine
            return engine
        from ..retrieval import HybridSearchEngine

        engine = HybridSearchEngine()
        self._search_cache = engine
        return engine


def create_dependencies(
    *,
    db_path: str | None = None,
    incident_service: IncidentService | None = None,
    search_engine: Any = None,
    search_factory: Callable[[], Any] | None = None,
) -> MCPDependencies:
    """Create MCP dependencies with an isolated application database by default."""
    if incident_service is None:
        incident_service = IncidentService(db_path=db_path)
    return MCPDependencies(
        incident_service=incident_service,
        search_engine=search_engine,
        search_factory=search_factory,
    )
