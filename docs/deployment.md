# OpsPilot Deployment

This document describes the Phase 14 container package for OpsPilot — two
production images (FastAPI backend, Next.js frontend), a single-host Docker
Compose stack, persistent storage, backup/restore — plus the Phase 15 AI
runtime that now runs inside it. See `docs/ai-runtime.md` for provider,
embedding, indexing and readiness details.

Everything below was exercised locally on Linux with Docker Engine 29.9 and
Docker Compose v5.6. No public cloud deployment, DNS change, or TLS
provisioning is included.

## 1. Deployment architecture

```
                    browser
                       │
        ┌──────────────┴───────────────┐
        │                              │
   http://localhost:3000         http://127.0.0.1:8000
        │                              │
┌───────▼────────┐            ┌────────▼────────┐
│   frontend     │            │    backend      │
│ Next.js 16     │            │ FastAPI/Uvicorn │
│ node, uid 1000 │            │ uid 10001       │
└────────────────┘            └────────┬────────┘
                                       │
                     ┌─────────────────┼──────────────────┐
                     │                 │                  │
              app SQLite        checkpoint SQLite    ChromaDB + HF cache
              (volume)            (volume)             (volumes)
```

- The browser talks **directly** to the API origin. It never uses Docker
  service names.
- One backend container, one Uvicorn worker. There is no worker queue and no
  background job process.
- The two SQLite databases stay separate by design. Never point
  `OPSPILOT_APP_DB` and `OPSPILOT_CHECKPOINT_DB` at the same file.

## 2. Runtime versions and image layout

| Component | Version | Base image |
| --- | --- | --- |
| Backend | Python 3.14.2 | `python:3.14.2-slim-bookworm` |
| Frontend build/runtime | Node.js 24.22 | `node:24.22-bookworm-slim` |
| Next.js | 16.4.6 | — |

Both Dockerfiles are multi-stage:

- **Backend** (`backend/Dockerfile`): a builder stage installs
  `requirements.txt`, CPU-only PyTorch from the PyTorch CPU wheel index, and
  `requirements-embeddings.txt` under `constraints-embeddings-cpu.txt` into
  `/opt/venv`; the runtime stage copies that venv, `backend/` and `scripts/`.
  It runs as `opspilot` (uid 10001), binds `0.0.0.0:8000`, logs to
  stdout/stderr, and ships a health check. The build imports
  `backend.app.main` so broken production imports fail the build. The
  embedding stack makes the image ≈2.73 GB (≈465 MB without it); no model
  weights are baked in.
- **Frontend** (`frontend/Dockerfile`): `npm ci` from the committed lockfile,
  then `next build` with `NEXT_STANDALONE=1`, which enables
  `output: "standalone"` only for the image build (`next start` does not support
  standalone output, so local builds keep the normal output). The runtime stage
  contains only the standalone server and static assets and runs as `node`
  (uid 1000) on port 3000.

Build contexts: backend uses the repository root; frontend uses `./frontend`.
Matching `.dockerignore` files keep `.git`, `.venv`, `node_modules`, `.next`,
caches, local databases, Chroma data, model caches and secrets out of images.

## 3. Compose architecture

`compose.yaml` defines two services and five named volumes:

| Service | Image | Ports (loopback) | Depends on |
| --- | --- | --- | --- |
| `backend` | `opspilot-backend:local` | `127.0.0.1:8000` | — |
| `frontend` | `opspilot-frontend:local` | `127.0.0.1:3000` | backend healthy |

Both use `restart: unless-stopped`, `init: true`, `no-new-privileges`,
`cap_drop: ALL`, bounded `json-file` logging and image health checks. There are
no privileged containers, Docker socket mounts, published database ports, or
secret literals in the Compose file.

`backend` reads `.env.production` (optional in the file so `docker compose
config` works without secrets). If authentication configuration is missing, the
application fails closed at startup and the container exits/restarts — by
design.

## 4. Persistent storage

The application's defaults are relative paths (`data/…`, `<repo>/chroma`).
Containers override them explicitly; Compose declares the paths so they cannot
drift from the mounts:

