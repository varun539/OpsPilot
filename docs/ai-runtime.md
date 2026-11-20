# OpsPilot AI runtime

Phase 15 connects the existing AI components to the production application
runtime. It does not add a second agent, RAG pipeline, vector store or
diagnostic system.

## 1. Architecture

At application startup, `backend/app/main.py` reads `OPSPILOT_AI_MODE`:

```
OPSPILOT_AI_MODE=live
        │
        ├── AIRuntimeConfig.from_env()          backend/app/ai/config.py
        ├── assemble_ai_runtime()               backend/app/ai/runtime.py
        │     ├── create_llm_provider(LLMConfig) backend/app/llm/factory.py
        │     ├── load_embedding_model()        backend/app/embeddings/model.py
        │     ├── ChromaVectorStore()           backend/app/vectorstore/chroma_store.py
        │     ├── SemanticSearchEngine()
        │     ├── BM25SearchEngine().build_index()
        │     ├── HybridSearchEngine()          RRF fusion
        │     └── RAGService()                  backend/app/rag/generator.py
        └── InvestigationOrchestrationService(..., ai_runtime=...)
              └── OpsPilotAgent → existing LangGraph graph
```

One process owns one embedding model instance, one Chroma client/collection,
one BM25 index and one RAG service. The same hybrid engine is passed both to
the graph's retrieval node and to `RAGService`, so a report is grounded in the
same records that retrieval returns.

Heavy imports are local to `assemble_ai_runtime`. With
`OPSPILOT_AI_MODE=disabled`, importing and starting the API does not import
PyTorch, sentence-transformers or ChromaDB.

## 2. Zero-cost LLM provider selection

`LLMConfig.from_env()` reads only these server-side variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPSPILOT_LLM_PROVIDER` | `gemini` | Exactly one of `gemini`, `groq`, `openrouter` |
| `OPSPILOT_LLM_MODEL` | none | **Required in live mode.** An explicitly chosen free-tier model ID |
| `OPSPILOT_LLM_API_KEY` | none | Optional neutral key; takes precedence over the provider variable |
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENROUTER_API_KEY` | none | Provider-specific server-side keys |
| `OPSPILOT_LLM_TIMEOUT` | `30` | Per-request timeout in seconds, applied to planner and report calls; max `120` |
| `OPSPILOT_LLM_MAX_OUTPUT_TOKENS` | `1200` | Hard output bound; max `4096` |
| `OPSPILOT_LLM_BASE_URL` | provider default | Only for `groq` / `openrouter`; HTTPS only |

`OPENAI_API_KEY` is intentionally **not read** and `openai` is not an accepted
`OPSPILOT_LLM_PROVIDER` value. `OpenAIProvider` remains in the codebase for the
original interface and its mocked tests, but the production factory cannot
select it, so a live runtime cannot make paid OpenAI calls.

The factory (`backend/app/llm/factory.py`) constructs exactly the selected
provider. There is no fallback chain: a failure becomes
`generation_status=provider_error`, not a retry against another or paid model.

### Adapters

| Provider | API surface | Structured output |
| --- | --- | --- |
| Gemini | Native `generateContent` (`x-goog-api-key` header) | `responseMimeType: application/json` + `responseJsonSchema`; Pydantic validation |
| Groq | OpenAI-compatible Chat Completions | `response_format: {"type":"json_object"}`; schema in system prompt; Pydantic validation |
| OpenRouter | OpenAI-compatible Chat Completions | Same as Groq; only `:free` model variants are accepted |

Gemini's OpenAI-compatible endpoint was not used because the existing
`OpenAIProvider` depends on the Responses API's `responses.parse` structured
parsing, which is not part of Gemini's compatible Chat Completions surface.

Every adapter returns the existing `LLMResult` and must produce the existing
`InvestigationReport`. The RAG service still validates every citation against
the retrieved evidence and still returns an explicit insufficient-evidence
report when evidence is missing or the provider fails.

### Zero-cost guards

