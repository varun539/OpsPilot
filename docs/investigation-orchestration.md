# OpsPilot Investigation Orchestration

Phase 11 connects the REST API, incident-management database, LangGraph agent,
diagnostic tools, retrieval, RAG reporting, and human approval into one durable
investigation lifecycle.

The orchestration layer coordinates existing components. It does not
reimplement planning, diagnostics, retrieval, report generation, incident
business rules, or the LangGraph interrupt/resume mechanism.

## Architecture

```text
Client
  -> FastAPI /api/v1 (JWT + RBAC)
  -> IncidentService (application database)
  -> InvestigationOrchestrationService
       -> OpsPilotAgent (existing compiled LangGraph graph)
            -> deterministic diagnostic tools
            -> hybrid retrieval + RAG provider
            -> human_review interrupt
       -> SQLite checkpointer (separate checkpoint database)
```

Packages:

- `backend/app/orchestration/`: lifecycle coordination, workflow status,
  approval resume, evidence/report persistence, recovery.
- `backend/app/api/routes/orchestration.py`: thin authenticated endpoints.
- `backend/app/agent/`: unchanged planning, diagnostics, retrieval, report,
  interrupt, and resume behavior.
- `backend/app/incidents/`: canonical incident, investigation, approval,
  evidence, resolution, and activity persistence.

The FastAPI process and the standalone MCP server remain independent.

## Persistence responsibilities

Two independent SQLite databases are used:

| Database | Default path | Contents |
|---|---|---|
| Application | `OPSPILOT_APP_DB` or `data/opspilot_app.sqlite` | incidents, tickets, investigations, evidence, approvals, resolutions, incident events |
| LangGraph checkpoint | `OPSPILOT_CHECKPOINT_DB` or `data/opspilot_checkpoints.sqlite` | graph checkpoints and pending interrupts |

Application writes and checkpoint writes are **not** one transaction. The
orchestration layer accounts for this with explicit workflow status, persisted
thread IDs, idempotent evidence insertion, and retryable resume state.

## Additive schema changes

No tables are dropped or recreated. `ApplicationDatabase.initialize()` adds the
following columns to an existing `investigations` table when missing, plus two
partial unique indexes: one on `thread_id` and one on `incident_id` for active
orchestrated investigations (identified by a non-null `thread_id`).

- `thread_id TEXT` — server-generated LangGraph thread ID, unique when present.
- `workflow_status TEXT` — agent workflow lifecycle.
- `last_error TEXT` — sanitized failure summary.

A new additive table records incident activity:

```sql
CREATE TABLE incident_events (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
```

`metadata` is strict JSON. Unsupported objects raise instead of being silently
stringified.

## Thread ID generation and association

The server generates the thread ID before graph execution:

```text
thread-<32 hex characters>
```

Sequence:

1. Create the investigation row.
2. Persist `thread_id`, `workflow_status = running`, and investigation status
   `running`.
3. Invoke the graph with that exact thread ID.
4. Reuse the persisted thread ID for every resume.

Clients cannot supply a thread ID, inspect checkpoint contents, or choose a
different thread during approval.

## Investigation lifecycle

Workflow status (agent execution) is separate from incident status (business
lifecycle):

| Workflow status | Meaning |
|---|---|
| `created` | Record created before execution |
| `running` | Graph is executing |
| `awaiting_approval` | Graph paused at the human-review interrupt |
| `resuming` | Approval recorded, resume in progress |
| `completed` | Graph finished (approved or rejected) |
| `failed` | Execution or persistence failed |
| `resume_failed` | Approval recorded but resume/finalization failed; retryable |

Investigation record status uses the existing Phase 8 values (`started`,
`running`, `completed`, `failed`, `cancelled`).

Incident transitions use existing `IncidentService` rules:

- start: `open`/`reopened` -> `investigating`
- interrupt: `investigating` -> `awaiting_approval`
- after review: `awaiting_approval` -> `investigating`

An approved investigation **never** automatically resolves the incident. A
resolution still requires an explicit resolution operation with documented
details.

## Starting an investigation

`POST /api/v1/incidents/{incident_id}/investigations`

Authorization: `operator` or `admin`. `viewer` receives `403`.

