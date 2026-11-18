from __future__ import annotations

from pathlib import Path

import anyio
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from backend.app.mcp_server import create_dependencies, create_server


def _run(coro):
    return anyio.run(coro)


def _call(tmp_path: Path, name: str, arguments: dict):
    async def main():
        deps = create_dependencies(db_path=str(tmp_path / "app.sqlite"))
        server = create_server(deps)
        async with InMemoryTransport(server) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)

    return _run(main)


def test_analyze_logs_tool(tmp_path: Path) -> None:
    result = _call(tmp_path, "analyze_logs", {"log_text": "ERROR boom\nINFO ok"})
    assert result.is_error is False
    payload = result.structured_content or {}
    assert payload["tool_name"] == "log_analyzer"
    assert payload["analyzed_items"] >= 1


def test_analyze_stack_trace_tool(tmp_path: Path) -> None:
    trace = (
        "Traceback (most recent call last):\n"
        '  File "a.py", line 1, in f\n'
        "    x()\n"
        "ValueError: bad\n"
    )
    result = _call(tmp_path, "analyze_stack_trace", {"trace_text": trace})
    assert result.is_error is False
    payload = result.structured_content or {}
    assert payload["tool_name"] == "stack_trace_analyzer"


def test_classify_http_error_tool(tmp_path: Path) -> None:
    result = _call(tmp_path, "classify_http_error", {"status_code": 500})
    assert result.is_error is False
    payload = result.structured_content or {}
    assert payload["metadata"]["status_code"] == 500


def test_validate_configuration_tool(tmp_path: Path) -> None:
    result = _call(
        tmp_path,
        "validate_configuration",
        {"config": {"DATABASE_URL": "https://example.com", "JWT_SECRET": "x" * 40}},
    )
    assert result.is_error is False
    payload = result.structured_content or {}
    assert payload["tool_name"] == "config_validator"


def test_diagnose_database_error_tool(tmp_path: Path) -> None:
    result = _call(
        tmp_path,
        "diagnose_database_error",
        {"error_text": "connection refused to database"},
    )
    assert result.is_error is False
    payload = result.structured_content or {}
    assert payload["tool_name"] == "database_diagnostics"


def test_correlate_incidents_tool(tmp_path: Path) -> None:
    events = [{"event_id": "a", "message": "boom"}, {"event_id": "b", "message": "boom"}]
    result = _call(tmp_path, "correlate_incidents", {"events": events})
    assert result.is_error is False
    payload = result.structured_content or {}
    assert payload["tool_name"] == "incident_correlator"


def test_invalid_input_returns_error(tmp_path: Path) -> None:
    result = _call(tmp_path, "classify_http_error", {"status_code": 9999})
    assert result.is_error is True


def test_secret_redaction_remains_effective(tmp_path: Path) -> None:
    result = _call(
        tmp_path,
        "analyze_logs",
        {"log_text": "ERROR password=supersecret123", "redact_secrets": True},
    )
    assert result.is_error is False
    text = result.content[0].text
    assert "supersecret123" not in text