- Exactly one explicitly configured provider/model/key is used.
- OpenRouter model IDs must end with `:free`; a paid ID is rejected before any
  request.
- OpenAI is not selectable, and no environment variable can cause a paid
  OpenAI request.
- There is no automatic model switch, router upgrade or paid fallback.
- Each provider call is one attempt plus the OpenAI SDK's own retries disabled
  (`max_retries=0`). No application retry loop exists.
- Per-call timeout and output-token overrides are clamped to the configured
  hard bounds.
- HTTP `429` becomes a clear rate-limit/quota error.

## 3. Provider configuration

Use a model that your own account currently lists on a zero-cost quota; the
project does not assume that every model offered by a provider is free. The
following are examples only:

```bash
# Primary option: Gemini Developer API
OPSPILOT_AI_MODE=live
export OPSPILOT_LLM_PROVIDER=gemini
export OPSPILOT_LLM_MODEL=gemini-3.5-flash   # confirm free-tier eligibility
export GEMINI_API_KEY=...                    # never in NEXT_PUBLIC_*
export OPSPILOT_LLM_TIMEOUT=30
export OPSPILOT_LLM_MAX_OUTPUT_TOKENS=1200
```

```bash
# Alternative: Groq
OPSPILOT_AI_MODE=live
export OPSPILOT_LLM_PROVIDER=groq
export OPSPILOT_LLM_MODEL=llama-3.3-70b-versatile  # confirm free-tier eligibility
export GROQ_API_KEY=...
```

```bash
# Optional: OpenRouter free variant (the :free suffix is mandatory)
OPSPILOT_AI_MODE=live
export OPSPILOT_LLM_PROVIDER=openrouter
export OPSPILOT_LLM_MODEL=meta-llama/llama-3.2-3b-instruct:free
export OPENROUTER_API_KEY=...
```

A live runtime without a model or provider key aborts startup with a message
naming `OPSPILOT_LLM_MODEL` or the relevant key variable. A configured key is
not validated at startup (that would spend quota); the first investigation
surfaces authentication, quota and validation failures as a controlled
`provider_error`.

Provider failures are mapped to the existing `LLM*Error` types. Client-visible
messages are deliberately generic: a provider 401 can echo a partially masked
key, so raw provider text is never persisted in a report or returned by the
API. The original exception remains chained for server-side debugging.

## 4. Embedding model