| Data | Container path | Environment variable | Volume |
| --- | --- | --- | --- |
| Incidents, tickets, reports, approvals, events | `/var/lib/opspilot/app/opspilot_app.sqlite` | `OPSPILOT_APP_DB` | `opspilot-app-data` |
| LangGraph workflow checkpoints | `/var/lib/opspilot/checkpoints/opspilot_checkpoints.sqlite` | `OPSPILOT_CHECKPOINT_DB` | `opspilot-checkpoint-data` |
| ChromaDB index | `/var/lib/opspilot/chroma` | `OPSPILOT_CHROMA_DIR` | `opspilot-chroma-data` |
| Hugging Face / SentenceTransformers cache | `/var/lib/opspilot/models` | `HF_HOME`, `SENTENCE_TRANSFORMERS_HOME` | `opspilot-model-cache` |
| Backup staging | `/var/lib/opspilot/backups` | — | `opspilot-backups` |

`OPSPILOT_CHROMA_DIR` is an additive Phase 14 setting: when unset the store
still defaults to `<repo>/chroma`, so local workflows are unchanged.

WAL sidecars (`-wal`, `-shm`) live next to their database in the same volume.
Every volume is independent; no volume is nested inside another. No database or
Chroma data is placed in the frontend directory or image.

## 5. Environment variables

Backend variables are read from the process environment (the app does not load
`.env` itself; Compose injects them). See `.env.production.example`.

| Variable | Required in production | Purpose |
| --- | --- | --- |
| `OPSPILOT_AUTH_MODE` | yes (`production`) | Selects the auth mode |
| `OPSPILOT_DEV_AUTH_ENABLED` | must be `false`/unset | Explicit loopback dev bypass |
| `OPSPILOT_HOST` | yes (`0.0.0.0`) | Bind-host used by the dev-auth guard |
| `OPSPILOT_AUTH_ISSUER` | yes | Trusted OIDC issuer |
| `OPSPILOT_AUTH_AUDIENCE` | yes | Required token audience |
| `OPSPILOT_AUTH_JWKS_URL` | yes, HTTPS | Trusted JWKS endpoint |
| `OPSPILOT_AUTH_ROLES_CLAIM` | no (`roles`) | Role claim path |
| `OPSPILOT_AUTH_ALGORITHMS` | no | Allowed signing algorithms |
| `OPSPILOT_CORS_ORIGINS` | yes | Exact dashboard origins |
| `OPSPILOT_AI_MODE` | yes (`live` or `disabled`) | Selects the AI runtime; see `docs/ai-runtime.md` |
| `OPSPILOT_LLM_PROVIDER` | no (`gemini`) | `gemini`, `groq` or `openrouter`; OpenAI is not selectable |
| `OPSPILOT_LLM_MODEL` | yes when `live` | Explicit free-tier model ID; no default |
| `OPSPILOT_LLM_API_KEY` or `GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENROUTER_API_KEY` | yes when `live` | Server-side provider key |
| `OPSPILOT_LLM_TIMEOUT/MAX_OUTPUT_TOKENS` | no | Bounded request limits |
| `OPSPILOT_LLM_BASE_URL` | no | HTTPS override for Groq/OpenRouter only |
| `OPSPILOT_APP_DB`, `OPSPILOT_CHECKPOINT_DB`, `OPSPILOT_CHROMA_DIR` | set by Compose | Storage paths |

