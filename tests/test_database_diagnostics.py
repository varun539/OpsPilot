from __future__ import annotations

from backend.app.investigation_tools import diagnose_database_error


def test_connection_refused():
    r = diagnose_database_error("could not connect to server: Connection refused")
    assert any(f.code == "DB_CONNECTION_REFUSED" for f in r.findings)


def test_timeout():
    r = diagnose_database_error("connection timed out")
    assert any(f.code == "DB_CONNECTION_TIMEOUT" for f in r.findings)


def test_auth_failure():
    r = diagnose_database_error("FATAL: password authentication failed for user postgres")
    assert any(f.code == "DB_AUTHENTICATION_FAILURE" for f in r.findings)


def test_dns_failure():
    r = diagnose_database_error("could not translate host name db to address")
    assert any(f.code == "DB_DNS_RESOLUTION_FAILURE" for f in r.findings)


def test_pool_exhaustion():
    r = diagnose_database_error("QueuePool limit of size 5 overflow 10 reached")
    assert any(f.code == "DB_POOL_EXHAUSTION" for f in r.findings)


def test_too_many_connections():
    r = diagnose_database_error("FATAL: sorry, too many clients already")
    assert any(f.code == "DB_TOO_MANY_CONNECTIONS" for f in r.findings)


def test_ssl_failures():
    r = diagnose_database_error("server does not support SSL")
    assert any(f.code == "DB_SSL_ERROR" for f in r.findings)


def test_deadlocks():
    r = diagnose_database_error("ERROR: deadlock detected")
    assert any(f.code == "DB_DEADLOCK" for f in r.findings)


def test_unknown_error():
    r = diagnose_database_error("something weird happened")
    assert r.status == "unknown"
    assert any(f.code == "DB_UNKNOWN_ERROR" for f in r.findings)
