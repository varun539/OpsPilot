"""Deterministic Python stack trace analyzer.

The analyzer parses traceback text only. It never executes or imports anything
referenced by the trace, and it does not claim to know a root cause from a
traceback alone.
"""

from __future__ import annotations

import re
from typing import Any

from .models import DiagnosticFinding, DiagnosticResult, InputSizeError, Severity
from .sanitization import redact_text, split_lines_bounded, truncate_text

TOOL_NAME = "stack_trace_analyzer"

_TRACEBACK_HEADER_RE = re.compile(r"^\s*Traceback \(most recent call last\):\s*$")
_FRAME_RE = re.compile(
    r"""^\s*File\s+(?P<quote>["'])(?P<file>.+?)(?P=quote),\s+line\s+(?P<line>\d+)"""
    r"""(?:,\s+in\s+(?P<func>.+?))?\s*$"""
)
# A line that looks like ``SomeError: message`` at column zero.
_EXCEPTION_RE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<message>.*))?$")
_EXCEPTION_SUFFIXES = ("Error", "Exception", "Warning", "Fault", "Panic", "Exit", "Interrupt")

_CHAIN_MARKERS = (
    (
        re.compile(r"direct cause of the following exception", re.IGNORECASE),
        "direct_cause",
    ),
    (
        re.compile(r"during handling of the above exception", re.IGNORECASE),
        "during_handling",
    ),
)


def _exception_line(line: str) -> tuple[str, str] | None:
    """Return (type, message) when the line is a traceback terminator."""
    stripped = line.rstrip()
    if not stripped or line[:1] in (" ", "\t"):
        return None
    if _TRACEBACK_HEADER_RE.match(stripped):
        return None
    match = _EXCEPTION_RE.match(stripped)
    if not match:
        return None
    exc_type = match.group("type")
    looks_qualified = "." in exc_type
    looks_typed = exc_type.endswith(_EXCEPTION_SUFFIXES)
    if not (looks_qualified or looks_typed):
        return None
    return exc_type, (match.group("message") or "").strip()


def _chain_marker(line: str) -> str | None:
    for regex, kind in _CHAIN_MARKERS:
        if regex.search(line):
            return kind
    return None


def _parse_sections(lines: list[str]) -> tuple[list[dict[str, Any]], int]:
    """Parse traceback sections.

    Returns the sections and the number of frames that appear outside any
    traceback header (an incomplete trace supplied without its header).
    """
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_chain: str | None = None
    orphan_frames = 0
    index = 0

    def start_section() -> dict[str, Any]:
        return {
            "frames": [],
            "exception_type": None,
            "exception_message": "",
            "chain": pending_chain,
            "complete": False,
        }

    while index < len(lines):
        line = lines[index]

        if _TRACEBACK_HEADER_RE.match(line):
            if current is not None and (current["frames"] or current["exception_type"]):
                sections.append(current)
            current = start_section()
            index += 1
            continue

        marker = _chain_marker(line)
        if marker is not None:
            if current is not None and (current["frames"] or current["exception_type"]):
                sections.append(current)
                current = None
            pending_chain = marker
            index += 1
            continue

        frame_match = _FRAME_RE.match(line)
        if frame_match:
            frame: dict[str, Any] = {
                "file": frame_match.group("file"),
                "line": int(frame_match.group("line")),
                "function": (frame_match.group("func") or "<module>").strip(),
            }
            # The next indented line is the source snippet for this frame.
            if index + 1 < len(lines):
                candidate = lines[index + 1]
                if candidate[:1] in (" ", "\t") and not _FRAME_RE.match(candidate):
                    snippet = candidate.strip()
                    if snippet and not snippet.startswith("^"):
                        frame["code"] = snippet
                        index += 1
            if current is None:
                orphan_frames += 1
                current = start_section()
            current["frames"].append(frame)
            index += 1
            continue

        if current is None:
            index += 1
            continue

        parsed_exception = _exception_line(line)
        if parsed_exception is not None:
            current["exception_type"], current["exception_message"] = parsed_exception
            current["complete"] = True
            sections.append(current)
            current = None
            pending_chain = None
            index += 1
            continue

        index += 1

    if current is not None and (current["frames"] or current["exception_type"]):
        sections.append(current)

    return sections, orphan_frames


