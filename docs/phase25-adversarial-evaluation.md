# Phase 25 — Adversarial Evaluation

## Overview

Phase 25 introduced a reusable adversarial test suite built on `tests/security_harness.py`. The harness provides `SecurityMetrics`, which distinguishes:

- `rejected` — request blocked at the boundary (auth, validation, tool-name allowlist).
- `contained` — failure occurred but was contained (tool error recorded, investigation not completed).
- `violation` — a security control was bypassed; every adversarial test asserts zero violations.

Each adversarial test file instantiates `SecurityMetrics()` and calls `metrics.assert_clean()` at the end.

## Test Inventory

| File | Tests | Focus |
| --- | --- | --- |
| `tests/test_security_auth.py` | 19 | malformed tokens, algorithm confusion, expired/not-yet-valid, issuer/audience mismatch, missing claims, dev-auth host gating, JWKS caching |
| `tests/test_security_rbac.py` | 23 | role hierarchy, viewer/operator/admin boundaries, cross-principal access documentation, rate-limit-after-role ordering |
| `tests/test_security_prompt_injection.py` | 14 | delimiter neutralization, evidence fencing, redaction, newline flattening, length bounding |
| `tests/test_security_tools.py` | 30 | tool allowlist, unknown tool rejection, type confusion containment, oversized input truncation, ReDoS, MCP argument validation, hostile payload handling |
| `tests/test_security_api.py` | 11 | body size limit, request-id sanitization, security headers, rate limiting + Retry-After, error sanitization, CORS, health disclosure |
| `tests/test_security_data_protection.py` | 10 (2 skipped on Windows) | redaction correctness, ReDoS bound, DB/checkpoint permissions, gitignore coverage, secret scan, JWT not logged |

## Key Adversarial Scenarios and Results

1. **Token tampering** — alg=none, HS256 with arbitrary secret, expired, future nbf, wrong iss/aud: all rejected. Result: 0 violations.
2. **Role escalation** — viewer attempting operator endpoints returns 403; operator cannot reach admin-only paths. Result: 0 violations.
3. **Prompt injection via evidence** — closing tags, instruction overrides, and delimiter smuggling in logs/stack traces/questions are neutralized and fenced. Result: 0 violations.
4. **Tool name injection** — `shell`, `run_shell`, `execute_sql`, `read_file`, `../log_analyzer`, empty string: all raise `ValueError`. Result: 0 violations.
5. **Type confusion** — `http_status="not-a-number"`, `events` as dict: each tool skipped and recorded, graph survives. Result: 0 violations.
6. **Oversized input** — 300k-char log line truncated to 200k; evidence capped at 500 chars; processing stays linear. Result: 0 violations.
7. **ReDoS** — 100k-char single-token line processed in <10s (was quadratic). Result: 0 violations.
8. **Body size** — 2 MiB + 256 bytes rejected with 413 `payload_too_large`. Result: 0 violations.
9. **Rate limiting** — second request within window returns 429 with `Retry-After`. Result: 0 violations.
10. **Error sanitization** — 404/422 responses contain only `code/message/request_id`; no traceback or offending value echo. Result: 0 violations.
11. **Secret redaction** — bearer tokens, key=value secrets, URL credentials masked; no orphaned token. Result: 0 violations.

## Regression with Adversarial Suite

- Backend: 730 tests collected, all passed.
- Ruff: passed.
- Frontend: test/typecheck/lint/api:check/build: all passed.

## Residual Gaps

- No tenant/ownership isolation tests (behavior documented, not enforced).
- Dev-auth socket verification not tested (configured-host based).
- MCP server stderr tracebacks not captured by client tests.
- No fuzzing of LLM outputs beyond delimiter neutralization.

## Phase 26 Recommendation

Add ownership/tenant isolation with tests, socket-level dev-auth verification, authenticated health/docs in production, global rate limiting, and expanded fuzzing of prompt inputs and MCP arguments.
