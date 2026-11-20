# OpsPilot Incident Management

## Purpose

Phase 8 adds durable application-level persistence for incidents, tickets,
investigations, evidence, approvals, and resolutions. This layer is separate
from LangGraph checkpoint persistence.

## Database architecture

- Application database path: `OPSPILOT_APP_DB`, default `data/opspilot_app.sqlite`.
- LangGraph checkpoint path: `OPSPILOT_CHECKPOINT_DB`, default
  `data/opspilot_checkpoints.sqlite`.
- The two SQLite databases are never shared.
- Foreign keys are enabled for every connection.
- Transactions are explicit and roll back on failure.

## Entity relationships

- An incident can have many tickets.
- An incident can have many investigations.
- An investigation can have many evidence records.
- Approvals reference an incident and optionally an investigation.
- Resolutions reference an incident and optionally an investigation.
- Referential integrity is enforced with `ON DELETE RESTRICT`.

## Lifecycle

Incident statuses:

`open -> investigating -> awaiting_approval -> resolved -> reopened/closed`

Ticket statuses:

`open -> in_progress -> blocked/resolved -> closed`

Approvals:

`pending -> approved/rejected`

Completed approvals cannot be overwritten.

## Repository and service responsibilities

- `ApplicationDatabase`: connection and schema management.
- `IncidentRepository`: SQL CRUD and row mapping.
- `IncidentService`: validation, lifecycle rules, orchestration.

## Environment configuration

- `OPSPILOT_APP_DB`: application database path.
- `OPSPILOT_CHECKPOINT_DB`: LangGraph checkpoint database path.

## Initialization

```python
from backend.app.incidents import IncidentService

service = IncidentService(db_path="data/opspilot_app.sqlite")
incident = service.create_incident(title="API 500 errors", severity="high")
```

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_incident_management.py -q
```

## Known limitations

- No FastAPI endpoints yet.
- No authentication or authorization.
- SQLite only; PostgreSQL support is not implemented.
- No background workers or notifications.

## Future integration points

- Agent orchestration can call `IncidentService` to persist investigations.
- FastAPI endpoints will wrap these services in a later phase.
- MCP integration can expose the same service operations.