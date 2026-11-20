# OpsPilot Frontend Dashboard

## Architecture

Phase 12 adds a Next.js App Router frontend under `frontend/`. The frontend is a
read-only client of the existing FastAPI REST API. It does not duplicate incident,
investigation, approval, ticket, or workflow logic. All mutations are performed by
calling the versioned API endpoints.

The application uses a persistent dark-first shell with a collapsible sidebar and
client-side routing. Data is fetched with a centralized typed API client and
rendered as loading, empty, error, and success states.

## Technology choices

- Next.js 16.4.6 with the App Router
- React 19.3.1
- TypeScript 5.9.3
- Tailwind CSS 4.3.5
- Lucide icons
- `oidc-client-ts` for OIDC Authorization Code Flow with PKCE
- Vitest 5.1.0, React Testing Library 16.3.3, and jsdom 31.0.1 for tests

No chart library was added; breakdowns are rendered with accessible CSS bars to
avoid an unnecessary dependency.

## Folder structure

```text
frontend/
  package.json
  next.config.ts
  tsconfig.json
  postcss.config.mjs
  eslint.config.mjs
  vitest.config.mts
  vitest.setup.ts
  src/
    app/
      page.tsx
      auth/callback/page.tsx
      (dashboard)/
        layout.tsx
        dashboard/page.tsx
        incidents/page.tsx
        incidents/[incidentId]/page.tsx
        investigations/page.tsx
        investigations/[investigationId]/page.tsx
        approvals/page.tsx
        tickets/page.tsx
        settings/page.tsx
    components/
      layout/
      dashboard/
      incidents/
      investigations/
      approvals/
      tickets/
      ui/
    lib/
      api/
      auth/
      hooks/
      format.ts
```

## Environment variables

Frontend variables are public build-time values:

```text
NEXT_PUBLIC_OPSPILOT_API_URL=http://127.0.0.1:8000
NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY=https://issuer.example/
NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID=opspilot-web
NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI=http://localhost:3000/auth/callback
NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE="openid profile email"
NEXT_PUBLIC_OPSPILOT_OIDC_ROLES_CLAIM=roles
NEXT_PUBLIC_OPSPILOT_DEV_AUTH=false
```

Backend variables relevant to the frontend:

```text
OPSPILOT_AUTH_MODE=production
OPSPILOT_AUTH_ISSUER=https://issuer.example/
OPSPILOT_AUTH_AUDIENCE=opspilot-api
OPSPILOT_AUTH_JWKS_URL=https://issuer.example/.well-known/jwks.json
OPSPILOT_AUTH_ROLES_CLAIM=roles
OPSPILOT_HOST=127.0.0.1
OPSPILOT_CORS_ORIGINS=http://localhost:3000
```

## Authentication configuration

The frontend uses `oidc-client-ts` with Authorization Code Flow and PKCE. Access
tokens are stored in session-scoped storage and attached as bearer tokens. ID
tokens are never sent to the API. No client secret is embedded in the frontend.

Roles are read from the configured access-token claim (default `roles`). The
backend remains the security boundary; frontend role checks are usability only.

If no OIDC authority and client ID are configured, the frontend shows a
configuration-required screen. For loopback development, set
`NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true` explicitly. This is never enabled by default
and should not be used in production.

## API configuration

The frontend calls the FastAPI API through one base URL configured by
`NEXT_PUBLIC_OPSPILOT_API_URL`. API modules live in `frontend/src/lib/api/` and
handle request construction, bearer-token injection, error normalization, and
query serialization.

Backend changes added for Phase 12 are read-only:

- `GET /api/v1/dashboard/stats`
- `GET /api/v1/approvals`
- `GET /api/v1/tickets`
- `GET /api/v1/investigations`

These use existing `IncidentService` methods and RBAC. No incident lifecycle
behavior was added.

## CORS configuration

Add the exact frontend origin to the backend environment:

```text
OPSPILOT_CORS_ORIGINS=http://localhost:3000
```

Use the actual origin where the frontend is served, including the port.

## Local development setup

Backend:

```bash
OPSPILOT_AUTH_MODE=development \
OPSPILOT_DEV_AUTH_ENABLED=true \
OPSPILOT_HOST=127.0.0.1 \
OPSPILOT_CORS_ORIGINS=http://localhost:3000 \
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
NEXT_PUBLIC_OPSPILOT_API_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true \
npm run dev
```

Open `http://localhost:3000`.

## Production build

```bash
cd frontend
npm run build
npm run start
```

## Testing commands

Frontend:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

Backend regression:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/pip check
```

## Dashboard functionality

- Overview KPI cards and breakdowns from aggregate API data.
- Recent incidents and investigation activity with navigation.
- Incident listing with API filters, client-side search, pagination, and refresh.
- Incident detail with overview, investigations, tickets, activity timeline, approvals, and resolutions.
- Start AI Investigation form using the exact Phase 11 request schema.
- Investigation detail with structured report, evidence viewer, workflow state, and error display.
- Approval queue with report inspection, approve/reject, reason, and resume result.
- Ticket listing, filtering, details, creation, and closing.
- Settings view of connection, authentication, and role configuration.

## Known limitations

- Frontend types are manually authored from the backend contract and must be
  updated when backend schemas change.
- Dashboard totals rely on the new read-only aggregate endpoint rather than a
  dedicated metrics service.
- No end-to-end browser tests are included; component and unit tests use mocked
  API responses.
- OIDC session renewal is manual; automatic silent renewal is disabled.
- Approval decisions require the backend to be running with the same principal
  context used by the frontend token.