- Model: `sentence-transformers/all-MiniLM-L6-v2`
- Revision: `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (pinned in
  `backend/app/embeddings/model.py`)
- Device: CPU only; `verify_cpu_only()` rejects a CUDA build or an available GPU
- Dimension: 384, L2-normalized vectors
- Max sequence length required: at least 240 tokens

The runtime uses the existing `load_embedding_model()` validation: model
identity, dimension, maximum sequence length and tokenizer revision are checked
before retrieval is assembled.

## 5. CPU inference and the model cache

The backend image installs `torch==2.15.0+cpu` from the PyTorch CPU wheel index,
then installs `requirements-embeddings.txt` under
`constraints-embeddings-cpu.txt`. This prevents pip from silently replacing the
CPU build with a CUDA wheel.

`HF_HOME` and `SENTENCE_TRANSFORMERS_HOME` both point at the persistent
`opspilot-model-cache` volume (`/var/lib/opspilot/models`) in Compose. Model
weights are downloaded on first indexing or first live startup, then reused.
They are **not** baked into the image: in a fresh container with
`local_files_only=True`, loading the model fails because the cache is empty.

Image size measured for this phase: **2.73 GB** for the embedding-enabled
backend image, versus **465 MB** for the same Dockerfile without the embedding
stack (delta ≈ 2.26 GB, dominated by CPU PyTorch, transformers, torch's sympy
dependency and ChromaDB's onnxruntime/kubernetes dependencies).

## 6. ChromaDB collection

- Path: `OPSPILOT_CHROMA_DIR` (`/var/lib/opspilot/chroma` in Compose)
- Client: the existing `chromadb.PersistentClient` wrapper
- Collection: `opspilot_embeddings`
- Distance: cosine (`hnsw:space=cosine`)
- Vectors: 384-dimensional, L2-normalized, stored as float32
- IDs: deterministic token-chunk IDs such as
  `KB-001::chunk::0000::tok::0000`
- Metadata: document id, parent chunk id, title, category, component, severity,
  source path, tokenizer identity/revision, token counts and character offsets

Startup opens the existing collection; it never deletes or recreates stored
data. If the collection already contains vectors of a different dimension,
startup fails with a clear re-index instruction. An empty collection is
reported as a setup prerequisite, not treated as successful retrieval.

## 7. Hybrid retrieval initialization

`assemble_ai_runtime()` builds:

1. `SemanticSearchEngine` over the persistent store, using the single loaded
   embedding model.
2. `BM25SearchEngine`, whose in-memory index is built **once** at startup from
   the persisted documents.
3. `HybridSearchEngine`, combining both ranked lists with Reciprocal Rank
   Fusion (`k=60`).
4. `RAGService(hybrid_engine, provider)`.

Retrieval keeps semantic distance/similarity, BM25 score/rank, RRF score, final
rank and all provenance metadata. Metadata filters are limited to the existing
allowlist. The optional cross-encoder reranker remains unused (no model is
downloaded for it).

## 8. Knowledge-base indexing command

The command reuses the existing ingestion, character chunking, token chunking,
embedding and upsert pipeline:

```bash
# from the repository root, with the embedding stack installed
.venv/bin/python -m backend.app.indexing --dry-run
.venv/bin/python -m backend.app.indexing

# a different source directory
.venv/bin/python -m backend.app.indexing --knowledge-base /path/to/kb
```

Inside the deployed image, the knowledge base is not baked into the image, so
mount it read-only and run the command against the persistent volumes:

```bash
docker compose run --rm \
  -v "$PWD/data/knowledge_base:/knowledge:ro" \
  backend python -m backend.app.indexing --knowledge-base /knowledge
