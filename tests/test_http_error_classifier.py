from __future__ import annotations

from backend.app.investigation_tools import classify_http_error


def test_valid_status():
    r = classify_http_error(200)
    assert r.status == "classified"
    assert r.metadata["reason_phrase"] == "OK"


def test_common_codes():
    for code in (400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504):
        r = classify_http_error(code)
        assert r.status == "classified"


def test_unsupported_code():
    r = classify_http_error(999)
    assert r.status == "invalid_input"


def test_retry_after():
    r = classify_http_error(429, headers={"Retry-After": "12"})
    assert r.metadata["retry_after"]["seconds"] == 12
    assert any(f.code == "HTTP_RETRY_AFTER" for f in r.findings)


def test_unknown_response_text():
    r = classify_http_error(500, response_text="Internal Server Error")
    assert r.metadata["response_text_snippet"] is not None


def test_classification_accuracy():
    r = classify_http_error(404)
    assert r.metadata["category"] == "client_error"
    r2 = classify_http_error(500)
    assert r2.metadata["category"] == "server_error"
