from __future__ import annotations

from backend.app.investigation_tools import validate_configuration


def test_missing_required():
    r = validate_configuration({}, rules={"DB": {"required": True}})
    assert any(f.code == "CONFIG_MISSING_REQUIRED" for f in r.findings)


def test_empty_values():
    r = validate_configuration({"DB": ""}, rules={"DB": {"required": True}})
    assert any(f.code == "CONFIG_EMPTY_VALUE" for f in r.findings)


def test_correct_types():
    r = validate_configuration({"PORT": 8080}, rules={"PORT": {"type": "int"}})
    assert r.status == "valid"


def test_invalid_types():
    r = validate_configuration({"PORT": "abc"}, rules={"PORT": {"type": "int"}})
    assert any(f.code == "CONFIG_INVALID_TYPE" for f in r.findings)


def test_invalid_urls():
    r = validate_configuration({"URL": "not-a-url"}, rules={"URL": {"type": "url"}})
    assert any(f.code == "CONFIG_INVALID_URL" for f in r.findings)


def test_invalid_ports():
    r = validate_configuration({"PORT": 70000}, rules={"PORT": {"type": "port"}})
    assert any(f.code == "CONFIG_INVALID_PORT" for f in r.findings)


def test_custom_rules():
    r = validate_configuration({"X": 5}, rules={"X": {"min": 1, "max": 10}})
    assert r.status == "valid"


def test_secret_redaction():
    r = validate_configuration({"PASSWORD": "secret"}, rules={"PASSWORD": {"required": True}})
    assert "PASSWORD" in r.metadata["sensitive_fields"]
    for f in r.findings:
        assert "secret" not in " ".join(f.evidence)


def test_no_env_access(monkeypatch):
    # The tool must validate only supplied data, never the process environment.
    monkeypatch.setenv("SECRET_FROM_ENV", "should-not-be-read")
    r = validate_configuration({"A": "1"}, rules={"A": {"required": True}})
    assert r.status == "valid"
    assert "ACCIDENTAL" not in r.model_dump_json()
    assert "SECRET_FROM_ENV" not in r.model_dump_json()
