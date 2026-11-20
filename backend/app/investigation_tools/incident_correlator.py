"""Deterministic incident correlation tool.

Correlation groups events by shared identifiers, matching error signatures,
shared dependencies, and timestamp proximity. Every group states the reason it
was proposed, its limitations, and alternative explanations: correlation never
proves causation.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from .models import CorrelationGroup, CorrelationResult
from .sanitization import redact_text

TOOL_NAME = "incident_correlator"
DEFAULT_TIME_WINDOW_MINUTES = 5.0

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
)

_CAUSATION_LIMITATION = "Correlation does not establish causation between these events."


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _get_field(obj: Any, *names: str) -> Any:
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        lowered = {str(key).lower(): value for key, value in obj.items()}
        for name in names:
            value = lowered.get(name.lower())
            if value is not None:
                return value
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text.rstrip("Z"), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _signature(message: Any) -> str | None:
    if message is None:
        return None
    normalized = " ".join(str(message).strip().lower().split())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _normalize_event(raw: Any, index: int) -> dict[str, Any]:
    identifier = _get_field(raw, "event_id", "id", "uid", "uuid")
    if not identifier:
        identifier = f"event-{index + 1}"
    timestamp_raw = _get_field(
        raw, "timestamp", "time", "ts", "@timestamp", "datetime", "created_at"
    )
    message = _get_field(raw, "message", "error", "description", "summary", "text")
    finding_refs = [str(value) for value in _as_list(_get_field(raw, "finding_ids"))]
    code = _get_field(raw, "code")
    finding_id = _get_field(raw, "finding_id")
    if finding_id:
        finding_refs.append(str(finding_id))
    if code:
        finding_refs.append(str(code))
    return {
        "id": str(identifier),
        "timestamp": _parse_timestamp(timestamp_raw),
        "timestamp_raw": timestamp_raw,
        "component": _string_or_none(_get_field(raw, "component", "service", "logger")),
        "message": _string_or_none(message),
        "correlation_id": _string_or_none(
            _get_field(raw, "correlation_id", "request_id", "trace_id", "req_id")
        ),
        "signature": _string_or_none(_get_field(raw, "signature", "error_signature"))
        or _signature(message),
        "dependencies": [
            str(dep)
            for dep in _as_list(_get_field(raw, "dependencies", "dependency", "related_services"))
        ],
        "finding_refs": sorted(set(finding_refs)),
    }


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _group_id(reason_key: str, event_ids: list[str]) -> str:
    """Stable group identifier derived from the reason and member events."""
    digest = hashlib.sha256(
        (reason_key + "|" + "|".join(sorted(event_ids))).encode("utf-8")
    ).hexdigest()
    return f"corr-{digest[:12]}"


def _timeline(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timed = sorted(
        (member for member in members if member["timestamp"] is not None),
        key=lambda member: (member["timestamp"], member["id"]),
    )
    untimed = sorted(
        (member for member in members if member["timestamp"] is None),
        key=lambda member: member["id"],
    )
    return [
        {
            "event_id": member["id"],
            "timestamp": (
                member["timestamp"].isoformat()
                if member["timestamp"] is not None
                else member["timestamp_raw"]
            ),
            "component": member["component"],
            "message": member["message"],
        }
        for member in [*timed, *untimed]
    ]


def _build_group(
    reason_key: str,
    reason: str,
    members: list[dict[str, Any]],
    shared_evidence: list[str],
    *,
    alternatives: list[str] | None = None,
    limitations: list[str] | None = None,
    dependency_notes: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> CorrelationGroup:
    event_ids = [member["id"] for member in members]
    return CorrelationGroup(
        group_id=_group_id(reason_key, event_ids),
        event_ids=event_ids,
        finding_ids=sorted({ref for member in members for ref in member["finding_refs"]}),
        shared_evidence=shared_evidence,
        timeline=_timeline(members),
        possible_dependency_relationships=dependency_notes or [],
        alternative_explanations=alternatives or [],
        limitations=[_CAUSATION_LIMITATION, *(limitations or [])],
        correlation_reason=reason,
        metadata=metadata or {},
    )


def correlate_incidents(events: Any, **options: Any) -> CorrelationResult:
    """Correlate supplied events/findings and return grouped evidence.

    Options (all optional):
        time_window_minutes / time_window (default 5)
        findings: extra diagnostic findings to reference
        redact / redact_secrets (default True)
    """
    findings_input = _as_list(options.get("findings", options.get("diagnostic_findings")))
    raw_window = options.get("time_window_minutes", options.get("time_window", 5))
    try:
        time_window_minutes = float(raw_window)
    except (TypeError, ValueError):
        time_window_minutes = DEFAULT_TIME_WINDOW_MINUTES
    time_window = timedelta(minutes=time_window_minutes)
    redact = bool(options.get("redact", options.get("redact_secrets", True)))

    warnings: list[str] = []
    raw_events = _as_list(events)

    if not raw_events and not findings_input:
        return CorrelationResult(
            tool_name=TOOL_NAME,
            status="empty",
            summary="No events or findings were provided for correlation.",
            correlation_groups=[],
            groups=[],
            analyzed_items=0,
            warnings=[],
            metadata={"event_count": 0, "group_count": 0},
        )

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_events):
        event = _normalize_event(raw, index)
        if event["id"] in seen_ids:
            unique_id = f"{event['id']}#{index + 1}"
            warnings.append(f"Duplicate event id {event['id']!r} renamed to {unique_id!r}.")
            event["id"] = unique_id
        seen_ids.add(event["id"])
        normalized.append(event)

    groups: list[CorrelationGroup] = []
    covered: set[frozenset[str]] = set()

    def add(group: CorrelationGroup) -> None:
        signature = frozenset(group.event_ids)
        if signature and signature in covered:
            return
        if signature:
            covered.add(signature)
        groups.append(group)

    # 1. Shared correlation / request / trace identifiers.
    by_correlation: dict[str, list[dict[str, Any]]] = {}
    for event in normalized:
        if event["correlation_id"]:
            by_correlation.setdefault(event["correlation_id"], []).append(event)
    for correlation_id, members in sorted(by_correlation.items()):
        if len(members) < 2:
            continue
        add(
            _build_group(
                "correlation_id",
                "Events share a correlation/request/trace ID.",
                members,
                [f"shared correlation/request ID: {correlation_id}"],
                alternatives=[
                    "A propagated ID links the requests but the failures may still be independent.",
                ],
                limitations=[
                    "A shared ID shows request linkage, not that one event caused another.",
                ],
                metadata={"correlation_id": correlation_id},
            )
        )

    # 2. Matching error signatures.
    by_signature: dict[str, list[dict[str, Any]]] = {}
    for event in normalized:
        if event["signature"]:
            by_signature.setdefault(event["signature"], []).append(event)
    for signature, members in sorted(by_signature.items()):
        if len(members) < 2:
            continue
        components = sorted({m["component"] for m in members if m["component"]})
        add(
            _build_group(
                "signature",
                "Events share an identical error signature.",
                members,
                [f"matching error signature: {signature}"],
                alternatives=[
                    "The same error text can be raised independently in different components.",
                ],
                limitations=[
                    "Identical messages may be produced by unrelated code paths.",
                ],
                dependency_notes=(
                    [f"Signature observed in: {', '.join(components)}"] if components else []
                ),
                metadata={"signature": signature},
            )
        )

    # 3. Shared dependencies, optionally bounded by timestamp proximity.
    dependency_groups: list[list[dict[str, Any]]] = []
    for first_index, first in enumerate(normalized):
        for second in normalized[first_index + 1 :]:
            shared = set(first["dependencies"]) & set(second["dependencies"])
            if not shared:
                continue
            within_window = True
            if first["timestamp"] is not None and second["timestamp"] is not None:
                within_window = (
                    abs((first["timestamp"] - second["timestamp"]).total_seconds())
                    <= time_window.total_seconds()
                )
            if not within_window:
                continue
            for group in dependency_groups:
                if any(member["id"] in {first["id"], second["id"]} for member in group):
                    for candidate in (first, second):
                        if all(candidate["id"] != member["id"] for member in group):
                            group.append(candidate)
                    break
            else:
                dependency_groups.append([first, second])

    for members in dependency_groups:
        common = set(members[0]["dependencies"])
        for member in members[1:]:
            common &= set(member["dependencies"])
        shared = sorted(common) or sorted(
            {dep for member in members for dep in member["dependencies"]}
        )
        add(
            _build_group(
                "dependency",
                "Events share dependencies and occur within the configured time window.",
                members,
                [f"shared dependency: {dependency}" for dependency in shared],
                alternatives=[
                    "Shared dependencies may be coincidental to the observed failures.",
                ],
                limitations=[
                    "A common dependency is a lead to investigate, not proof of a shared fault.",
                ],
                dependency_notes=["Possible common dependency: " + ", ".join(shared)]
                if shared
                else [],
                metadata={
                    "shared_dependencies": shared,
                    "time_window_minutes": time_window_minutes,
                },
            )
        )

    # 4. Time proximity within the same component. Time alone is never used to
    #    join events from different components; those need an explicit link.
    timed = sorted(
        (event for event in normalized if event["timestamp"] is not None),
        key=lambda event: (event["timestamp"], event["id"]),
    )
    by_component: dict[str, list[dict[str, Any]]] = {}
    for event in timed:
        if event["component"]:
            by_component.setdefault(event["component"], []).append(event)

    for component, component_events in sorted(by_component.items()):
        current: list[dict[str, Any]] = []
        clusters: list[list[dict[str, Any]]] = []
        for event in component_events:
            if not current:
                current = [event]
                continue
            gap = (event["timestamp"] - current[-1]["timestamp"]).total_seconds()
            if gap <= time_window.total_seconds():
                current.append(event)
            else:
                if len(current) >= 2:
                    clusters.append(current)
                current = [event]
        if len(current) >= 2:
            clusters.append(current)

        for cluster in clusters:
            if frozenset(event["id"] for event in cluster) in covered:
                continue
            add(
                _build_group(
                    "time_proximity",
                    "Events from the same component occurred close in time.",
                    cluster,
                    [
                        f"same affected component: {component}",
                        "events occurred within the configured time window",
                    ],
                    alternatives=[
                        "The events may be independent but coincidentally close in time.",
                    ],
                    limitations=[
                        "Time proximity alone is weak evidence and must not be treated "
                        "as one incident.",
                        "Events from different components are never grouped on time "
                        "proximity alone.",
                    ],
                    metadata={
                        "time_window_minutes": time_window_minutes,
                        "component": component,
                    },
                )
            )

    # 5. Supplied findings: attach only where an event links to them.
    unattached_findings: list[str] = []
    for finding in findings_input:
        finding_id = _get_field(finding, "finding_id", "id", "code")
        if not finding_id:
            continue
        finding_id = str(finding_id)
        linked = False
        for group in groups:
            member_ids = set(group.event_ids)
            if finding_id in member_ids or any(
                event["id"] in member_ids
                for event in normalized
                if finding_id in event["finding_refs"]
            ):
                group.finding_ids = sorted({*group.finding_ids, finding_id})
                linked = True
        if not linked:
            unattached_findings.append(finding_id)

    for finding_id in sorted(set(unattached_findings)):
        groups.append(
            CorrelationGroup(
                group_id=_group_id("finding_only", [finding_id]),
                event_ids=[],
                finding_ids=[finding_id],
                shared_evidence=["supplied diagnostic finding without event linkage"],
                timeline=[],
                possible_dependency_relationships=[],
                alternative_explanations=[
                    "No event was supplied, so there is no evidence this finding is "
                    "related to any other finding.",
                ],
                limitations=[
                    _CAUSATION_LIMITATION,
                    "Listed alone only because it was supplied; no correlation evidence exists.",
                ],
                correlation_reason=("Diagnostic finding supplied without associated events."),
                metadata={"unattached": True},
            )
        )

    if redact:
        for group in groups:
            group.shared_evidence = [redact_text(evidence) for evidence in group.shared_evidence]
            for item in group.timeline:
                if isinstance(item, dict) and item.get("message") is not None:
                    item["message"] = redact_text(str(item["message"]))

    without_timestamps = [event for event in normalized if event["timestamp"] is None]
    if without_timestamps:
        warnings.append(
            f"{len(without_timestamps)} event(s) lack parseable timestamps; "
            "temporal correlation is limited."
        )

    status = "correlated" if groups else "no_correlation"
    summary = (
        f"Correlated {len(normalized)} event(s) into {len(groups)} group(s)."
        if groups
        else f"Analyzed {len(normalized)} event(s); no correlation evidence was found."
    )

    return CorrelationResult(
        tool_name=TOOL_NAME,
        status=status,
        summary=summary,
        correlation_groups=list(groups),
        groups=list(groups),
        findings=[],
        analyzed_items=len(normalized),
        warnings=warnings,
        metadata={
            "event_count": len(normalized),
            "group_count": len(groups),
            "time_window_minutes": time_window_minutes,
            "events_with_timestamps": len(timed),
            "events_without_timestamps": len(without_timestamps),
            "supplied_findings": len(findings_input),
        },
        observed_facts=[
            f"{len(normalized)} event(s) analyzed.",
            f"{len(groups)} correlation group(s) proposed.",
        ],
        detected_patterns=["correlation_groups"] if groups else [],
        potential_explanations=(
            ["Events in a group may share a cause or dependency; this is unconfirmed."]
            if groups
            else ["Supplied evidence is insufficient to propose any correlation."]
        ),
        recommended_next_steps=[
            "Review each group's shared evidence, alternatives, and limitations.",
            "Gather additional logs or metrics before treating a group as one incident.",
        ],
    )