Frontend variables are **build-time**; they are compiled into the browser
bundle and cannot be changed by restarting the container. See
`frontend/.env.production.example`.

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_OPSPILOT_API_URL` | Browser-visible API origin |
| `NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY` | OIDC authority |
| `NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID` | Public client ID |
| `NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI` | Callback URL |
| `NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE` | Requested scopes |
| `NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM` | Display-only role claim |
| `NEXT_PUBLIC_OPSPILOT_DEV_AUTH` | Local dev bypass (must be false in production) |

There is no server-side runtime override for `NEXT_PUBLIC_*` values. Changing
the API URL or OIDC configuration requires rebuilding the frontend image.

## 6. Secrets

- Real credentials are never committed, baked into image layers, or exposed to
  the frontend.
- `.env.production` is passed only to the backend service at runtime and is
  git-ignored. Inject production values from your secret manager by templating
  that file (or a Compose `secrets:` setup) on the host.
- Only public, non-secret `NEXT_PUBLIC_*` values are build arguments.
- Startup validation messages name variables but never print values.
- Keep free-provider keys backend-only; they never appear in a
  `NEXT_PUBLIC_*` variable. `OPENAI_API_KEY` is not used by the AI runtime.

## 7. Frontend-to-backend networking and CORS

`NEXT_PUBLIC_OPSPILOT_API_URL` must be an origin the **user's browser** can
reach:

- Local Compose: `http://127.0.0.1:8000` (published on loopback).
- Reverse proxy: `https://api.example.com` (placeholder).
- Never `http://backend:8000`; that only resolves inside the Compose network.
- Never a development URL in a production build.

These values are build arguments. Compose takes them from the shell
environment (or a Compose `.env` file in the repository root, which is a
different file from the backend `.env.production`). A production frontend that
is missing the OIDC authority/client ID will render the fail-closed
configuration screen, so build it explicitly:

```bash
export NEXT_PUBLIC_OPSPILOT_API_URL=https://api.example.com
export NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY=https://issuer.example.com/
export NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID=opspilot-dashboard
export NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI=https://app.example.com/auth/callback
export NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM=roles
docker compose build frontend
```

Set `OPSPILOT_CORS_ORIGINS` to the exact dashboard origin, for example
`http://localhost:3000` locally or `https://app.example.com` behind a proxy.
Do not combine `*` with credentialed CORS; the backend disables credentials
when a wildcard is configured, and that combination should not be used.

For a future reverse-proxy deployment, terminate TLS at the proxy, keep the
backend unpublished (or loopback-only), and route:

- `https://app.example.com` → `frontend:3000`
- `https://api.example.com` → `backend:8000`

Both hosts must be the same registrable site for the current CORS/session
model, or CORS must list the dashboard origin explicitly.

## 8. Production authentication

Production behavior is unchanged and fail-closed:

- `OPSPILOT_AUTH_MODE=production` requires issuer, audience and an HTTPS JWKS
  URL; otherwise startup raises and the container exits.
- Enabling `OPSPILOT_DEV_AUTH_ENABLED` in production is rejected.
- Development authentication requires all of: development mode, the explicit
  flag, and a loopback `OPSPILOT_HOST`. It is for local verification only.
- Tokens are verified against the JWKS key set with issuer, audience, expiry
  and algorithm checks. Roles come from the configured claim.
- The test harness in `tests/e2e_backend.py` is never copied into the image.

**Limitation:** no live OIDC provider has been exercised. The container tests
used explicit development authentication; production identity-provider
integration remains unverified against a real issuer.

## 9. Health checks, lifecycle and logs

- Backend: `GET /health` (no auth, no external calls, no database work, no
  filesystem detail) returns `{"status":"ok"}`. It is used by the image and
  Compose health checks.
- Frontend: the container fetches `/` (a redirect to `/dashboard`) and checks
  for an OK status.
- Compose starts the frontend only after the backend is healthy.
- `stop_grace_period` allows Uvicorn to finish and the application to close its
  checkpointer/database handles on SIGTERM.
- Logs go to stdout/stderr: `docker compose logs -f backend`.
- `restart: unless-stopped` restarts crashed or unhealthy-restarted services
  while respecting an intentional `docker compose stop`.
- Do not scale the backend: `--scale backend=2` would put two writers on the
  same SQLite files.

### Local verification auth note

`OPSPILOT_HOST` is the value the **dev-auth guard validates**; it is not the
Uvicorn bind address. The container command always binds `0.0.0.0:8000` inside
the container, and the Compose port mapping is what limits reachability. When
using development authentication, keep the published port loopback-bound
(`127.0.0.1:8000:8000`, as in `compose.yaml`). Never publish the backend port
on a public interface while dev auth is enabled, and never use dev auth in a
real deployment.