```

`--dry-run` parses, cleans and chunks only; it writes nothing and creates no
collection. `--local-files-only` forbids model/tokenizer downloads. If the API
is already running, restart it after indexing so the in-memory BM25 index is
rebuilt from the new records.

The command is idempotent: token-chunk IDs are deterministic and the store
upserts by ID. It never deletes the collection or unrelated records. Failures
abort without modifying previously indexed records.

Verified in the built image (`opspilot-backend:p15`) with the eight repository
documents:

| Run | Documents | Parent chunks | Token chunks | Upserted | Collection count |
| --- | --- | --- | --- | --- | --- |
| 1 (model downloaded) | 8 | 156 | 203 | 203 | 203 |
| 2 (cached) | 8 | 156 | 203 | 203 | 203 |

## 9. Runtime modes

`OPSPILOT_AI_MODE=disabled` (default):

- API, dashboard, incidents and tickets remain available.
- No ML import, provider, Chroma client or model download.
- Investigation start returns `503 ai_unavailable` with an explicit message.
- No investigation record or fake report is created.

`OPSPILOT_AI_MODE=live`:

- Requires an explicitly selected free provider, model and key.
- Loads the pinned embedding model and persistent collection at startup.
- Fails startup if the provider configuration, model or store cannot be
  assembled.
- Starts with an empty collection but reports `ready: false`
  (`reason: knowledge_base_empty`) and refuses new investigations until
  indexing has run.
- Never silently falls back to a deterministic/fake provider.

Unknown values are rejected at startup rather than coerced.

## 10. Readiness

`GET /health` is unchanged: a liveness-only response with no AI access.

`GET /health/ai` returns a sanitized snapshot:

```json
{
  "mode": "live",
  "ready": true,
  "reason": null,
  "provider_configured": true,
  "provider": "gemini",
  "provider_model": "gemini-3.5-flash",
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "embedding_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
  "collection": "opspilot_embeddings",
  "knowledge_chunks": 203,
  "checks": {
    "provider": true,
    "embedding_model": true,
    "vector_store": true,
    "knowledge_collection": true,
    "hybrid_retrieval": true,
    "rag_service": true
  }
}
```

It never exposes the key, filesystem paths, client objects, raw provider
errors or retrieved content, and never calls a provider. The endpoint is
unauthenticated like `/health` but contains only booleans, public model names
and counts.

## 11. Investigation integration

`InvestigationOrchestrationService` receives the runtime-assembled provider,
hybrid engine and RAG service. The existing graph is unchanged:

- retrieve → real hybrid retrieval over ChromaDB
- plan/diagnostics → existing deterministic tools
- synthesize → `RAGService.investigate_with_evidence()` with diagnostic
  findings and retrieved evidence
- citation validation → rejects IDs not present in the retrieved set
- `interrupt()` → the real approval pause
- approve/reject → the same thread resumes via the existing checkpointer

A provider failure produces `generation_status=provider_error` and an explicit
insufficient-evidence report; it is never presented as a successful
investigation. Incidents are never auto-resolved.

Runtime injection through `create_app(..., ai_runtime=...)` remains available
for deterministic tests and bypasses environment assembly.

## 12. Security

- API keys are read from the backend environment only. `OPENAI_API_KEY` is
  not consulted.
- No key or model weight is baked into the image or the frontend bundle.
- Client-visible provider errors are generic; they do not include provider
  response bodies or key fragments.
- `/health/ai` excludes secrets and paths.
- ChromaDB and the model cache are not published; only the loopback API port
  is exposed in the Compose package.
- Retrieval does not execute SQL or shell commands and does not accept
  arbitrary filters.
- The runtime never deletes a collection automatically.

## 13. Local setup

```bash
python -m pip install "torch==2.15.0+cpu" --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-embeddings.txt
.venv/bin/python -m backend.app.indexing
export OPSPILOT_AI_MODE=live
export OPSPILOT_LLM_PROVIDER=gemini
export OPSPILOT_LLM_MODEL=gemini-3.5-flash   # a free-tier model for your account
export GEMINI_API_KEY=...
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/health/ai
```

The application itself never loads `.env` files.

## 14. Docker setup

```bash
cp .env.production.example .env.production    # set OIDC, CORS, AI mode, key
docker compose build
docker compose run --rm \
  -v "$PWD/data/knowledge_base:/knowledge:ro" \
  backend python -m backend.app.indexing --knowledge-base /knowledge
docker compose up -d
docker compose ps
curl -s http://127.0.0.1:8000/health/ai
```

The one-off indexing container uses the same image, environment and volumes,
so embeddings land in `opspilot-chroma-data` and weights in
`opspilot-model-cache`.

## 15. Testing

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m pip check
```

Phase 15 adds:

- `tests/test_ai_runtime.py` — mode validation, missing key, single model load,
  shared store/engine, empty collection, dimension mismatch, shallow disabled
  imports.
- `tests/test_indexing.py` — dry run, initial/idempotent indexing, document and
  chunk IDs, metadata, persistence, invalid documents, CLI exit codes.
- `tests/test_ai_runtime_api.py` — disabled 503, live startup fail-closed,
  readiness shape.
- `tests/test_ai_full_stack.py` — real persistent ChromaDB, real BM25/RRF, real
  RAG, real LangGraph interrupt/resume, deterministic fake LLM.
- `tests/test_llm_provider_config.py` — provider selection, key precedence,
  paid OpenAI rejection, free-model guard, URL and limit bounds.
- `tests/test_gemini_provider.py` — native request shape, schema validation,
  quota/auth/refusal/timeout mapping, citation preservation.
- `tests/test_openai_compatible_provider.py` — Groq/OpenRouter request shape,
  free-variant guard, rate limits, truncation, usage.
- `tests/test_live_llm_smoke.py` — opt-in real provider call.

No default test makes a live provider call.

## 16. Opt-in live-provider verification

