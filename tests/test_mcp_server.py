from __future__ import annotations

from pathlib import Path

import anyio
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from backend.app.mcp_server import create_dependencies, create_server


def _run(coro):
    return anyio.run(coro)


def test_server_initializes_and_lists_tools(tmp_path: Path) -> None:
    async def main() -> None:
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                assert len(result.tools) == 17
                names = {tool.name for tool in result.tools}
                assert "search_knowledge_base" in names
                assert "create_incident" in names
                for tool in result.tools:
                    assert tool.description
                    assert tool.input_schema

    _run(main)


def test_tool_schemas_are_discoverable(tmp_path: Path) -> None:
    async def main() -> None:
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                search_tool = next(t for t in result.tools if t.name == "search_knowledge_base")
                assert "query" in search_tool.input_schema.get("properties", {})
                assert "top_k" in search_tool.input_schema.get("properties", {})

    _run(main)


def test_no_destructive_or_approval_tools_exposed(tmp_path: Path) -> None:
    async def main() -> None:
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                names = {tool.name for tool in result.tools}
                forbidden = {
                    "approve",
                    "reject",
                    "resolve_incident",
                    "execute_sql",
                    "run_shell",
                    "delete_incident",
                }
                assert forbidden.isdisjoint(names)

    _run(main)