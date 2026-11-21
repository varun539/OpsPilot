"""Adversarial authorization tests (Phase 25).

Exercises vertical authorization (role ceilings) and the MCP tool boundary.
There is no tenant or ownership model in OpsPilot, so horizontal isolation is
asserted to be absent rather than invented.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from backend.app.agent.tool_registry import TOOL_NAMES, execute_tool
from backend.app.auth.config import AuthSettings
from backend.app.auth.dependencies import get_current_principal
from backend.app.auth.models import Principal
from backend.app.main import create_app
from backend.app.mcp_server import create_dependencies, create_server
from backend.app.mcp_server.exceptions import MCPToolError
from tests.security_harness import SecurityMetrics

EXPECTED_TOOLS = {
    "log_analyzer",
    "stack_trace_analyzer",
    "http_error_classifier",
    "config_validator",
    "database_diagnostics",
    "incident_correlator",
}

FORBIDDEN_TOOL_NAMES = (
    "shell",
    "run_shell",
    "bash",
    "python",
    "execute",
    "execute_sql",
    "read_file",
    "../../etc/passwd",
    "approve_investigation",
    "resolve_incident",
    "delete_incident",
    "admin",
)


class FakeProvider:
    model = "fake"

    def generate_structured(
        self,
        *,
        system_instructions,
        user_input,
        output_schema,
        timeout=None,
        max_output_tokens=None,
    ):
        from backend.app.agent.models import InvestigationPlan
        from backend.app.rag.models import InvestigationReport

        if output_schema.__name__ == "InvestigationPlan":
            output = InvestigationPlan(selected_tools=[], rationale="synthetic")
        else:
            output = InvestigationReport(
                summary="s",
                incident_category="x",
                evidence_status="insufficient",
            )
        result = MagicMock()
        result.output = output
        result.usage = None
        result.model = "fake"
        return result


def _app(tmp_path, *, dev_auth: bool = True, **kwargs):
    settings = AuthSettings(
        auth_mode="development", dev_auth_enabled=dev_auth, host="127.0.0.1"
    )
    return create_app(
        db_path=str(tmp_path / "app.sqlite"),
        checkpoint_db_path=str(tmp_path / "checkpoints.sqlite"),
        auth_settings=settings,
        llm_provider=FakeProvider(),
        **kwargs,
    )


def _as(app, subject: str, role: str) -> None:
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject=subject, roles=[role]
    )


def test_unauthenticated_access_is_denied_on_every_route(tmp_path) -> None:
    metrics = SecurityMetrics()
    app = _app(tmp_path, dev_auth=False)
    calls = [
        ("GET", "/api/v1/incidents"),
        ("POST", "/api/v1/incidents"),
        ("GET", "/api/v1/incidents/any"),
        ("PATCH", "/api/v1/incidents/any"),
        ("GET", "/api/v1/incidents/any/history"),
        ("POST", "/api/v1/incidents/any/investigations"),
        ("POST", "/api/v1/investigations/any/approval"),
        ("GET", "/api/v1/investigations/any"),
        ("GET", "/api/v1/dashboard/stats"),
        ("GET", "/api/v1/approvals"),
        ("GET", "/api/v1/tickets"),
    ]
    with TestClient(app) as client:
        for method, path in calls:
            response = client.request(method, path, json={} if method != "GET" else None)
            if response.status_code != 401:
                metrics.violation(
                    "authorization_bypasses",
                    f"{method} {path} returned {response.status_code} without a token",
                )
            metrics.rejected()
    metrics.assert_clean()


def test_viewer_is_denied_every_mutating_endpoint(tmp_path) -> None:
    metrics = SecurityMetrics()
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = client.post("/api/v1/incidents", json={"title": "synthetic"}).json()
        ticket = client.post(
            f"/api/v1/incidents/{incident['id']}/tickets", json={"title": "t"}
        ).json()
        investigation = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()

        _as(app, "viewer-user", "viewer")
        denied = [
            ("POST", "/api/v1/incidents", {"title": "blocked"}),
            ("PATCH", f"/api/v1/incidents/{incident['id']}", {"title": "blocked"}),
            ("POST", f"/api/v1/incidents/{incident['id']}/tickets", {"title": "blocked"}),
            ("POST", f"/api/v1/incidents/{incident['id']}/investigations", {}),
            (
                "POST",
                f"/api/v1/investigations/{investigation['investigation_id']}/approval",
                {"decision": "approved"},
            ),
            ("POST", f"/api/v1/tickets/{ticket['id']}/close", None),
        ]
        for method, path, body in denied:
            response = client.request(method, path, json=body)
            if response.status_code != 403:
                metrics.violation(
                    "authorization_bypasses",
                    f"viewer reached {method} {path} ({response.status_code})",
                )
            metrics.rejected()

        # Read access is intentionally available to viewers.
        for path in (
            "/api/v1/incidents",
            f"/api/v1/incidents/{incident['id']}",
            f"/api/v1/investigations/{investigation['investigation_id']}",
            "/api/v1/dashboard/stats",
            "/api/v1/tickets",
        ):
            assert client.get(path).status_code == 200
    metrics.assert_clean()


def test_viewer_cannot_approve_or_reject(tmp_path) -> None:
    metrics = SecurityMetrics()
    app = _app(tmp_path)
    with TestClient(app) as client:
        incident = client.post("/api/v1/incidents", json={"title": "synthetic"}).json()
        created = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        _as(app, "viewer-user", "viewer")
        for decision in ("approved", "rejected"):
            response = client.post(
                f"/api/v1/investigations/{created['investigation_id']}/approval",
                json={"decision": decision},
            )
            if response.status_code != 403:
                metrics.violation(
                    "authorization_bypasses", f"viewer recorded decision {decision}"
                )
            metrics.rejected()
        # The approval must still be pending after the denied attempts.
        assert client.get(
            f"/api/v1/investigations/{created['investigation_id']}/approval"
        ).json()["status"] == "pending"
    metrics.assert_clean()


def test_operator_workflow_is_allowed(tmp_path) -> None:
    metrics = SecurityMetrics()
    app = _app(tmp_path)
    with TestClient(app) as client:
        _as(app, "operator-a", "operator")
        incident = client.post("/api/v1/incidents", json={"title": "synthetic"})
        assert incident.status_code == 201
        incident_id = incident.json()["id"]
        assert client.patch(
            f"/api/v1/incidents/{incident_id}", json={"priority": "high"}
        ).status_code == 200
        ticket = client.post(
            f"/api/v1/incidents/{incident_id}/tickets", json={"title": "t"}
        )
        assert ticket.status_code == 201
        assert client.post(f"/api/v1/tickets/{ticket.json()['id']}/close").status_code == 200
        started = client.post(f"/api/v1/incidents/{incident_id}/investigations", json={})
        assert started.status_code == 202
        decision = client.post(
            f"/api/v1/investigations/{started.json()['investigation_id']}/approval",
            json={"decision": "approved"},
        )
        assert decision.status_code == 200
        metrics.accepted()
    metrics.assert_clean()


def test_admin_role_has_no_extra_undeclared_routes(tmp_path) -> None:
    metrics = SecurityMetrics()
    app = _app(tmp_path)
    with TestClient(app) as client:
        _as(app, "admin-user", "admin")
        # Admin implies operator, but there is no hidden admin surface.
        assert client.post("/api/v1/incidents", json={"title": "synthetic"}).status_code == 201
        assert client.get("/api/v1/admin/users").status_code == 404
        assert client.get("/api/v1/admin/audit").status_code == 404
        metrics.accepted()
    metrics.assert_clean()


def test_approval_state_machine_rejects_unapproved_transitions(tmp_path) -> None:
    metrics = SecurityMetrics()
    app = _app(tmp_path)
    with TestClient(app) as client:
        _as(app, "operator-a", "operator")
        incident = client.post("/api/v1/incidents", json={"title": "synthetic"}).json()
        started = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        url = f"/api/v1/investigations/{started['investigation_id']}/approval"
        assert client.post(url, json={"decision": "approved"}).status_code == 200
        conflicting = client.post(url, json={"decision": "rejected"})
        if conflicting.status_code == 200:
            metrics.violation(
                "authorization_bypasses", "conflicting decision overwrote a recorded approval"
            )
        metrics.rejected()
    metrics.assert_clean()


def test_any_operator_can_decide_any_investigation(tmp_path) -> None:
    """Documents the known absence of a per-user ownership boundary."""
    metrics = SecurityMetrics()
    app = _app(tmp_path)
    with TestClient(app) as client:
        _as(app, "operator-a", "operator")
        incident = client.post("/api/v1/incidents", json={"title": "synthetic"}).json()
        started = client.post(
            f"/api/v1/incidents/{incident['id']}/investigations", json={}
        ).json()
        _as(app, "operator-b", "operator")
        response = client.post(
            f"/api/v1/investigations/{started['investigation_id']}/approval",
            json={"decision": "approved"},
        )
        # Current design: operator authority is global, not per-incident.
        assert response.status_code == 200
        metrics.accepted()
    metrics.assert_clean()


def test_agent_tool_allowlist_is_exact() -> None:
    metrics = SecurityMetrics()
    assert set(TOOL_NAMES) == EXPECTED_TOOLS
    state = {"request": {"logs": "ERROR synthetic"}, "available_inputs": ["logs"]}
    for name in FORBIDDEN_TOOL_NAMES:
        try:
            result = execute_tool(name, state)
        except (ValueError, TypeError):
            metrics.rejected()
            continue
        # A forbidden tool that returns a result is an unauthorized execution.
        metrics.violation(
            "unauthorized_tool_executions", f"tool {name!r} executed and returned {result!r}"
        )
    metrics.assert_clean()


def test_mcp_tool_surface_is_exactly_the_contract(tmp_path) -> None:
    metrics = SecurityMetrics()

    async def main() -> None:
        deps = create_dependencies(db_path=str(tmp_path / "mcp.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                for forbidden in (
                    "run_shell",
                    "execute_sql",
                    "read_file",
                    "approve_investigation",
                    "delete_incident",
                    "resolve_incident",
                ):
                    if forbidden in names:
                        metrics.violation(
                            "unauthorized_tool_executions",
                            f"MCP exposed forbidden tool {forbidden!r}",
                        )
                # The graph allowlist and the MCP adapter surface use different
                # names for the same six diagnostic capabilities.
                assert {
                    "analyze_logs",
                    "analyze_stack_trace",
                    "classify_http_error",
                    "validate_configuration",
                    "diagnose_database_error",
                    "correlate_incidents",
                }.issubset(names)
                assert len(names) == 17, sorted(names)

                # The server-side service boundary still validates arguments.
                result = await session.call_tool("get_ticket", {"ticket_id": "missing"})
                assert result.is_error, "MCP returned success for a missing ticket"
                metrics.rejected()

                result = await session.call_tool(
                    "search_knowledge_base",
                    {"query": "synthetic", "filters": {"evil_field": "x"}},
                )
                assert result.is_error, "unsupported filter field was accepted"
                metrics.rejected()

    anyio.run(main)
    metrics.assert_clean()


def test_mcp_diagnostic_errors_are_contained(tmp_path) -> None:
    metrics = SecurityMetrics()

    async def main() -> None:
        deps = create_dependencies(db_path=str(tmp_path / "mcp.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("analyze_logs", {"log_text": ""})
                assert result.is_error
                # The client sees a generic message, never a traceback or input.
                rendered = " ".join(
                    getattr(part, "text", "") for part in result.content
                )
                assert "Traceback" not in rendered
                assert "log_text" not in rendered
                metrics.rejected()

    anyio.run(main)
    metrics.assert_clean()


def test_mcp_tool_error_type_is_dedicated() -> None:
    assert issubclass(MCPToolError, Exception)
    assert not issubclass(MCPToolError, (SystemExit, KeyboardInterrupt))


@pytest.mark.parametrize("name", FORBIDDEN_TOOL_NAMES)
def test_unregistered_tool_names_never_resolve(name: str) -> None:
    metrics = SecurityMetrics()
    from backend.app.agent.tool_registry import get_tool_callable

    assert get_tool_callable(name) is None
    metrics.rejected()
    metrics.assert_clean()