```bash
export OPSPILOT_LIVE_LLM_TESTS=1
export OPSPILOT_LLM_PROVIDER=gemini
export OPSPILOT_LLM_MODEL=...        # free-tier model for your account
export GEMINI_API_KEY=...
.venv/bin/python -m pytest tests/test_live_llm_smoke.py -q
```

The smoke test sends one synthetic health-check prompt and never prints the
key. It is skipped unless the opt-in variable and a provider key are present.
Use only synthetic data for this test.

## 17. Cost and latency

- Indexing all eight documents (203 token chunks) took ≈35 s on CPU with a
  warm model cache; the first run took ≈45 s including the model download.
- A retrieval query embeds one short query on CPU and scans 203 vectors; this
  is negligible compared with generation.
- An investigation can make up to two provider calls: one bounded planner
  call and one report-synthesis call. If the planner call fails, the existing
  deterministic fallback is used; there is no retry against another model.
  Both calls consume the configured free-tier quota.
- The model cache and Chroma collection are persistent, so restarts do not
  re-download or re-index.
- Provider inference is intended to consume a free-tier quota. No paid model
  is configured or selected automatically. Free-tier limits, availability and
  data-handling terms can change; verify them with the chosen provider.
- Live cost/latency were not measured in this phase because no free-tier API
  key was configured. All provider tests use mocked clients.

## Known limitations

- Single backend process, single Uvicorn worker; SQLite and local ChromaDB are
  single-writer.
- The BM25 index and the readiness chunk count are built once at startup.
  After re-indexing while the API is running, restart the backend so hybrid
  retrieval sees the new records (semantic retrieval already sees them, but the
  two halves would otherwise disagree).
- The optional cross-encoder reranker is not initialized.
- A configured API key is not probed at startup; validity is discovered on the
  first investigation.
- The first live startup with an empty model volume needs network access to
  Hugging Face. Pre-warm the cache with the indexing command if the runtime
  host is offline.
- This phase does not add PostgreSQL, Redis, multiple replicas, Kubernetes,
  live OIDC setup or cloud deployment.

## 18. Provider privacy and data handling

This project is a portfolio/demo deployment. Free-tier provider terms vary and
may permit logging, human review, or use of inputs to improve services. Treat
every external provider as untrusted with data.

What leaves the process on a live investigation:

- the incident question and supplied logs/stack traces,
- retrieved knowledge-base chunk text and titles,
- deterministic diagnostic summaries included in the prompt,
- the JSON Schema and system instructions.

What never leaves the process:

- provider API keys (sent only in the provider's authentication header),
- the local SQLite databases, ChromaDB files, model cache and filesystem paths,
- other incidents or retrieved records not selected for this prompt.

Use only synthetic incident descriptions, logs and stack traces when testing a
free-tier account. Do not submit real credentials, tokens, customer evidence or
confidential production logs. The application does not automatically redact
secret-looking strings from user input, so redact before submitting.

Embeddings remain local: `sentence-transformers/all-MiniLM-L6-v2` runs on CPU
and no paid embedding API is used. Only the selected generation provider
receives the text above.

## 19. Zero-cost operating notes

- `OPSPILOT_LLM_MODEL` has no default. Select a model ID that the provider
  currently offers on a zero-cost quota for your account. Model catalogues and
  free eligibility change; re-check before deployment.
- OpenRouter paid IDs are rejected unless they end in `:free`. Other providers
  cannot be programmatically checked for account-level free eligibility, so
  the deployment operator remains responsible for choosing a free model.
- A `429` response is reported as a rate-limit/quota failure. The runtime does
  not retry, rotate keys, switch models, or route to a paid endpoint.
- If a free model is retired or its schema support changes, the investigation
  fails explicitly with `provider_error`; it does not silently degrade to a
  paid model or a fabricated report.
- Gemini and Groq may enforce model-specific structured-output restrictions.
  OpsPilot always re-validates the report locally, so a provider-side schema
  hint is not trusted as proof of validity.
