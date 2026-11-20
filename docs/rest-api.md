# OpsPilot REST API

## Architecture

Phase 10 exposes OpsPilot capabilities through a versioned REST API under
`/api/v1`. Route handlers delegate to the existing `IncidentService`; no
second business-logic layer is introduced. The REST API and the standalone MCP
server remain independent.

Application structure:

- `backend/app/main.py`: `create_app()` factory and application lifespan
- `backend/app/api/router.py`: root and versioned routers
- `backend/app/api/routes/`: health, incidents, tickets, investigations
- `backend/app/api/dependencies.py`: service and principal dependencies
- `backend/app/api/exceptions.py`: consistent error handling
- `backend/app/auth/`: JWT verification, principal model, RBAC

## Running the API

From the project root:

```bash
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

OpenAPI documentation:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/openapi.json`

## Request and response examples

Create an incident:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/incidents \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"title":"Database latency","severity":"high"}'
```

Error response:

```json
{
  "error": {
    "code": "not_found",
    "message": "record not found",
    "request_id": "..."
  }
}
```

## Endpoints

All `/api/v1` endpoints require authentication.

Incidents:

- `POST /api/v1/incidents`
- `GET /api/v1/incidents`
- `GET /api/v1/incidents/{incident_id}`
- `PATCH /api/v1/incidents/{incident_id}`
- `GET /api/v1/incidents/{incident_id}/history`
- `GET /api/v1/incidents/{incident_id}/tickets`
- `POST /api/v1/incidents/{incident_id}/tickets`
- `GET /api/v1/incidents/{incident_id}/investigations`
- `GET /api/v1/incidents/{incident_id}/approvals`
- `GET /api/v1/incidents/{incident_id}/resolutions`

Tickets:

- `GET /api/v1/tickets/{ticket_id}`
- `PATCH /api/v1/tickets/{ticket_id}`
- `POST /api/v1/tickets/{ticket_id}/close`

Investigations:

- `GET /api/v1/investigations/{investigation_id}`
- `GET /api/v1/investigations/{investigation_id}/evidence`

## Authentication

Authentication is provider-neutral JWT bearer-token validation using a trusted
JWKS endpoint. No passwords are stored.

Environment variables:

- `OPSPILOT_AUTH_MODE`: `production` (default) or `development`
- `OPSPILOT_AUTH_ISSUER`
- `OPSPILOT_AUTH_AUDIENCE`
- `OPSPILOT_AUTH_JWKS_URL`
- `OPSPILOT_AUTH_ROLES_CLAIM` (default `roles`)
- `OPSPILOT_AUTH_ALGORITHMS` (optional comma-separated allowlist)
- `OPSPILOT_HOST` (default `127.0.0.1`)
- `OPSPILOT_DEV_AUTH_ENABLED` (default `false`)
- `OPSPILOT_CORS_ORIGINS` (comma-separated origins; default none)

Production mode fails closed when issuer, audience, or JWKS URL is missing.

JWT validation checks signature, issuer, audience, expiration, not-before,
subject, algorithm allowlist, and trusted JWKS key ID.

## Generic OIDC provider example

1. Register OpsPilot as an application in your identity provider.
2. Configure a confidential or public client as appropriate for your frontend.
3. Set the issuer URL, audience, and JWKS URL in OpsPilot environment variables.
4. Issue access tokens containing the configured roles claim.
5. Call the API with `Authorization: Bearer <access_token>`.

Example:

```bash
export OPSPILOT_AUTH_MODE=production
export OPSPILOT_AUTH_ISSUER=https://issuer.example/
export OPSPILOT_AUTH_AUDIENCE=opspilot-api
export OPSPILOT_AUTH_JWKS_URL=https://issuer.example/.well-known/jwks.json
export OPSPILOT_AUTH_ROLES_CLAIM=roles
```

## Roles

Roles: `viewer`, `operator`, `admin`. Role checks are hierarchical.

- Viewer: read operations
- Operator: viewer plus create/update/close operations
- Admin: operator plus administrative operations

A valid JWT without a recognized role receives `viewer`.

## Local development mode

Development bypass is enabled only when all of the following are true:

- `OPSPILOT_AUTH_MODE=development`
- `OPSPILOT_DEV_AUTH_ENABLED=true`
- API binds to a loopback address

Example:

```bash
OPSPILOT_AUTH_MODE=development \
OPSPILOT_DEV_AUTH_ENABLED=true \
OPSPILOT_HOST=127.0.0.1 \
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Development mode cannot be enabled on a public bind address.

## CORS

`OPSPILOT_CORS_ORIGINS` defaults to no cross-origin access. Wildcard origins
are not used with credentialed requests.

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

MCP server remains independently runnable:

```bash
python -m backend.app.mcp_server
```

## Security boundaries

- No delete endpoints.
- No arbitrary SQL or filesystem access.
- No approval decision or LangGraph resume endpoints in this phase.
- No stack traces, SQL, or credentials in error responses.
- Application and checkpoint databases remain separate.