## 10. Backup and restore

`scripts/compose-backup.sh` implements a short maintenance-window backup:

1. Stop the backend so ChromaDB is quiescent.
2. Copy both SQLite databases with the SQLite backup API (consistent snapshot)
   and verify `PRAGMA integrity_check`.
3. Copy the Chroma directory and write a manifest.
4. Copy the backup out of the `opspilot-backups` volume to `./backups/<stamp>`.
5. Restart the backend and wait for health.

Commands:

```bash
./scripts/compose-backup.sh backup
./scripts/compose-backup.sh verify ./backups/<stamp>
./scripts/compose-backup.sh restore ./backups/<stamp>          # refuses existing data
./scripts/compose-backup.sh restore ./backups/<stamp> --force
```

Restore behavior:

- A backup is verified before any write.
- Without `--force`, restore refuses to overwrite existing databases or Chroma
  data.
- With `--force`, previous databases are preserved beside the target as
  `*.pre-restore-<stamp>`; previous Chroma data is preserved inside the Chroma
  volume under `.pre-restore-<stamp>/`.
- SQLite WAL/SHM files are never copied blindly while the app is running; the
  offline window and the backup API make the snapshot consistent.
- Restore keeps application and checkpoint databases separate.

Set `COMPOSE="docker compose -p <project>"` to operate on an isolated stack.
The wrapper only touches the selected Compose project and never removes volumes.

Verified locally against an isolated Compose project: backup succeeded,
`verify` reported `integrity_check=ok` for both databases and 5 Chroma files,
restore without `--force` was refused without modifying data, and restore with
`--force` removed a post-backup incident while preserving the completed
investigation, its approval record, the checkpoint database and the Chroma
record (read back with ChromaDB after restore).

## 11. Local commands

```bash
# 1. Configure (backend runtime values; git-ignored)
cp .env.production.example .env.production
# edit .env.production: OIDC issuer/audience/JWKS, CORS origin, OpenAI key

# 2. Build (see §7 for the frontend build variables)
docker compose build

# 3. One-time knowledge-base indexing into the persistent Chroma volume
docker compose run --rm \
  -v "$PWD/data/knowledge_base:/knowledge:ro" \
  backend python -m backend.app.indexing --knowledge-base /knowledge

# 4. Start
docker compose up -d

# 5. Status, health, logs
docker compose ps
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/health/ai
curl -fsS http://127.0.0.1:8000/health/ai | grep -q '"ready": true'
docker compose logs -f backend

# 6. Stop
docker compose down          # keeps volumes and data
docker compose down -v       # DESTROYS data; use only for throwaway stacks
```

For local verification only (never a real deployment), use explicit
development authentication. Compose binds both ports to loopback; set
`OPSPILOT_HOST=127.0.0.1` in `.env.production` so the dev-auth guard accepts it,
build the frontend with `NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true` and
`NEXT_PUBLIC_OPSPILOT_API_URL=http://127.0.0.1:8000`, and keep
`OPSPILOT_CORS_ORIGINS=http://localhost:3000`. The guard only checks
`OPSPILOT_HOST`; do not publish port 8000 beyond loopback in this mode.

## 12. CI

`.github/workflows/ci.yml` runs:

- Backend: dependency install, Ruff, full pytest, `pip check`.
- Frontend: `npm ci`, ESLint, TypeScript, unit tests, OpenAPI drift check,
  production build.
- Containers: `docker compose config --quiet`, then backend and frontend image
  builds.

CI requires no production secrets, pushes no images and deploys nothing.
Playwright is not part of CI; browser tests remain a local/verification step.

## 13. Single-instance limitations

This deployment is deliberately:

- one backend instance and **one Uvicorn worker**;
- SQLite for application records and for LangGraph checkpoints;
- a local persistent ChromaDB directory;
- synchronous investigation execution inside the starting request.