Request body is optional. It accepts bounded versions of the existing agent
inputs:

```json
{
  "question": "Why are API requests timing out?",
  "logs": "ERROR pool exhausted",
  "stack_trace": "Traceback ...",
  "http_status": 503,
  "response_text": "Service Unavailable",
  "response_headers": {"retry-after": "30"},
  "configuration": {"DB_POOL_SIZE": "5"},
  "configuration_rules": {"DB_POOL_SIZE": {"min": 10}},
  "database_error": "too many connections",
  "events": [{"timestamp": "2026-01-01T00:00:00Z", "service": "api"}],
  "retrieval_filters": {"component": "api"},
  "requested_top_k": 5
}
```

Limits: 10,000 characters per question, 200,000 characters per large text
field, 200,000 combined text characters, at most 200 events, and
`requested_top_k` between 1 and 50. Inputs are validated by Pydantic and by the
existing `InvestigationRequest` model.

Stored incident metadata (number, title, description, severity, priority,
environment, service) is appended to `incident_context`.

Responses:

- `202 Accepted` — paused for human approval.
- `200 OK` — completed synchronously without an interrupt.
- `404 Not Found` — incident does not exist.
- `409 Conflict` — an investigation is already active for the incident.
- `422 Unprocessable Entity` — invalid or oversized input.

```json
{
  "incident_id": "...",
  "investigation_id": "...",
  "workflow_status": "awaiting_approval",
  "approval_status": "pending",
  "message": "investigation paused for human approval"
}
```

The handler is synchronous (`def`), so FastAPI runs it in a worker thread and
the event loop is not blocked. No background task queue is introduced.

## Report and evidence persistence

When the graph interrupts:

1. The structured `InvestigationReport` from the graph state is persisted
   without reconstruction from partial strings. Source references, retrieval
   metadata, provider/model/usage, and citation-validation fields from the RAG
   response are retained alongside it.
2. Evidence is stored with the existing `Evidence` model:
   - `diagnostic` records per tool result,
   - `knowledge_base` records per retrieved chunk (chunk ID, document ID,
     title, source path, source metadata),
   - `correlation` records for correlation output.
3. A `pending` human-review approval is created exactly once.
4. Workflow status becomes `awaiting_approval`; the incident transitions to
   `awaiting_approval`.

Evidence insertion is idempotent: existing `(evidence_type, source)` pairs are
skipped, so retries and resume do not duplicate records. Raw secrets are not
stored intentionally; diagnostics already redact secrets where applicable.

## Approval and resume

`POST /api/v1/investigations/{investigation_id}/approval`

Authorization: `operator` or `admin`. `decided_by` always comes from the
authenticated principal.

```json
{"decision": "approved", "reason": "Verified against production metrics"}
```

`decision` must be `approved` or `rejected`. Responses:

- `200 OK` — graph resumed and final state persisted.
- `403 Forbidden` — viewer.
- `404 Not Found` — investigation does not exist.
- `409 Conflict` — wrong workflow state, duplicate decision, or contradictory
  decision.
- `502 Bad Gateway` — resume or final persistence failed; approval remains
  recorded and the workflow is `resume_failed`.

Before resuming, the service verifies the investigation, its workflow status,
the pending approval, and the persisted thread ID. The approval update is a
conditional `UPDATE ... WHERE status = 'pending'`, so only one decision wins.
The investigation is then claimed atomically with
`UPDATE ... WHERE workflow_status = 'awaiting_approval'` (or `resume_failed`),
preventing concurrent resumes.

Resume uses the existing agent interface:

```python
agent.resume(thread_id=stored_thread_id, decision=decision, reviewer_comment=reason)
```

which invokes the existing compiled graph with `Command(resume=...)` on the
original checkpoint. No new investigation or thread is created.

After a successful resume:

- the final report and evidence are persisted idempotently,
- workflow status becomes `completed` (or `failed`),
- the incident returns to `investigating`,
- approval decision, actor, reason, and timestamps remain stored,
- activity events are appended.

`GET /api/v1/investigations/{investigation_id}/approval` returns the approval
record (`viewer` or higher), or `404` if none exists.

## Activity and audit trail

`GET /api/v1/incidents/{incident_id}/history` now includes an `events` array in
chronological order. Recorded event types:

