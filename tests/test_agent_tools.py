from __future__ import annotations

from unittest.mock import patch

from backend.app.agent.tool_registry import execute_tool


def test_tool_invocation():
    state = {"request": {"logs": "ERROR boom"}, "available_inputs": ["logs"]}
    result = execute_tool("log_analyzer", state)
    assert "findings" in result


def test_missing_input_skipped():
    state = {"request": {}, "available_inputs": []}
    try:
        execute_tool("log_analyzer", state)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_isolated_failures():
    state = {"request": {"logs": "ERROR boom"}, "available_inputs": ["logs"]}
    with patch("backend.app.agent.tool_registry.analyze_logs", side_effect=RuntimeError("boom")):
        try:
            execute_tool("log_analyzer", state)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")