It does **not** provide horizontal scaling, high availability, or
multi-writer safety. Do not run multiple backend replicas or multiple workers
against these volumes. PostgreSQL, an external vector store and distributed job
execution are possible future migrations, but are out of scope.

The embedding stack is part of the backend image, and `OPSPILOT_AI_MODE=live`
assembles the OpenAI provider, pinned embedding model, persistent ChromaDB,
BM25/RRF hybrid retrieval and RAG service at startup. `disabled` keeps the
management API available but returns `503 ai_unavailable` for new
investigations. An empty knowledge collection is reported by `/health/ai` and
refuses investigations until `python -m backend.app.indexing` has run.

The first live startup (or indexing run) needs the pinned model in
`HF_HOME`; it is downloaded once into `opspilot-model-cache` when the host can
reach Hugging Face. See `docs/ai-runtime.md` for the full runtime contract.

## 14. Verification record

Run on Linux, Docker Engine 29.9.2, Compose v5.6.1, isolated Compose project
`opspilotp14` with its own named volumes:

| Check | Result |
| --- | --- |
| Backend image build | pass (imports `backend.app.main` in the build) |
| Frontend image build | pass (`NEXT_STANDALONE=1`, standalone server verified on port 3000) |
| Non-root runtime | backend uid 10001, frontend uid 1000 |
| Compose startup | both services healthy |
| Production fail-closed | missing OIDC values exit the container; dev auth on a public bind is rejected |
| Browser to API | headless Chromium loaded the dashboard and called `/api/v1/incidents`, `/api/v1/dashboard/stats`, `/api/v1/investigations` (all 200, no failed requests) |
| Real workflow | incident created, investigation paused at the real `interrupt()` with `202 awaiting_approval` |
| Persistence | report, diagnostic evidence and pending approval persisted in the app DB; checkpoint rows in `/var/lib/opspilot/checkpoints` |
| Backend container recreation | pending approval survived; the original LangGraph thread resumed and completed (`approved`) |
| Full stack recreation | incident, checkpoint database and Chroma record survived (`docker compose down` without `-v`) |
| Backup / restore | verified as described in §10 |

Phase 15 (same host, isolated Docker volumes `p15-chroma`, `p15-models`,
`p15-app`):

| Check | Result |
| --- | --- |
| Embedding-enabled image build | pass; 2.73 GB vs 465 MB baseline; no model weights in the image |
| CPU-only PyTorch | `2.15.0+cpu`, `torch.version.cuda is None` |
| Container indexing | 8 documents, 156 parent chunks, 203 token chunks, 203 records |
| Idempotent re-index | 203 upserted again, collection count stayed 203 |
| Live startup | assembles the selected provider + 203-chunk hybrid retrieval; `/health/ai` ready (Gemini rechecked with a dummy key; no request sent) |
| Invalid provider key | `generation_status=provider_error`, explicit insufficient-evidence report, generic client message (no key fragment) |
| Disabled mode | `/health/ai` disabled; investigation start returns `503 ai_unavailable` |
| Missing key/model in live mode | startup aborts naming the provider key or `OPSPILOT_LLM_MODEL` |
| OpenRouter paid model | startup aborts before any request; `:free` is mandatory |
| Documented Compose flow | `compose build` → `compose run` indexing (203 records) → `up`; backend and frontend healthy, `/health/ai` ready |
| Recreation recovery | pending investigation survived backend container recreation and resumed its original thread to `completed` |
| Backup / restore with populated vectors | backup, verify and `--force` restore OK; 1 incident, 1 completed investigation and 203 Chroma vectors restored |

These are container builds and local Docker runs. Nothing was deployed to a
public host. No successful live LLM call was made: no free-tier provider key
was configured, so provider behavior is covered only by mocked tests.

## 15. What this phase does not include

No public cloud deployment, DNS or certificate provisioning, Kubernetes,
Redis, Celery, PostgreSQL migration, new auth providers, new agent/RAG
features, frontend redesign, or payment integration.
