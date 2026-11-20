from __future__ import annotations

from backend.app.investigation_tools import correlate_incidents


def test_shared_correlation_ids():
    events = [
        {
            "id": "e1",
            "timestamp": "2026-11-14 10:00:00",
            "component": "api",
            "message": "boom",
            "correlation_id": "r1",
        },
        {
            "id": "e2",
            "timestamp": "2026-11-14 10:00:01",
            "component": "db",
            "message": "boom",
            "correlation_id": "r1",
        },
    ]
    r = correlate_incidents(events)
    assert len(r.correlation_groups) == 1
    assert "r1" in r.correlation_groups[0].shared_evidence[0]


def test_matching_signatures():
    events = [
        {
            "id": "e1",
            "timestamp": "2026-11-14 10:00:00",
            "component": "api",
            "message": "boom",
        },
        {
            "id": "e2",
            "timestamp": "2026-11-14 10:00:01",
            "component": "db",
            "message": "boom",
        },
    ]
    r = correlate_incidents(events)
    assert len(r.correlation_groups) >= 1
    assert any("signature" in g.correlation_reason for g in r.correlation_groups)


def test_timestamp_proximity():
    events = [
        {
            "id": "e1",
            "timestamp": "2026-11-14 10:00:00",
            "component": "api",
            "message": "a",
            "dependencies": ["db"],
        },
        {
            "id": "e2",
            "timestamp": "2026-11-14 10:00:01",
            "component": "db",
            "message": "b",
            "dependencies": ["db"],
        },
    ]
    r = correlate_incidents(events)
    assert len(r.correlation_groups) >= 1


def test_different_components():
    events = [
        {
            "id": "e1",
            "timestamp": "2026-11-14 10:00:00",
            "component": "api",
            "message": "a",
            "dependencies": ["db"],
        },
        {
            "id": "e2",
            "timestamp": "2026-11-14 10:00:01",
            "component": "db",
            "message": "b",
            "dependencies": ["db"],
        },
    ]
    r = correlate_incidents(events)
    assert r.correlation_groups


def test_missing_timestamps():
    events = [
        {"id": "a", "component": "api", "message": "x"},
        {"id": "b", "component": "db", "message": "y"},
    ]
    r = correlate_incidents(events)
    assert any("timestamp" in w.lower() for w in r.warnings)


def test_unrelated_events():
    events = [
        {
            "id": "e1",
            "timestamp": "2026-11-14 10:00:00",
            "component": "api",
            "message": "ok",
        },
        {
            "id": "e2",
            "timestamp": "2026-11-14 12:00:00",
            "component": "db",
            "message": "different",
        },
    ]
    r = correlate_incidents(events)
    assert len(r.correlation_groups) == 0


def test_no_causal_claims():
    events = [
        {
            "id": "e1",
            "timestamp": "2026-11-14 10:00:00",
            "component": "api",
            "message": "boom",
            "correlation_id": "r1",
        },
        {
            "id": "e2",
            "timestamp": "2026-11-14 10:00:01",
            "component": "db",
            "message": "boom",
            "correlation_id": "r1",
        },
    ]
    r = correlate_incidents(events)
    for g in r.correlation_groups:
        assert g.limitations
        joined = " ".join(g.limitations).lower()
        assert "causation" in joined or "not" in joined


def test_stable_refs():
    events = [
        {
            "id": "e1",
            "timestamp": "2026-11-14 10:00:00",
            "component": "api",
            "message": "boom",
            "correlation_id": "r1",
        },
        {
            "id": "e2",
            "timestamp": "2026-11-14 10:00:01",
            "component": "db",
            "message": "boom",
            "correlation_id": "r1",
        },
    ]
    r1 = correlate_incidents(events)
    r2 = correlate_incidents(events)
    left = [g.group_id for g in r1.correlation_groups]
    right = [g.group_id for g in r2.correlation_groups]
    assert left == right

