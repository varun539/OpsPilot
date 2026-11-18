# OpsPilot Production Readiness

This document describes the verified local development setup, production
configuration expectations, authentication model, API contract workflow, test
layers, and known limitations for OpsPilot. Commands below were executed against
the repository during Phase 13 validation.

## Architecture and system boundaries

OpsPilot is a single-user-facing operations dashboard backed by a synchronous
FastAPI service:

- **Frontend** (`frontend/`): Next.js 16 App Router, React 19, TypeScript,
  Tailwind CSS v4. Browser code calls the REST API over HTTP.
- **API** (`backend/app/api/`): FastAPI routes under `/api/v1`, JWT bearer
  authentication, role checks (`viewer` < `operator` < `admin`), CORS, request
  IDs, and normalized error envelopes.
- **Workflow** (`backend/app/agent/`, `backend/app/orchestration/`): LangGraph
  graph with validation, classification, retrieval, planning, deterministic
  diagnostic tools, correlation, report synthesis, validation, a real
  `interrupt()` for human review, and `finalize`.
- **Persistence** (`backend/app/incidents/`, `backend/app/agent/checkpointer.py`):
  two separate SQLite databases — application records
  (`OPSPILOT_APP_DB`) and LangGraph workflow checkpoints
  (`OPSPILOT_CHECKPOINT_DB`).
- **External providers**: OpenAI-compatible structured generation and hybrid
  retrieval/Chroma indexes. These are the only external dependencies in the
  investigation path.

Incident status and investigation workflow status are deliberately independent.
Approving or rejecting an investigation never resolves the incident
automatically. Investigation execution is synchronous in the request that
starts it; there is no background worker infrastructure.

## Verified environment variables

Backend (read from the process environment; the app does not load `.env` files):

| Variable | Purpose | Default |
| --- | --- | --- |
| `OPSPILOT_HOST` | Bind host used for the loopback dev-auth check | `127.0.0.1` |
| `OPSPILOT_AUTH_MODE` | `development` or `production` | `production` |
| `OPSPILOT_DEV_AUTH_ENABLED` | Explicit loopback development bypass | `false` |
| `OPSPILOT_AUTH_ISSUER` | Trusted OIDC issuer | unset |
| `OPSPILOT_AUTH_AUDIENCE` | Required token audience | unset |
| `OPSPILOT_AUTH_JWKS_URL` | Trusted JWKS endpoint | unset |
| `OPSPILOT_AUTH_ROLES_CLAIM` | Claim containing roles (dot paths allowed) | `roles` |
| `OPSPILOT_AUTH_ALGORITHMS` | Allowed signing algorithms | asymmetric family |
| `OPSPILOT_CORS_ORIGINS` | Comma-separated allowed origins | none |
| `OPSPILOT_APP_DB` | Application SQLite path | `data/opspilot_app.sqlite` |
| `OPSPILOT_CHECKPOINT_DB` | LangGraph checkpoint SQLite path | library default |

Frontend (public build-time values; never secrets):

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_OPSPILOT_API_URL` | API base URL (default `http://127.0.0.1:8000`) |
| `NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY` | OIDC authority |
| `NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID` | Public OIDC client ID |
| `NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI` | Callback URL (defaults to `/auth/callback`) |
| `NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE` | Requested scopes |
| `NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM` | Claim decoded for display only |
| `NEXT_PUBLIC_OPSPILOT_DEV_AUTH` | Explicit local development bypass (`true`) |

The roles claim variable is spelled `NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM` in the
implementation. Placeholder examples live in `.env.example` and
`frontend/.env.example`.

## Authentication and authorization

### Development

Development authentication is opt-in and requires all three conditions:

1. `OPSPILOT_AUTH_MODE=development`
2. `OPSPILOT_DEV_AUTH_ENABLED=true`
3. A loopback `OPSPILOT_HOST` (`127.0.0.1`, `localhost`, `::1`, or `127.*`)

If the host is non-loopback, application construction raises immediately. The
frontend also requires the separate build-time flag
`NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true`; it never selects development mode
automatically. The UI shows a "Development auth" badge while active and grants
the `admin` role.

### Production

Production requires an issuer, audience, and JWKS URL. At startup the
application fails closed (`RuntimeError`) when:

- The auth mode is neither `development` nor `production`.
- Required issuer/audience/JWKS values are missing.
- The JWKS URL is not `https` outside loopback.
- `OPSPILOT_DEV_AUTH_ENABLED` is set while the mode is `production`.

Tokens are verified with a trusted JWKS key set, issuer, audience, expiry, and
signing algorithm checks. Roles may be a list or a space/comma-separated string
in the configured claim (dot paths are supported); an unrecognized role set
falls back to `viewer`. Decoded frontend JWT claims are used for display only —
the backend always verifies signatures. `tests/test_auth_jwt.py` covers valid
claims, nested role claims, expiry, audience/issuer mismatch, missing required
claims, wrong signing keys, unsigned tokens, and fail-closed behavior when
verification is not configured.

### Frontend session behavior

- Authorization Code Flow with PKCE via `oidc-client-ts`; no client secret.
- Access and state tokens are stored in `sessionStorage`, not `localStorage`.
- The access token is sent only in the `Authorization: Bearer` header.
- Silent renewal is intentionally disabled. When the OIDC library reports
  `addAccessTokenExpired`, the local user is cleared and the sign-in screen is
  shown.
- Any API `401` invokes a global session handler that clears the local user and
  returns the user to sign-in, so protected screens do not keep stale data.
- Logout clears the local user and performs an OIDC sign-out redirect when a
  provider is configured; on failure it falls back to removing the local user.
