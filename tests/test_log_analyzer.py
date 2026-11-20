from __future__ import annotations

from backend.app.investigation_tools import analyze_logs


def test_standard_format():
    text = "2026-11-14 10:15:32 ERROR application.database - Connection refused"
    r = analyze_logs(text)
    assert r.status == "issues_found"
    assert r.analyzed_items == 1
    assert any(f.code == "LOG_ERROR_ENTRIES" for f in r.findings)


def test_iso_timestamps():
    text = "2026-11-14T10:15:32Z ERROR service.api Request failed"
    r = analyze_logs(text)
    assert r.metadata["earliest_error_timestamp"] is not None


def test_json_entries():
    text = '{"timestamp":"2026-11-14T10:15:32Z","level":"ERROR","message":"boom","request_id":"r1"}'
    r = analyze_logs(text)
    assert r.metadata["correlation_ids"] == ["r1"]
    assert r.metadata["level_counts"]["ERROR"] == 1


def test_multiple_levels():
    text = "INFO ok\nWARNING w\nERROR e\nCRITICAL c"
    r = analyze_logs(text)
    assert r.metadata["level_counts"]["INFO"] == 1
    assert r.metadata["level_counts"]["WARNING"] == 1
    assert r.metadata["level_counts"]["ERROR"] == 1
    assert r.metadata["level_counts"]["CRITICAL"] == 1


def test_repeated_grouping():
    text = "2026-11-14 10:00:00 ERROR app - boom\n2026-11-14 10:00:01 ERROR app - boom\n"
    r = analyze_logs(text)
    assert r.metadata["repeated_message_count"] == 1


def test_malformed_input():
    text = "not a log line at all\n@@@\n"
    r = analyze_logs(text)
    assert r.analyzed_items >= 1


def test_empty_input():
    r = analyze_logs("")
    assert r.status == "empty"
    assert r.analyzed_items == 0


def test_input_size_limits():
    text = "INFO x\n" * 1000
    r = analyze_logs(text, max_lines=5)
    assert any("truncated" in w.lower() for w in r.warnings)
    assert r.analyzed_items == 5


def test_secret_redaction():
    text = "ERROR app - password=secret123 token=abcdefghijklmnopqrstuvwxyz"
    r = analyze_logs(text)
    joined = " ".join(f.evidence[0] for f in r.findings if f.evidence)
    assert "secret123" not in joined
    assert "<redacted>" in joined
