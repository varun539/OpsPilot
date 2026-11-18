from __future__ import annotations

from pathlib import Path

import anyio
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from backend.app.incidents import IncidentService
from backend.app.mcp_server import create_dependencies, create_server


def _run(coro):
    return anyio.run(coro)


def test_create_and_get_incident(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                created = await session.call_tool(
                    "create_incident",
                    {"title": "API 500", "severity": "high"},
                )
                assert created.is_error is False
                payload = created.structured_content
                incident_id = payload["id"]
                fetched = await session.call_tool("get_incident", {"incident_id": incident_id})
                assert fetched.is_error is False
                assert fetched.structured_content["title"] == "API 500"

    _run(main)


def test_list_incidents_and_filter(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("create_incident", {"title": "a", "severity": "high"})
                await session.call_tool("create_incident", {"title": "b", "severity": "low"})
                result = await session.call_tool(
                    "list_incidents", {"limit": 10, "severity": "high"}
                )
                assert result.is_error is False
                assert result.structured_content["count"] == 1

    _run(main)


def test_create_ticket_and_list(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                created = await session.call_tool("create_incident", {"title": "incident"})
                incident_id = created.structured_content["id"]
                ticket = await session.call_tool(
                    "create_ticket",
                    {"incident_id": incident_id, "title": "ticket", "priority": "high"},
                )
                assert ticket.is_error is False
                ticket_id = ticket.structured_content["id"]
                fetched = await session.call_tool("get_ticket", {"ticket_id": ticket_id})
                assert fetched.is_error is False
                listed = await session.call_tool(
                    "list_incident_tickets", {"incident_id": incident_id}
                )
                assert listed.structured_content["count"] == 1

    _run(main)


def test_missing_records_handled(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_incident", {"incident_id": "missing"})
                assert result.is_error is True
                ticket = await session.call_tool("get_ticket", {"ticket_id": "missing"})
                assert ticket.is_error is True
                investigation = await session.call_tool(
                    "get_investigation", {"investigation_id": "missing"}
                )
                assert investigation.is_error is True

    _run(main)


def test_incident_history_and_investigation_evidence(tmp_path: Path) -> None:
    db_path = str(tmp_path / "app.sqlite")
    service = IncidentService(db_path=db_path)
    incident = service.create_incident(title="x")
    investigation = service.create_investigation(incident_id=incident.id)
    report = {
        "summary": "done",
        "root_cause_hypotheses": [{"title": "db pool"}],
        "confidence": 0.9,
    }
    service.persist_investigation_report(investigation.id, report)
    service.store_evidence(
        investigation.id, "log", source="logs", content={"pattern": "boom"}
    )

    async def main():
        deps = create_dependencies(db_path=db_path)
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                history = await session.call_tool(
                    "get_incident_history", {"incident_id": incident.id}
                )
                assert history.is_error is False
                payload = history.structured_content
                assert payload["incident"]["id"] == incident.id
                assert len(payload["investigations"]) == 1
                inv = await session.call_tool(
                    "get_investigation", {"investigation_id": investigation.id}
                )
                assert inv.is_error is False
                assert inv.structured_content["report"]["summary"] == "done"
                assert inv.structured_content["suspected_root_cause"] == "db pool"
                evidence = await session.call_tool(
                    "list_investigation_evidence",
                    {"investigation_id": investigation.id},
                )
                assert evidence.structured_content["count"] == 1
                assert evidence.structured_content["evidence"][0]["content"]["pattern"] == "boom"

    _run(main)


def test_invalid_incident_reference_rejected(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "create_ticket", {"incident_id": "missing", "title": "t"}
                )
                assert result.is_error is True

    _run(main)


def test_application_db_separate_from_checkpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite"
    checkpoint_path = tmp_path / "checkpoint.sqlite"
    async def main():
        deps = create_dependencies(db_path=str(db_path))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("create_incident", {"title": "x"})
    _run(main)
    assert db_path.exists()
    assert not checkpoint_path.exists()


def test_knowledge_base_search_returns_results(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "search_knowledge_base",
                    {"query": "database connection pool", "top_k": 3},
                )
                assert result.is_error is False
                payload = result.structured_content
                assert payload["result_count"] <= 3
                if payload["result_count"] > 0:
                    first = payload["results"][0]
                    assert "chunk_id" in first
                    assert "doc_id" in first

    _run(main)


def test_knowledge_base_search_empty_result_handled(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        # Inject a fake search engine returning no results.
        class FakeEngine:
            def search(self, *args, **kwargs):
                return []
        deps.search_engine = FakeEngine()
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "search_knowledge_base",
                    {"query": "zzzz", "top_k": 2},
                )
                assert result.is_error is False
                assert result.structured_content["result_count"] == 0

    _run(main)


def test_top_k_limit_enforced(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        class FakeEngine:
            def search(self, query, top_k=5, filters=None):
                return []
        deps.search_engine = FakeEngine()
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "search_knowledge_base",
                    {"query": "x", "top_k": 100},
                )
                assert result.is_error is True

    _run(main)


def test_input_length_limit_enforced(tmp_path: Path) -> None:
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "analyze_logs", {"log_text": "x" * 300000}
                )
                assert result.is_error is True

    _run(main)