- With explicit development auth, reloading restores the development session by
  design; there is no real provider session to expire.

## Local development

Backend:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
OPSPILOT_AUTH_MODE=development \
OPSPILOT_DEV_AUTH_ENABLED=true \
OPSPILOT_HOST=127.0.0.1 \
OPSPILOT_CORS_ORIGINS=http://localhost:3000 \
OPSPILOT_APP_DB=data/opspilot_app.sqlite \
OPSPILOT_CHECKPOINT_DB=data/opspilot_checkpoints.sqlite \
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Storage is initialized lazily with additive `CREATE TABLE IF NOT EXISTS` /
`ALTER TABLE` migrations. No separate migration command is required. Keep the
application and checkpoint paths separate.

Frontend:

```bash
cd frontend
npm install
NEXT_PUBLIC_OPSPILOT_API_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true \
npm run dev
```

Open `http://localhost:3000`. Do not expose the development server publicly.

## OpenAPI type generation

The FastAPI schema is the source of truth.

```bash
cd frontend
npm run api:types   # export ../frontend/openapi.json, generate src/lib/api/generated.ts
npm run api:check   # fail if generated types drift from the backend schema
```

`src/lib/api/types.ts` maps generated component schemas to frontend aliases and
keeps only genuinely UI-specific types (role and query parameter helpers).
`generated.ts` and `openapi.json` are normal project source; do not edit
`generated.ts` by hand. Run `npm run api:types` after any backend route or
Pydantic schema change.

## Test suite

Backend:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/pip check
```

Frontend:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run api:check
npm run build
```

Browser end-to-end (requires a Playwright Chromium browser):

```bash
cd frontend
npx playwright install chromium   # once per environment
npm run test:e2e
npm run test:e2e:ui               # interactive mode
```

The E2E suite runs serially against a real Next.js dev server and a real
FastAPI process started by `e2e/support/global-setup.ts`.

- Backend: `tests/e2e_backend.py` uses temporary SQLite paths under `/tmp`,
  explicit loopback development authentication, a deterministic fake structured
  LLM provider, and a deterministic fake retrieval engine. No live model or
  network call occurs.
- Frontend: `NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true` is set only for the test dev
  server.
- Restart recovery: `e2e/restart-recovery.spec.ts` stops and restarts the
  backend process against the same application and checkpoint databases, then
  loads the pending investigation in the browser and resumes the original
  LangGraph thread. A second restart verifies the final result is durable.
- The full journey in `e2e/full-investigation-flow.spec.ts` exercises real HTTP
  from the browser through the API, the real LangGraph graph, the real
  human-review interrupt, approval persistence, resume, duplicate-decision
  rejection (`409`), and a browser refresh.
- The `oidc-unconfigured` project runs `e2e/config-required.spec.ts` against a
  second instance served from a separate production build (`NEXT_DIST_DIR` is
  set only for that build, on port 3001) with no OIDC values and no
  development-auth flag. It verifies the fail-closed configuration screen and
  that the development bypass is not compiled into a normal production build.

These browser tests do not mock the application API. Only external providers are
replaced. Role restrictions for `viewer`/`operator` are covered by backend API
tests (`tests/test_investigation_api.py`, `tests/test_api_auth.py`) and JWT/role
unit tests (`tests/test_auth_jwt.py`) rather than the browser suite, because
browser E2E runs with the explicit development `admin` principal.

## Security considerations

- No `dangerouslySetInnerHTML`, unsafe HTML injection, or untrusted Markdown
  execution in the dashboard.
- Investigation reports, evidence JSON, and event metadata render as text or
  JSON viewers. They are never executed.
- Citations and source references are displayed as text/JSON metadata; they do
  not become clickable internal URLs.
- No client secret is present in frontend code or build output.
- ID tokens are never used as API bearer tokens; API calls use the access token.
- Tokens never appear in URLs or application logs, and the dashboard has no
  token logging.
- CORS never combines a wildcard origin with credentials. If `*` is configured,
  credentials are disabled.
- Development authentication cannot be enabled on a public bind address and is
  rejected at startup in production mode.
- Configuration error messages name missing variables but never print secret
  values.
- Test providers (`tests/e2e_backend.py`) are never imported by the production
  application.

## Release verification checklist

1. `git status` shows only intended changes; no secrets or local databases.
2. Backend: pytest, Ruff, and `pip check` pass.
3. Frontend: lint, TypeScript checks, unit/component tests, and production build
   pass.
4. `npm run api:check` reports generated types match the FastAPI schema.
5. `npm run test:e2e` passes, including restart recovery.
6. A production environment sets `OPSPILOT_AUTH_MODE=production` with issuer,
   audience, and an https JWKS URL; startup succeeds with valid configuration.
7. `OPSPILOT_DEV_AUTH_ENABLED` is unset/false and
   `NEXT_PUBLIC_OPSPILOT_DEV_AUTH` is unset in the production build.
8. `OPSPILOT_CORS_ORIGINS` lists exact dashboard origins.
9. Application and checkpoint databases are separate and backed up.
10. The deployment terminates TLS before the API and dashboard.

## Known limitations

- OIDC was reviewed in code but not exercised against a live identity provider;
  no production issuer credentials are included. Browser tests use explicit
  development authentication.
- Silent token renewal is disabled. Expiry is surfaced as a reauthentication
  experience; provider-specific refresh-token behavior is not implemented.
- Investigation execution is synchronous. Long-running graphs block the request
  that starts them; there are no background workers by design.
- Incident text search filters only the currently loaded page; severity/status
  filtering and pagination are server-side.
- SQLite is intended for local/single-process use; no multi-tenant or
  distributed deployment architecture is provided.
- No production hosting deployment is performed or documented here.