def analyze_stack_trace(trace_text: str, **options: Any) -> DiagnosticResult:
    """Analyze untrusted stack-trace text and return structured findings.

    Options (all optional):
        max_input_chars / max_chars
        max_lines
        redact / redact_secrets (default True)
        truncate (bool, default True)
    """
    max_input_chars = int(options.get("max_input_chars", options.get("max_chars", 200_000)))
    max_lines = int(options.get("max_lines", options.get("max_input_lines", 10_000)))
    redact = bool(options.get("redact", options.get("redact_secrets", True)))
    allow_truncate = bool(options.get("truncate", True))

    warnings: list[str] = []
    text = "" if trace_text is None else str(trace_text)

    if len(text) > max_input_chars:
        if not allow_truncate:
            raise InputSizeError(
                f"input exceeds max_input_chars ({len(text)} > {max_input_chars})"
            )
        text, _ = truncate_text(text, max_input_chars)
        warnings.append(f"Input truncated to {max_input_chars} characters.")

    lines, lines_truncated = split_lines_bounded(text, max_lines)
    if lines_truncated:
        warnings.append(f"Input truncated to {max_lines} lines.")

    if not text.strip():
        return DiagnosticResult(
            tool_name=TOOL_NAME,
            status="empty",
            summary="No stack trace content was provided.",
            findings=[],
            analyzed_items=0,
            warnings=warnings,
            metadata={"trace_count": 0, "truncated": False},
        )

    sections, orphan_frames = _parse_sections(lines)
    if orphan_frames:
        warnings.append(
            "Stack frames were found without a 'Traceback' header; the trace may be incomplete."
        )

    values = [s for s in sections if s["exception_type"]]
    root = next((s for s in values if s["chain"] != "direct_cause"), values[0] if values else None)
    final = values[-1] if values else None
    incomplete = [s for s in sections if not s["complete"]]
    if incomplete:
        warnings.append(f"{len(incomplete)} traceback section(s) appear incomplete.")

    frames = [
        {
            "trace_index": section_index,
            "file": frame["file"],
            "function": frame["function"],
            "line": frame["line"],
            **({"code": frame["code"]} if "code" in frame else {}),
        }
        for section_index, section in enumerate(sections)
        for frame in section["frames"]
    ]
    failure_frame = None
    if final is not None and final["frames"]:
        failure_frame = final["frames"][-1]
    elif frames:
        failure_frame = frames[-1]

    findings: list[DiagnosticFinding] = []

    if final is not None:
        message = final["exception_message"]
        if redact:
            message = redact_text(message)
        detail = f": {message}" if message else ""
        findings.append(
            DiagnosticFinding(
                code="STACKTRACE_FINAL_EXCEPTION",
                title="Final exception identified",
                description=f"Final exception: {final['exception_type']}{detail}",
                severity=Severity.high,
                evidence=[
                    f"{frame['file']}:{frame['line']} in {frame['function']}"
                    for frame in final["frames"][-3:]
                ],
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Inspect the deepest application frame in the traceback.",
                    "Reproduce the failure with the original inputs.",
                ],
                observed_facts=[f"Final exception type: {final['exception_type']}."],
                detected_patterns=["python_traceback"],
                potential_explanations=[
                    "The final exception shows where the failure surfaced, not necessarily "
                    "why it occurred.",
                ],
                recommended_next_steps=[
                    "Inspect the first frame inside application code for the failing state.",
                ],
            )
        )

    if failure_frame is not None:
        findings.append(
            DiagnosticFinding(
                code="STACKTRACE_FAILURE_LOCATION",
                title="Relevant failure location",
                description=(
                    f"Deepest frame: {failure_frame['file']}:{failure_frame['line']} "
                    f"in {failure_frame['function']}"
                ),
                severity=Severity.informational,
                evidence=[
                    f"{failure_frame['file']}:{failure_frame['line']} "
                    f"in {failure_frame['function']}"
                ],
                source=TOOL_NAME,
                remediation_suggestions=["Start the investigation at this location."],
                observed_facts=[
                    f"Deepest frame: {failure_frame['file']}:{failure_frame['line']}."
                ],
                recommended_next_steps=[
                    "Treat upstream frames as the call path, not separate defects.",
                ],
            )
        )

    if final is None and sections:
        findings.append(
            DiagnosticFinding(
                code="STACKTRACE_INCOMPLETE",
                title="Incomplete stack trace",
                description=(
                    "A traceback was found but no terminating exception line could be "
                    "identified."
                ),
                severity=Severity.medium,
                evidence=[
                    (redact_text(line) if redact else line)
                    for line in lines[:5]
                    if line.strip()
                ],
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Obtain the complete traceback including the exception line."
                ],
                observed_facts=["Traceback present without a terminating exception."],
                recommended_next_steps=["Re-capture the error output in full."],
            )
        )

    if len(sections) > 1:
        findings.append(
            DiagnosticFinding(
                code="STACKTRACE_MULTIPLE_SECTIONS",
                title="Multiple traceback sections detected",
                description=f"Found {len(sections)} traceback section(s).",
                severity=Severity.informational,
                evidence=[
                    f"section {number}: {section['exception_type'] or 'unknown'}"
                    for number, section in enumerate(sections, start=1)
                ],
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Review exception chaining to separate the root failure from the final one."
                ],
                observed_facts=[f"{len(sections)} traceback sections."],
                detected_patterns=["multiple_tracebacks"],
            )
        )

    chained = [section for section in sections if section["chain"]]
    if chained:
        findings.append(
            DiagnosticFinding(
                code="STACKTRACE_EXCEPTION_CHAIN",
                title="Exception chaining detected",
                description="Traceback sections are linked by exception chaining.",
                severity=Severity.informational,
                evidence=[
                    f"{section['chain']}: {section['exception_type']}"
                    for section in chained
                ],
                source=TOOL_NAME,
                remediation_suggestions=[
                    "Identify the original exception and the wrapping mechanism."
                ],
                observed_facts=["Traceback sections are chained."],
                detected_patterns=["exception_chaining"],
            )
        )

    status = "issues_found" if final is not None else "parsed"
    summary = (
        f"Analyzed {len(sections)} traceback section(s) with {len(frames)} frame(s)."
    )
    if final is not None:
        summary += f" Final exception: {final['exception_type']}."

    metadata: dict[str, Any] = {
        "trace_count": len(sections),
        "frame_count": len(frames),
        "frames": frames,
        "final_exception": final["exception_type"] if final else None,
        "final_exception_message": final["exception_message"] if final else None,
        "root_exception": root["exception_type"] if root else None,
        "root_exception_message": root["exception_message"] if root else None,
        "incomplete_trace_count": len(incomplete),
        "failure_location": (
            f"{failure_frame['file']}:{failure_frame['line']}" if failure_frame else None
        ),
        "truncated": bool(warnings),
    }

    observed_facts: list[str] = []
    if final is not None:
        observed_facts.append(f"Final exception type: {final['exception_type']}.")
    if frames:
        observed_facts.append(
            f"{len(frames)} stack frame(s) across {len(sections)} section(s)."
        )

    return DiagnosticResult(
        tool_name=TOOL_NAME,
        status=status,
        summary=summary,
        findings=findings,
        analyzed_items=len(sections),
        warnings=warnings,
        metadata=metadata,
        observed_facts=observed_facts,
        detected_patterns=["python_traceback"] if sections else [],
        potential_explanations=(
            [
                "A traceback shows the failing call path; it does not by itself "
                "establish the root cause."
            ]
            if sections
            else []
        ),
        recommended_next_steps=[
            "Inspect application frames and reproduce the failure.",
            "Check the inputs and dependencies involved in the deepest frame.",
        ],
    )
