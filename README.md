# OpsPilot

OpsPilot is an agentic AI support and incident investigation system with a
FastAPI backend, a LangGraph investigation workflow, deterministic diagnostic
tools, retrieval-augmented generation, and a Next.js operations dashboard.

## Local development

Backend (from the repository root):

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

Health check: `curl http://127.0.0.1:8000/health`

AI investigations are disabled by default (`OPSPILOT_AI_MODE=disabled`). To
install the embedding stack, index the knowledge base and enable the live
provider, see [`docs/ai-runtime.md`](docs/ai-runtime.md):

```bash
.venv/bin/pip install "torch==2.15.0+cpu" --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements-embeddings.txt
.venv/bin/python -m backend.app.indexing
OPSPILOT_AI_MODE=live OPSPILOT_LLM_PROVIDER=gemini \
OPSPILOT_LLM_MODEL=gemini-3.5-flash GEMINI_API_KEY=... \
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health/ai
```

Frontend:

```bash
cd frontend
npm install
NEXT_PUBLIC_OPSPILOT_API_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true \
npm run dev
```

Open `http://localhost:3000`.

Development authentication is an explicit, loopback-only opt-in. Production
requires trusted OIDC issuer/audience/JWKS configuration and fails closed at
startup when it is missing. See `.env.example` and `frontend/.env.example` for
placeholder configuration.

## API contract types

The FastAPI OpenAPI schema is the source of truth for frontend types:

```bash
cd frontend
npm run api:types   # export openapi.json and regenerate src/lib/api/generated.ts
npm run api:check   # fail if generated types are out of date
```

Do not edit `src/lib/api/generated.ts` manually.

## Verification

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

Browser end-to-end tests (real Next.js + real FastAPI + real LangGraph workflow):

```bash
cd frontend
npx playwright install chromium   # once per environment
npm run test:e2e
npm run test:e2e:ui               # interactive mode
```

## Phase 16 Evaluation

The evaluation framework is available as a standalone CLI and does not require
production services to be running.

```bash
# Run diagnostics suite (offline, default)
python -m backend.app.evaluation.cli run --suite diagnostics --mode offline
# Run retrieval suite (offline)
python -m backend.app.evaluation.cli run --suite retrieval --mode offline
# Run agent suite (offline)
python -m backend.app.evaluation.cli run --suite agent --mode offline
# Run all suites
python -m backend.app.evaluation.cli run --suite all --mode offline
```

Evaluation results are written to `evaluation-results/` as JSON and Markdown reports.
Default mode is fully offline: no API keys, no external LLM requests, no paid providers.
Live LLM evaluation is explicitly opt-in via `--mode live` with strict bounds.

```bash
# Live mode (opt-in, requires API keys)
python -m backend.app.evaluation.cli run --suite all --mode live \
  --provider openrouter --model :free
```

Report types: machine-readable JSON and human-readable Markdown. Results clearly
distinguish between mocked/offline, real local embedding/Chroma, and real external LLM
evaluation. Do not commit generated reports containing sensitive data.

For detailed documentation, see `docs/agent-evaluation.md`.
```

The E2E suite starts a test-only FastAPI process with temporary SQLite databases
and deterministic fake external providers. It includes a full
incident-to-investigation-to-approval journey and a backend restart-recovery
test. It does not mock the application API or the LangGraph interrupt/resume
lifecycle.

## Container deployment

A single-host Docker Compose package is available for reproducible deployment:

```bash
cp .env.production.example .env.production   # backend runtime config (OIDC, CORS)

# Frontend settings are compiled into the browser bundle at build time.
export NEXT_PUBLIC_OPSPILOT_API_URL=https://api.example.com
export NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY=https://issuer.example.com/
export NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID=opspilot-dashboard
export NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI=https://app.example.com/auth/callback

docker compose build

# One-time: index the knowledge base into the persistent Chroma volume.
docker compose run --rm \
  -v "$PWD/data/knowledge_base:/knowledge:ro" \
  backend python -m backend.app.indexing --knowledge-base /knowledge

docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/health/ai
```

The stack runs with `OPSPILOT_AI_MODE=live` when `.env.production` requests it;
`disabled` keeps the management API available but returns HTTP 503 for new
investigations. The backend image includes CPU-only PyTorch and the pinned
embedding stack; model weights are downloaded into the model-cache volume, not
baked into the image.

Both ports are published on loopback only (`http://127.0.0.1:3000` for the
dashboard, `http://127.0.0.1:8000` for the API). The browser must be able to
reach the API origin; never use `http://backend:8000`, which only resolves
inside the Compose network. Changing any `NEXT_PUBLIC_*` value requires
rebuilding the frontend image.

The backend runs one Uvicorn worker; application SQLite, LangGraph checkpoint
SQLite, ChromaDB and the model cache live in separate named volumes. Backups
and restore use a short offline window:

```bash
./scripts/compose-backup.sh backup
./scripts/compose-backup.sh verify ./backups/<stamp>
./scripts/compose-backup.sh restore ./backups/<stamp> --force
```

`NEXT_PUBLIC_*` values are compiled into the browser bundle at build time;
changing them requires rebuilding the frontend image. See
`docs/deployment.md` for the storage layout, secret injection, OIDC/CORS
configuration, backup semantics and single-instance limitations.

## Documentation

- `docs/deployment.md` — container architecture, persistent storage, backup and
  restore, deployment and operational limits
- `docs/production-readiness.md` — configuration, authentication, testing,
  security, and release checklist
- `docs/rest-api.md`
- `docs/frontend-dashboard.md`
- `docs/incident-management.md`
- `docs/investigation-orchestration.md`
- `docs/mcp-integration.md`
