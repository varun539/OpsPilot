# OpsPilot MCP Integration

## What MCP is and how OpsPilot uses it

The Model Context Protocol (MCP) lets an external AI client discover and invoke
OpsPilot tools over a standard protocol. OpsPilot exposes a controlled subset of
its capabilities through a standalone stdio MCP server.

The MCP layer is an adapter only. It delegates to the existing diagnostic tools,
hybrid retrieval engine, and Phase 8 incident-management service. It does not
duplicate their logic.

## MCP server architecture

Package: `backend/app/mcp_server/`

- `server.py`: builds the `MCPServer` and runs stdio transport.
- `tools.py`: registers MCP tools and validates their inputs.
- `dependencies.py`: constructs application dependencies without import-time
  side effects.
- `exceptions.py`: client-safe MCP error types.

The server is separate from the FastAPI application. It is not started by
FastAPI and does not expose HTTP.

## Python SDK dependency and version

- Package: `mcp`
- Version pinned in `requirements.txt`: `mcp==2.3.0`
- Transport used: stdio.

## Available MCP tools

Knowledge base:

- `search_knowledge_base`

Diagnostics:

- `analyze_logs`
- `analyze_stack_trace`
- `classify_http_error`
- `validate_configuration`
- `diagnose_database_error`
- `correlate_incidents`

Incidents:

- `create_incident`
- `get_incident`
- `list_incidents`
- `get_incident_history`

Tickets:

- `create_ticket`
- `get_ticket`
- `list_incident_tickets`

Investigations:

- `get_investigation`
- `list_incident_investigations`
- `list_investigation_evidence`

## Input and output examples

Create an incident:

```json
{
  "name": "create_incident",
  "arguments": {
    "title": "API 500 errors",
    "severity": "high"
  }
}
```

Search the knowledge base:

```json
{
  "name": "search_knowledge_base",
  "arguments": {
    "query": "database connection pool",
    "top_k": 5
  }
}
```

All tools return structured JSON-compatible dictionaries. Pydantic models are
serialized with JSON-mode serialization. Dates and UUIDs are serialized as
strings.

## Security boundaries

- stdio transport only.
- No public HTTP endpoint.
- No arbitrary code execution, shell execution, SQL execution, or filesystem access.
- No approval, rejection, resolution, deletion, or arbitrary status-change tools.
- Retrieval and diagnostic tools are read-only.
- Incident and ticket creation use the existing controlled service methods.
- Input lengths, result counts, and identifiers are bounded.
- Errors are returned as safe messages without internal stack traces.
- Application logs go to stderr, keeping stdout protocol-safe.
- The application database and checkpoint database remain separate.

## Why stdio transport was selected

stdio keeps the server local, avoids exposing an unauthenticated network
service, and lets compatible clients launch it as a subprocess.

## How the server is launched

From the project root:

```bash
python -m backend.app.mcp_server
```

The process reads MCP JSON-RPC messages from stdin and writes responses to
stdout.

## How to connect an MCP-compatible client

Example client configuration:

```json
{
  "mcpServers": {
    "opspilot": {
      "command": "python",
      "args": ["-m", "backend.app.mcp_server"],
      "cwd": "/path/to/OpsPilot"
    }
  }
}
```

Use the project's virtual environment Python interpreter when available.

## Environment variables and configuration

- `OPSPILOT_APP_DB`: application SQLite database path. Defaults to
  `data/opspilot_app.sqlite`.
- `OPSPILOT_CHECKPOINT_DB`: separate LangGraph checkpoint database path.
- Retrieval uses the existing ChromaDB collection under `chroma/`.

No OpenAI API key is required to start the server or use the exposed tools.

## Local testing instructions

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_mcp_server.py tests/test_mcp_tools.py tests/test_mcp_integration.py -q
```

## Known limitations

- No HTTP transport.
- No authentication or authorization beyond local process isolation.
- No MCP resources or prompts are exposed.
- No approval or remediation operations are exposed.
- SQLite application storage only.

## Future integration opportunities

- FastAPI endpoints can reuse the same service layer.
- Agent orchestration can persist investigations through `IncidentService`.
- Additional MCP tools can be added only after preserving the approval workflow.