- `investigation_started`
- `human_review_requested`
- `approval_approved`
- `approval_rejected`
- `workflow_resumed`
- `investigation_completed`
- `investigation_failed`
- `resume_failed`

Each event records the authenticated actor, associated investigation, optional
reason, sanitized error summary, and timestamp. Actor identities are taken only
from the verified principal.

## Failure and recovery

| Failure | Behavior |
|---|---|
| Graph invocation fails | Investigation marked `failed`; incident remains in a valid non-resolved state; sanitized error stored; client receives `502` without a stack trace. |
| Report/evidence persistence fails after interrupt | Investigation marked `failed`; no misleading `awaiting_approval`; `502` returned. |
| Approval recorded, resume fails | Approval remains recorded; workflow becomes `resume_failed`; retryable. |
| Resume succeeds, final persistence fails | Workflow becomes `resume_failed`; the recorded approval is preserved and the same checkpoint can be resumed again (LangGraph returns the final state). |
| Application restart while awaiting approval | Thread ID and approval live in the application DB; the graph interrupt lives in the checkpoint DB. A new process reconstructs both and resumes the original thread. |
| Duplicate approval request | Conditional approval update and workflow-state check produce `409`. |
| Two simultaneous approvals | Only one conditional update succeeds; the other receives `409`. |
| API timeout during graph execution | The graph may still complete; the persisted checkpoint is authoritative. Reconciliation/retry uses the stored thread. |

### Retrying a failed resume

Re-submit the **same** decision to the approval endpoint:

```json
{"decision": "approved", "reason": "retry after dependency recovery"}
```

The workflow status `resume_failed` permits this. A contradictory decision is
rejected. A new approval record is never created; the original decision remains
immutable.

Retrying invokes `resume` on the original thread again. If the checkpoint
already advanced past the interrupt, LangGraph returns the final state, which is
then persisted. There is no second approval record.

## Application restart recovery

The recovery test performs the real sequence:

1. Create an incident and start an investigation on SQLite checkpoints.
2. Let the real LangGraph graph pause at the human-review interrupt.
3. Close the orchestration service, agent, and checkpointer.
4. Reconstruct `IncidentService` against the same application database.
5. Reconstruct the checkpointer and agent against the same checkpoint database.
6. Read the persisted `thread_id` and `awaiting_approval` status.
7. Approve the investigation.
8. Resume the original thread and persist the final state.

## Running

```bash
OPSPILOT_APP_DB=data/opspilot_app.sqlite \
OPSPILOT_CHECKPOINT_DB=data/opspilot_checkpoints.sqlite \
OPSPILOT_AUTH_MODE=development \
OPSPILOT_DEV_AUTH_ENABLED=true \
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Development authentication requires a loopback bind. Production uses JWT/JWKS
configuration from Phase 10; see `docs/rest-api.md`.

## Testing

```bash
.venv/bin/python -m pytest tests/test_investigation_orchestration.py \
  tests/test_investigation_api.py \
  tests/test_investigation_approval_flow.py \
  tests/test_investigation_recovery.py
```

Tests use temporary application and checkpoint databases and fake LLM/retrieval
dependencies. They exercise the real compiled LangGraph graph, actual
`interrupt()`/`Command(resume=...)`, restart recovery, database migration,
idempotency, concurrency, and failure paths. No live provider or network calls
are required.

## Known limitations

- Execution is synchronous up to the interrupt or completion. There is no
  Celery/Redis/distributed job platform, and a client timeout does not cancel a
  graph run already in progress.
- Concurrent execution protection is database-backed for approval claims and
  workflow state. It assumes the shared SQLite application database is the
  coordination point; it is not a cross-region distributed lock.
- Application and checkpoint databases cannot commit atomically. Reconciliation
  is explicit and retry-based, not a two-phase commit.
- Graph execution happens inside the API process. A process crash mid-run relies
  on checkpoint replay and the persisted workflow status rather than a worker
  supervisor.
- The structured report is persisted with its source references and safe
  provider/retrieval metadata. Retrieved chunk text is stored once as evidence,
  not duplicated inside the report.
- Incident activity is a simple append-only event table, not an enterprise audit
  platform.
