"""Adversarial tests for secret/data protection (Phase 25)."""

from __future__ import annotations

import os
import stat

import pytest

from backend.app.investigation_tools.sanitization import redact_text


def test_redact_text_masks_bearer_token():
    text = "Authorization: Bearer sk-live-abcdefghijklmnop"
    redacted = redact_text(text)
    assert "sk-live" not in redacted
    assert "<redacted>" in redacted


def test_redact_text_masks_key_value_secrets():
    cases = [
        "api_key=supersecretvalue123",
        "password: hunter2hunter2",
        "token=eyJhbGciOiJIUzI1NiJ9.payload.signature",
    ]
    for case in cases:
        out = redact_text(case)
        assert "supersecret" not in out and "hunter2" not in out
        assert "<redacted>" in out


def test_redact_text_masks_url_credentials():
    text = "https://user:pass@internal.example.com/path"
    out = redact_text(text)
    assert "pass" not in out
    assert "<redacted>" in out


def test_redact_text_handles_long_single_line_without_blowup():
    import time

    blob = "ERROR " + "x" * 100_000
    start = time.monotonic()
    out = redact_text(blob)
    assert time.monotonic() - start < 10.0
    assert isinstance(out, str)


def test_sqlite_db_file_permissions_restricted_on_posix(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX permission bits not enforced on this platform")
    from backend.app.incidents.database import ApplicationDatabase

    db = ApplicationDatabase(db_path=str(tmp_path / "app.sqlite"))
    db.initialize()
    mode = stat.S_IMODE(os.stat(str(tmp_path / "app.sqlite")).st_mode)
    assert mode & 0o077 == 0, f"database file is group/world accessible: {oct(mode)}"


def test_checkpoint_db_permissions_restricted_on_posix(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX permission bits not enforced on this platform")
    from backend.app.agent.checkpointer import SQLiteCheckpointer

    db_path = tmp_path / "checkpoints.sqlite"
    with SQLiteCheckpointer(str(db_path)):
        pass
    mode = stat.S_IMODE(os.stat(str(db_path)).st_mode)
    assert mode & 0o077 == 0, f"checkpoint db group/world accessible: {oct(mode)}"


def test_gitignore_covers_secrets_and_dbs():
    with open(".gitignore", encoding="utf-8") as fh:
        content = fh.read()
    for pattern in (".env", "*.sqlite", "*.db", "*.pem", "*.key"):
        assert pattern in content, f".gitignore missing {pattern}"


def test_no_api_keys_in_recent_source():
    # Lightweight scan: ensure no obvious live secret literals in tracked backend.
    import re

    secret_patterns = [
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"AIza[0-9A-Za-z_-]{35}"),
        re.compile(r"ghp_[A-Za-z0-9]{36}"),
    ]
    import pathlib

    for path in pathlib.Path("backend").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in secret_patterns:
            assert not pat.search(text), f"possible secret in {path}"


def test_jwt_not_logged_by_verifier():
    # Smoke: importing/constructing verifier must not print tokens.
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        from backend.app.auth.config import AuthSettings
        from backend.app.auth.jwt import JWTVerifier

        s = AuthSettings(auth_mode="development", dev_auth_enabled=True, host="127.0.0.1")
        try:
            JWTVerifier(s)
        except Exception:
            pass
    assert "eyJ" not in buf.getvalue()
