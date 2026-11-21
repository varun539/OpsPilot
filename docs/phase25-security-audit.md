# Phase 25 — Security Audit & Hardening

## Repository

Local OpsPilot repository. Branch: `phase25-security-hardening`. Phase 25 performed a security audit and minimal hardening across authentication, authorization, prompt injection, tool execution, API security, and secret/data protection. No commits or pushes were made; all changes remain uncommitted in the working tree.

## Classification

**PARTIAL** — High-value controls were added and verified (authentication hardening, RBAC dependency enforcement, per-principal rate limiting, request body ceiling, security headers, JWT verifier caching, untrusted-evidence fencing, sanitization bug fix, ReDoS mitigation, file permission hardening). Residual risks remain that are documented and not fully remediated: no tenant/ownership isolation, dev-auth relies on configured host rather than the actual client socket, health/AI and OpenAPI endpoints are unauthenticated (informational), MCP server errors surface tracebacks on server stderr (client sees a generic message), and production deployment still depends on operator configuration of OIDC, CORS, and secrets.

## Threat Model and Trust Boundaries

Trust boundaries (in priority order):

1. **Client ↔ FastAPI API** — untrusted HTTP input (request bodies, headers, query/path params).
2. **API ↔ Auth provider** — OIDC/JWKS verification of bearer tokens; dev-auth bypass only on loopback hosts with explicit opt-in.
3. **Orchestration service ↔ LLM provider** — untrusted incident text, logs, stack traces, configuration, and retrieved evidence are passed into prompts.
4. **Agent ↔ Diagnostic tools** — fixed, allowlisted tools only; no shell, file, SQL, or network tool exists.
5. **Agent ↔ MCP server** — stdio JSON-RPC boundary; tool arguments validated server-side.
6. **Service ↔ Persistence** — SQLite app DB and LangGraph checkpoint DB on local filesystem.
7. **Container runtime** — non-root user, dropped capabilities, read-only-ish filesystem, loopback-bound ports.

Assets protected: incident/investigation data, diagnostic evidence, API credentials, JWTs, LLM provider keys, checkpoint state.

## Authentication Findings

- JWT verification (`backend/app/auth/jwt.py`) enforces issuer, audience, expiry, not-before, algorithm allowlist (asymmetric by default; HS256 only via explicit local secret), and required claims. No change needed; verified by `tests/test_auth_jwt.py`.
- **Finding (fixed):** `JWTVerifier` was constructed per request, defeating JWKS caching and rotation. Fixed: verifier is now cached on `app.state.jwt_verifier` (`backend/app/auth/dependencies.py`).
- Dev-auth (`_is_dev_mode`) requires `auth_mode=development`, `dev_auth_enabled=True`, and `is_loopback_host()`. **Residual risk:** the loopback check uses the configured `OPSPILOT_HOST`, not the actual peer socket, so a misconfigured host value could enable dev-auth on a non-loopback bind. Documented; changing it would break `TestClient`-based tests and is deferred.
- Auth settings validation surfaces production misconfiguration at lifespan startup (not construction), verified by tests.

## RBAC Findings

- Role hierarchy enforced via `require_viewer` / `require_operator` / `require_admin` dependencies on every route. Verified by `tests/test_security_rbac.py` (23 tests).
- **Finding (documented, not remediated):** no tenant/ownership isolation. Any operator can read or decide any investigation. Recorded as a limitation; `test_any_operator_can_decide_any_investigation` asserts current behavior so a regression is detectable.
- Rate limiting on investigation start and approval is declared *after* the role check, so unauthorized callers receive 403, never 429.

## Prompt-Injection Results

- All untrusted evidence is now fenced in dedicated XML-ish blocks and sanitized:
  - Planner (`backend/app/agent/planner.py`): question, incident context, and evidence titles are fenced, delimiters neutralized, newlines collapsed.
  - Nodes (`backend/app/agent/nodes.py`): `_safe_text` redacts secrets, flattens newlines, escapes `<`/`>`, and bounds length; diagnostic findings fenced in `<diagnostic_findings>`.
  - Generator (`backend/app/rag/generator.py`): `_safe_prompt_value`, redacted error list, sanitized/escaped diagnostic findings.
  - Sanitization (`backend/app/investigation_tools/sanitization.py`): added `neutralize_prompt_delimiters`; sensitive fragments extended (`api-key`, `x-api-key`, `cookie`, `session`, `jwt`).
- **Fixed real leak:** `redact_text` previously consumed only the word `Bearer`, orphaning the token in log evidence. Bearer redaction now runs before the key=value pass.
- Verified by `tests/test_security_prompt_injection.py` (14 tests) and `tests/test_rag_generator.py`.

## Tool Security

- Tool registry (`backend/app/agent/tool_registry.py`) exposes only six fixed diagnostic tools; `execute_tool` raises `ValueError` for any other name. No shell/file/SQL/network tool exists.
- `execute_diagnostics` is fault-isolated: a single bad tool is recorded in `tool_errors` and does not abort the investigation; unexpected exceptions propagate and fail the start (REL-016).
- Input construction is hardened: `http_status` type confusion returns `None` (skipped) instead of raising; `events` must be a list.
- MCP server (`backend/app/mcp_server`) validates arguments against JSON Schema; tool errors are contained client-side as a generic `Error executing tool X` message. Server-side tracebacks go to stderr only (documented as informational/low).
- **ReDoS mitigation:** `log_analyzer` exception regex and `_URL_CREDS_RE` were quadratic on long single-token lines; both now run linearly (200k chars in <1s). Evidence and extracted identifiers are bounded (`_MAX_EVIDENCE_CHARS=500`).
- Verified by `tests/test_security_tools.py` (30 tests).

