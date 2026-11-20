# OpsPilot Agent Architecture

## System architecture

OpsPilot Phase 7 adds a bounded LangGraph investigation agent under
`backend/app/agent/`. It reuses the existing hybrid retrieval, RAG, OpenAI
provider, and six deterministic investigation tools.

## State schema

`InvestigationState` is a typed dictionary containing JSON-serializable fields
such as request data, available inputs, retrieval results, diagnostic results,
correlation results, report, warnings, and approval status. No clients, models,
or credentials are stored.

## Graph nodes and edges

The graph runs:

`START -> validate_request -> classify_incident -> retrieve_knowledge ->
plan_investigation -> execute_diagnostics -> correlate_findings ->
synthesize_report -> validate_report -> human_review -> finalize -> END`

Conditional edges skip to finalization on validation or planning failure.

## Tool selection policy

The planner may select only from the fixed allow-list of six diagnostic tools.
Unknown tools are rejected. Tool arguments are built from validated request
fields. A deterministic fallback is used when the provider is unavailable.

## Diagnostic execution

Tool execution uses a fixed registry. Failures are isolated and recorded as
structured tool errors. At most six tools run per investigation.

## RAG integration

The agent reuses `RAGService` patterns and the existing citation validator.
Diagnostic findings are supplied as supplemental context. Knowledge citations
are validated against retrieved chunk IDs.

## Error handling

Typed agent exceptions and structured error details are used. There are no
unbounded retries. Failed stages preserve earlier findings.

## Checkpointing

SQLite checkpointing is provided through `SQLiteCheckpointer` and
`OPSPILOT_CHECKPOINT_DB`. In-memory checkpointers are used for tests.

## Thread IDs

Each investigation uses a thread ID for checkpoint isolation and resume.

## Interrupt and resume

`human_review` calls `interrupt()` after report generation. The graph resumes
with `Command(resume=...)` using the same thread ID. Approval records review
outcome only and does not execute production changes.

## Human approval

Supported decisions are `approved` and `rejected`, with optional reviewer
comments. Invalid decisions are recorded as failures.

## Security constraints

No shell execution, eval, arbitrary imports, network access, credential
storage, or unredacted secrets in checkpoints.

## Execution limits

Maximum six tools and bounded graph steps. No unrestricted loops.

## Example investigation

A user submits an HTTP 500 incident with sanitized logs. The agent retrieves
knowledge, plans tools, runs diagnostics, synthesizes a report, and pauses for
human approval.

## Current limitations

The agent has no frontend, FastAPI endpoints, MCP, or incident database. It
requires a configured LLM provider for non-fallback planning and synthesis.