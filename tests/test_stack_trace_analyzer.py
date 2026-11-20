from __future__ import annotations

from backend.app.investigation_tools import analyze_stack_trace

TRACE = """Traceback (most recent call last):
  File "app/main.py", line 42, in handler
    result = service.execute()
RuntimeError: database connection failed
"""


def test_standard_trace():
    r = analyze_stack_trace(TRACE)
    assert r.metadata["final_exception"] == "RuntimeError"
    assert r.metadata["frames"][0]["file"] == "app/main.py"
    assert r.metadata["frames"][0]["line"] == 42


def test_multiple_frames():
    text = """Traceback (most recent call last):
  File "a.py", line 1, in f
    g()
  File "b.py", line 2, in g
    raise ValueError("x")
ValueError: x
"""
    r = analyze_stack_trace(text)
    assert r.metadata["frame_count"] == 2


def test_nested_chained():
    text = """Traceback (most recent call last):
  File "a.py", line 1, in f
    g()
ValueError: boom

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "b.py", line 2, in h
    raise RuntimeError("wrap")
RuntimeError: wrap
"""
    r = analyze_stack_trace(text)
    assert r.metadata["trace_count"] == 2
    assert r.metadata["root_exception"] == "ValueError"
    assert r.metadata["final_exception"] == "RuntimeError"
    assert any(f.code == "STACKTRACE_EXCEPTION_CHAIN" for f in r.findings)


def test_incomplete_trace():
    text = "Traceback (most recent call last):\n  File \"a.py\", line 1, in f\n    g()\n"
    r = analyze_stack_trace(text)
    assert r.metadata["incomplete_trace_count"] >= 1


def test_multiple_tracebacks():
    text = TRACE + "\n" + TRACE
    r = analyze_stack_trace(text)
    assert r.metadata["trace_count"] == 2


def test_exact_file_line():
    r = analyze_stack_trace(TRACE)
    assert r.metadata["failure_location"] == "app/main.py:42"
    location = next(f for f in r.findings if f.code == "STACKTRACE_FAILURE_LOCATION")
    assert "app/main.py:42" in location.evidence[0]
    assert r.metadata["frames"][0]["function"] == "handler"


def test_empty():
    r = analyze_stack_trace("")
    assert r.status == "empty"