## API Security

- New `backend/app/api/middleware.py`: `BodySizeLimitMiddleware` (2 MiB default, `OPSPILOT_MAX_REQUEST_BYTES`), `sanitize_request_id` (regex allowlist, invalid → fresh UUID), `apply_security_headers` (nosniff, DENY, no-referrer, plus no-store for `/api/`).
- New `backend/app/api/rate_limit.py`: per-principal fixed-window limiter; 60 requests / 60s default; env-overridable; applied to investigation start and approval only.
- Body limit middleware is added before CORS; request-id sanitization and security headers on every response.
- Error envelope (`backend/app/api/exceptions.py`) returns `error.code/message/request_id`; 429 carries `Retry-After`; unexpected exceptions do not leak tracebacks.
- CORS defaults to no origins (must be explicitly configured).
- Verified by `tests/test_security_api.py` (11 tests).

## Secret and Data Protection

- Redaction (`redact_text`) masks bearer tokens, key=value secrets, URL credentials; long lines bounded.
- SQLite app DB and LangGraph checkpoint DB are chmod 0600 on POSIX (best-effort; Windows skipped).
- `.gitignore` covers `.env`, `*.sqlite`, `*.db`, `*.pem`, `*.key`.
- Source scan found no obvious live secret literals.
- `Health/ai` endpoint is unauthenticated and returns provider/model/collection metadata (informational/low).
- `/docs`, `/redoc`, `/openapi.json` are unauthenticated (informational/low).
- Verified by `tests/test_security_data_protection.py` (10 tests, 2 skipped on Windows).

## Adversarial Results Summary

New adversarial suite (all assert zero violations):

| File | Tests | Status |
| --- | --- | --- |
| `tests/test_security_auth.py` | 19 | pass |
| `tests/test_security_rbac.py` | 23 | pass |
| `tests/test_security_prompt_injection.py` | 14 | pass |
| `tests/test_security_tools.py` | 30 | pass |
| `tests/test_security_api.py` | 11 | pass |
| `tests/test_security_data_protection.py` | 10 (2 skipped on Windows) | pass |

## Regression Results

- `python -m pytest -q`: 730 tests collected, all passed (skips on Windows for POSIX permission bits).
- `python -m ruff check .`: passed.
- Frontend: `npm run test` (31 passed), `npm run typecheck`, `npm run lint`, `npm run api:check`, `npm run build`: all passed.
- `OPSPILOT_PYTHON` set to the system Python on Windows for `api:check`.

## Exact Files Changed

New files:
- `backend/app/api/middleware.py`
- `backend/app/api/rate_limit.py`
- `tests/security_harness.py`
- `tests/test_security_auth.py`
- `tests/test_security_rbac.py`
- `tests/test_security_prompt_injection.py`
- `tests/test_security_tools.py`
- `tests/test_security_api.py`
- `tests/test_security_data_protection.py`
- `docs/phase25-security-audit.md`
- `docs/phase25-adversarial-evaluation.md`

Modified files:
- `backend/app/main.py`
- `backend/app/api/exceptions.py`
- `backend/app/api/routes/orchestration.py`
- `backend/app/auth/dependencies.py`
- `backend/app/incidents/database.py`
- `backend/app/agent/checkpointer.py`
- `backend/app/investigation_tools/sanitization.py`
- `backend/app/agent/planner.py`
- `backend/app/agent/nodes.py`
- `backend/app/agent/tool_registry.py`
- `backend/app/investigation_tools/log_analyzer.py`
- `backend/app/rag/generator.py`
- `.env.example`
- `.gitignore`
- `frontend/openapi.json`
- `frontend/src/lib/api/generated.ts`
- `frontend/scripts/check-api-types.mjs`

## Unresolved Risks

1. No tenant/ownership isolation; any operator can act on any investigation.
2. Dev-auth loopback check uses configured host, not the actual peer socket.
3. `Health/ai`, `/docs`, `/redoc`, `/openapi.json` are unauthenticated (informational).
4. MCP server writes tracebacks to stderr (client receives generic message).
5. `InvestigationStartError`/`AIServiceUnavailableError` surface `str(exc)` in some error paths.
6. SQLite/checkpoint permissions are best-effort on Windows (no chmod).
7. No global rate limit; only investigation start and approval are bounded.
8. No CAPTCHA/abuse protection for unauthenticated endpoints.

## Phase 26 Recommendation

Prioritize (in order): (1) tenant/ownership model and per-tenant authorization; (2) socket-level loopback verification for dev-auth (or remove dev-auth entirely); (3) authenticate or restrict `Health/ai` and OpenAPI in production; (4) global rate limiting and abuse protection; (5) human report-quality rubric collection; (6) production deployment hardening review (TLS, secret rotation, backup encryption).
