# Phase 21 — Live LLM Runtime Validation

**Date:** 2026-11-19
**Branch:** `release/opspilot-showcase`
**HEAD:** `0eb9a41b5c5fb27db5eaaafba6f2142c5ca58812`
**Working tree at start and end:** clean (only this report is added)

**Overall status: PARTIAL**

The live provider boundary is verified: a real Gemini request succeeded (HTTP 200,
JSON parsed and schema-validated). The end-to-end investigation did **not** produce
a Gemini-generated report. The single permitted investigation run was blocked by
provider-side HTTP 503 after the planner call had already timed out at its
hardcoded 30 s limit. Per the Phase 21 rules, the failed request was not retried,
so report generation, report citation quality and live model output quality remain
**unverified**.

---

## 1. Environment and model configuration

| Setting | Value |
| --- | --- |
| `OPSPILOT_AI_MODE` | `live` |
| `OPSPILOT_LLM_PROVIDER` | `gemini` |
| `OPSPILOT_LLM_MODEL` | `gemini-3.1-flash-lite` |
| Provider key variable | `GEMINI_API_KEY` (present, non-empty; value never read into output) |
| `OPSPILOT_LLM_TIMEOUT` | `90` for the investigation run only (see §3.2) |
| `OPSPILOT_LLM_MAX_OUTPUT_TOKENS` | `1200` (default) |
| `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` | `1` / `1` |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, dim 384 |
| Collection | `opspilot_embeddings`, 203 chunks (repo-local `chroma/`, gitignored) |
| Python | 3.14.2 |

The runtime was confirmed to read exactly these values through
`AIRuntimeConfig.from_env()` → `LLMConfig.from_env()`; the key is read from
`OPSPILOT_LLM_API_KEY` first and then `GEMINI_API_KEY` (`backend/app/llm/config.py`).
`LLMConfig.api_key` is excluded from `repr`, and provider errors never include
response text, URLs or headers.

## 2. Local startup validation (no external calls)

`assemble_ai_runtime` assembled the real stack, not a fixture:

- `GeminiProvider` (`provider_name="gemini"`, model `gemini-3.1-flash-lite`)
- pinned CPU sentence-transformer loaded from the local cache
- persistent `ChromaVectorStore` at the repo-local `chroma/` directory, 203 vectors, dim 384
- `BM25SearchEngine` (index size 203), `SemanticSearchEngine`, `HybridSearchEngine`
- `RAGService`, and the full LangGraph agent with a SQLite checkpointer

Readiness snapshot (`AIRuntime.status()`, the `/health/ai` payload): `mode=live`,
`ready=true`, `reason=null`, all six checks true (`provider`, `embedding_model`,
`vector_store`, `knowledge_collection`, `hybrid_retrieval`, `rag_service`).

Graph nodes compiled: `validate_request`, `classify_incident`, `retrieve_knowledge`,
`plan_investigation`, `execute_diagnostics`, `correlate_findings`,
`synthesize_report`, `validate_report`, `human_review`, `finalize`.

Real hybrid retrieval for the INC-011 question returned 5 chunks in the documented
Phase 18 order/window (`KB-008`, `KB-005`, `KB-008`, `KB-003`, `KB-001`), including
both expected documents (`KB-008`, `KB-001`) with at most two chunks per document.

## 3. Live Gemini smoke test

Executed exactly as specified:

```
OPSPILOT_LIVE_LLM_TESTS=1 python -m pytest tests/test_live_llm_smoke.py -q
```

**Result: PASS — one real generation request.**

| Field | Value |
| --- | --- |
| HTTP status | `200` |
| Requests made | 1 (`http_requests=1`) |
| Model (requested) | `gemini-3.1-flash-lite` |
| Model (`modelVersion`) | `gemini-3.1-flash-lite` |
| HTTP latency | 26.499 s |
| Total generation latency | 26.599 s |
| Finish reason | `STOP` |
| Token usage | prompt 71, candidate 6, total 77 (`serviceTier=standard`) |
| Response top-level keys | `candidates`, `modelVersion`, `responseId`, `usageMetadata` |
| `promptFeedback` | absent |
| JSON parsing | passed → `SmokeAnswer(status="ok")` |
| Provider metadata | `provider=gemini`, `zero_cost=free_tier` |

The test file needed no correction. It is gated on `OPSPILOT_LIVE_LLM_TESTS=1`
plus a configured key, uses a synthetic health-check prompt, and asserts only on
the parsed model, provider metadata and output type.

Measurement was done with an untracked pytest plugin in `/tmp/phase21/` that wraps
`httpx.Client.post` and `GeminiProvider.generate_structured` to record status,
latency, finish reason and usage. It does not change assertions, retry anything, or
record URLs/headers/keys.

## 4. Full live investigation (INC-011)

The prepared harness `/tmp/phase21/live_investigation.py` **did not exist** in this
environment (the `/tmp` directory had been cleared). A replacement harness was
written at that path around the existing production workflow — it does not
reimplement or bypass any workflow stage. It drives:

INC-011 synthetic request → `InvestigationOrchestrationService`
→ real LangGraph agent → pinned local embeddings → ChromaDB semantic retrieval
→ BM25 → hybrid retrieval → `RAGService` context builder → real Gemini API
→ report parsing → `CitationValidator` → human-review interrupt → approval/resume
→ SQLite persistence → recovery over fresh service objects.

Synthetic data only (`INC-011` from `backend/app/evaluation/datasets/incidents_v1.jsonl`);
all application and checkpoint SQLite files were created in a temporary directory;
the repository Chroma collection was opened read-only (no indexing, no deletion).

### 4.1 Live requests made by the investigation: 2, both failed

| # | Call | Result | Latency | Detail |
| --- | --- | --- | --- | --- |
| 1 | Planner (`InvestigationPlan`, 878 prompt chars) | `LLMTimeoutError` | 30.079 s | `planner.py` hardcodes `timeout=30.0`, so `OPSPILOT_LLM_TIMEOUT=90` did not apply. The planner silently fell back to its deterministic plan (`planner_used=false`, `fallback=true`). |
| 2 | Report (`InvestigationReport`, 5 205 prompt chars) | `LLMResponseError` — **HTTP 503** | 3.611 s | Provider-side `Service Unavailable`. Model/auth were already proven by the immediately preceding smoke test, so this is classified as transient provider unavailability, not a configuration or schema defect (a schema/payload fault would return 400). |

No automatic retry was attempted, and no second investigation was started.

### 4.2 What did complete (verified)

- **Retrieval reached the model path with real evidence:** the report prompt was
  built from the 5 hybrid-retrieved chunks (`KB-008::chunk::0000::tok::0000`,
  `KB-005::chunk::0000::tok::0001`, `KB-008::chunk::0001::tok::0000`,
  `KB-003::chunk::0005::tok::0000`, `KB-001::chunk::0007::tok::0000`) — not a
  hardcoded fixture.
- **Deterministic diagnostics ran:** `log_analyzer` (`LOG_ERROR_ENTRIES`,
  `LOG_HTTP_5XX`), `http_error_classifier` (`HTTP_500`), `database_diagnostics`
  (`DB_POOL_EXHAUSTION`).
- **Provider error handling behaves correctly end to end:** the failure surfaced as
  `generation_status="provider_error"`, `valid=false`, `citation_valid=false`,
  `validation_errors=["Gemini API request failed (status 503)"]`, with no key,
  URL or provider body in the message. `validate_report` then set
  `execution_status="failed"`.
- **Human review interrupts execution:** `start_investigation` returned
  `workflow_status="awaiting_approval"`, `approval_status="pending"` — a branch the
  service only takes when `"__interrupt__"` is present in the graph result. At that
  point the investigation was `status="running"`, an `approval` row was `pending`,
  and 8 evidence rows were persisted (5 `knowledge_base`, 3 `diagnostic`).
- **Approval resumes the same thread:** `approve_investigation(...)` returned
  `workflow_status="completed"`; final graph state had the same `thread_id`,
  `approval_status="approved"`, and identical `retrieval_results`.
- **Persistence:** investigation `status="completed"`, report row present with a
  summary, retrieved chunk IDs stored as `knowledge_base` evidence, event trail
  exactly
  `investigation_started → human_review_requested → approval_approved → workflow_resumed → investigation_completed`.
- **Recovery:** fresh `IncidentService`/orchestration objects over the same SQLite
  files restored `status="completed"`, the same `thread_id`, the persisted report,
  and all 5 retrieval results with `approval_status="approved"`. A second approval
  was correctly rejected with `InvalidStateTransitionError`.
- **Invented citations are rejected:** appending the synthetic ID
  `KB-999::invented::token-000` to a hypothesis caused `CitationValidator` to return
  `valid=false` with `Unknown citation chunk ID: KB-999::invented::token-000`. This
  check is local and made no API call.

### 4.3 What is NOT verified

- **No Gemini-generated report exists.** `gemini_report_non_empty=true` and
  `report_matches_schema=true` in the run record refer to the *provider-error
  placeholder* report (summary `"Gemini API request failed (status 503)"`,
  `evidence_status="insufficient"`), not to model output. They must not be read as
  proof of live generation.
- **Schema adherence of live model output** (e.g. `responseJsonSchema` handling of
  the `EvidenceReference | str` union, hypothesis/action citation fields) is untested.
- **Citation quality of live output** — whether the model cites retrieved chunk IDs —
  is untested; only the validator's rejection path was exercised.
- **Planner quality via live LLM** is untested: the only planner call timed out and
  fell back deterministically.
- **Latency and token usage for a report-sized response.** The only successful
  response was 77 tokens at 26.5 s; that is not representative of a 1200-token
  budget report.

## 5. Regression results

| Check | Command | Result |
| --- | --- | --- |
| Full backend suite, offline defaults | `python -m pytest` (no `OPSPILOT_*` overrides) | **517 passed, 5 skipped** — identical to the pre-Phase-21 baseline |
| Lint | `python -m ruff check .` | **All checks passed** |
| Live test opt-in | suite run without `OPSPILOT_LIVE_LLM_TESTS` | `tests/test_live_llm_smoke.py` **skipped** even though `GEMINI_API_KEY` is present |
| Embedding integration opt-in | suite run without `OPSPILOT_LOCAL_EMBEDDING_TESTS` | 4 tests in `test_agent_real_retrieval_integration.py` **skipped** |
| Secret scan | scan of 1 476 repo files + both `/tmp` run artifacts + the run SQLite files | 0 occurrences of the key |
| Git state | `git status --porcelain` | clean before changes; only this report added |

No dependency installs, model downloads, builds, commits or pushes were performed.

## 6. Findings and known limitations

1. **Provider 503 is the Phase 21 blocker.** The free-tier `gemini-3.1-flash-lite`
   endpoint returned `503` for the report request minutes after a successful smoke
   request. This is external availability, not application code.
2. **The planner timeout is hardcoded** (`backend/app/agent/planner.py`,
   `timeout=30.0`) and therefore ignores `OPSPILOT_LLM_TIMEOUT`. With observed
   first-token latencies around 26 s, planner calls are likely to time out and
   silently degrade to the deterministic fallback. Not changed in this phase —
   it is a production behaviour decision, not part of the validation task.
3. **A failed report can still end as `completed`:** `validate_report` marks the
   run failed, but the graph always proceeds to `human_review`, and approving there
   makes `finalize` set `execution_status="completed"`. This run's investigation is
   `completed` with `citation_valid=false`. Worth a deliberate decision in a later
   phase.
4. **`chroma/chroma.sqlite3` mtime changed** when the persistent client was opened
   (size unchanged, count still 203, dim 384, no documents added or removed). The
   directory is gitignored.
5. The replacement `/tmp` harness is untracked scaffolding, not part of the
   application. A stale assertion in it (`"__interrupt__" in get_state().values`)
   reported the interrupt as unverified even though the service took its
   interrupt-only branch; the correct evidence is recorded in §4.2.

## 7. Recommended next phase

Re-run the Phase 21 step 6 investigation only — no application change is required —
when the Gemini endpoint is not returning 503, to obtain a real generated report and
validate output schema adherence and citation quality. That re-run should also
record report-size token usage and latency. Separately, decide whether the planner
should honour `OPSPILOT_LLM_TIMEOUT` and whether an approved-but-failed report
should be able to reach `completed`.

---

# Phase 21.1 — LLM Failure Handling and Workflow Reliability Audit

**Date:** 2026-11-19 · **Branch:** `release/opspilot-showcase` · **Base HEAD:** `0eb9a41b5c5fb27db5eaaafba6f2142c5ca58812`

**Status: PASS.** Every failure mode in scope is now represented as a failure in
state, persistence and the API response, and is covered by mocked-provider tests.
No Gemini or other external request was made in this phase.

## 8. Root causes found

1. **Approval erased the failure verdict (critical).** `finalize()` in
   `backend/app/agent/nodes.py` set `execution_status="completed"` for *any*
   approved run, and `approve_investigation()` mapped
   `execution_status in {completed, approved, validated_report, rejected}` to
   `workflow_status="completed"`, `status="completed"` and `last_error=None`.
   Report validity was never consulted. This is exactly the Phase 21 outcome:
   `citation_valid=false` was persisted, yet the investigation was `completed`.
2. **A raised synthesis error produced a "valid" placeholder.** When
   `rag_service.investigate_with_evidence()` (or the in-node provider path) raised,
   `synthesize_report()` fell back to `_insufficient_rag_response()`, which
   hardcoded `valid=True` and `citation_valid=True`. `validate_report()` therefore
   passed the placeholder and the workflow proceeded to human review as if
   synthesis had succeeded. `RAGService._error_response()` was already correct;
   only the node-level fallback was wrong.
3. **Planner failures were silent.** `InvestigationPlanner.plan()` ended in
   `except Exception: return fallback, False`, recording nothing. A provider crash
   was indistinguishable from "no provider configured", and no warning, field or
   event captured the degradation.
4. **A failed pause looked identical to a normal pause.** `InvestigationStartResponse`
   carried no report validity or generation status, and both cases returned the
   message `investigation paused for human approval`.
5. **The planner timeout is hardcoded** (`timeout=30.0` in `planner.py`), so
   `OPSPILOT_LLM_TIMEOUT` has no effect on planning — the direct cause of the
   Phase 21 planner timeout at 30.079 s under a 90 s configured timeout.

## 9. Exact failure lifecycle (before → after)

**Planner timeout.** Before: exception swallowed → deterministic plan → diagnostics
run → report generation attempted; nothing anywhere recorded that the planner LLM
failed. After: same bounded deterministic fallback, plus
`state["planner_error"] = "LLMTimeoutError: ..."` (secret-redacted, ≤300 chars) and a
warning `Planner LLM call failed; deterministic fallback plan used: ...`; report
generation continues unchanged.

**Report HTTP 503.** Before: `provider_error`, `valid=false` in graph state →
`validate_report` set `execution_status="failed"` → graph still interrupted → service
persisted the placeholder and opened an approval → the pause response was
indistinguishable from success-path review → approval ran `finalize` →
`completed`, `status=completed`, `last_error=None`. After: the placeholder is
persisted with `valid=false`, `citation_valid=false`,
`generation_status="provider_error"`; the pause response carries
`report_valid=false`, `report_status="provider_error"` and a message stating
report generation failed; approval is still recorded (`approval_status="approved"`
for auditability) but finalizes as `execution_status="failed"`,
`workflow_status="failed"`, `status="failed"`, `last_error` explaining that the
report is invalid, and the event stream records `investigation_failed`.

**Both failing.** Planner falls back and is recorded; the report fails; the run
pauses, then fails on approval, with all retrieved chunks and diagnostic findings
retained as evidence.

**Malformed JSON / empty output / refusal / truncated output.** Each raises a
provider error that becomes `generation_status="provider_error"`, `valid=false`, and
is never approvable into completion. Malformed output that fails Pydantic
validation is treated identically.

**Checkpoint containing a failed generation.** Restart recovery restores
`awaiting_approval` with the failed report; approval after restart is gated by the
same `_report_validity` check, so an older checkpoint cannot be approved into
`completed` either.

**Successful generation.** Unchanged: report `valid=true`, `citation_valid=true`,
review pause, approval → `completed`, report and summary persisted.

## 10. Changes made

| File | Change |
| --- | --- |
| `backend/app/agent/planner.py` | Record `last_error` (secret-redacted, truncated) when a configured provider fails; fallback behaviour and `(plan, used)` signature unchanged. |
| `backend/app/agent/state.py` | Add `planner_error` to `InvestigationState` and `DEFAULT_STATE`. |
| `backend/app/agent/nodes.py` | Surface planner degradation as `planner_error` + warning; synthesis exceptions/None results now produce `generation_status="provider_error"`, `valid=false`; `human_review` and `finalize` refuse to mark an approved invalid report as completed. |
| `backend/app/orchestration/service.py` | `_report_state()` helper; approval/final-status gating on report validity; resume persistence writes `failed` for invalid approved reports; explicit `last_error`; start response reports validity and a distinct message; secrets still sanitized. |
| `backend/app/orchestration/schemas.py` | Additive optional `report_valid` / `report_status` on start and approval responses (existing fields unchanged). |
| `backend/app/incidents/service.py` | Persist the RAG envelope's `valid` flag alongside `generation_status` / `citation_valid` so stored reports are self-describing. |
| `frontend/openapi.json` | Regenerated from the updated FastAPI app (additive only: 44 inserted lines, no removals). |

No architecture change, no dependency added, no retrieval/citation/DB redesign, no
change to the planner fallback itself (it is deterministic, bounded to 6 tools, and
now observable).

## 11. New tests — `tests/test_llm_failure_handling.py` (15 tests)

Mocked providers only; no network. Cases:

1. planner timeout → deterministic fallback, `planner_error`, warning, review reached;
2. report 503 → review pause with `report_valid=false` / `provider_error` and persisted flags;
3. RAGService-level provider error → same invalid semantic;
4. approval after failed generation → `failed`, not `completed`; events show `investigation_failed`;
5. planner + report failure → evidence preserved, approval still fails safely;
6. malformed/invalid model output → `provider_error`;
7. empty output → `provider_error`;
8. provider refusal → `provider_error`, no fabricated evidence;
9. synthesis exception → invalid report and secret redaction (`api_key=...` absent);
10. invented citation (`KB-999::invented`) → `citation_validation_failed`, cannot complete;
11. restart recovery from a checkpoint with a failed report → approval still fails;
12. successful generation → review, then completed with report persisted and no `last_error`;
13. successful planner clears `planner_error`;
14. synthesis service returning `None` → `provider_error`;
15. truncated/incomplete output (`LLMIncompleteOutputError`) → `provider_error`.

## 12. Test results

| Check | Result |
| --- | --- |
| `python -m pytest` | **532 passed, 5 skipped** (Phase 21 baseline 517 + 15 new; same 5 opt-in skips) |
| `python -m ruff check .` | **All checks passed** |
| Live tests | still opt-in: `tests/test_live_llm_smoke.py` skipped without `OPSPILOT_LIVE_LLM_TESTS`; no external requests made |

## 13. Remaining limitations and risks

1. **Planner timeout is still hardcoded at 30 s** and ignores
   `OPSPILOT_LLM_TIMEOUT`. With observed ~26 s latency this makes planner fallbacks
   likely; it is now visible but not configurable.
2. **`rejected` still finalizes as `workflow_status="completed"`** (pre-existing
   review-outcome semantics, covered by existing tests). For a failed report the
   status reflects review completion, not investigative success; the approval
   response's `report_valid=false` disambiguates it.
3. **No-evidence outcomes remain `valid=True` by design**
   (`insufficient_evidence`, e.g. diagnostics without knowledge-base hits). Those
   are not generation failures and can still be approved; distinguishing "nothing
   to investigate" from "investigation failed" is intentional.
4. **No live verification yet.** These guarantees are proven with mocked providers
   only; Phase 21's live report path is still unverified pending a successful Gemini
   report call.
5. **Frontend not updated** to render `report_valid` / `report_status`; the fields
   exist in OpenAPI and are populated, but the UI still shows the generic pause text.
6. **Stale-thread protection** relies on the DB workflow-status claim plus the
   resume check; a report whose content changed outside the workflow would not be
   detected. The graph checkpoint remains the single source of truth for a review.

## 14. Readiness for another single live investigation

**Ready.** The workflow now records provider failures, never completes an approved
invalid report, preserves evidence, and surfaces validity in both pause and approval
responses. A single live investigation can be run again as soon as the Gemini
endpoint stops returning 503; expected requests remain 1 planner + 1 report, and
the planner will still fall back after its 30 s bound rather than blocking the run.

---

# Phase 21.2 — Runtime Reliability, UI Visibility & Live Gemini Validation

**Date:** 2026-11-19 · **Branch:** `release/opspilot-showcase` · **HEAD:** `0eb9a41b5c5fb27db5eaaafba6f2142c5ca58812`

**Status: PASS.** A genuine Gemini-generated investigation report was produced,
validated, paused for human review, approved, persisted as completed, and recovered
from disk. Exactly **2 real Gemini requests** were made (1 planner + 1 report); no
smoke test was repeated and nothing was retried.

## 15. Configurable planner timeout

`OPSPILOT_LLM_TIMEOUT` already existed with validated semantics in
`backend/app/llm/config.py`: default `30`, minimum `0.1`, maximum `120`, applied
per request by the provider adapters via `bounded_timeout()`. Only the planner
bypassed it with a literal `timeout=30.0`.

`InvestigationPlanner` now resolves its bound once, in this order:

1. an explicit `timeout=` argument, if it is a real number in `(0, 120]`;
2. the validated `timeout` on the provider's `LLMConfig` (i.e. `OPSPILOT_LLM_TIMEOUT`);
3. `DEFAULT_TIMEOUT` (30 s) for providers without usable configuration.

Invalid explicit values (zero, negative, above the maximum, non-numeric) fall
through to the configured value instead of raising or silently becoming zero.
The planner still catches every provider/validation error, records a redacted
`last_error`, and returns the deterministic bounded fallback. No new environment
variable was added and the report path already honoured the same setting.

Tests added: provider default, provider without config, custom configured value
observed on the outgoing call, invalid override rejection, timeout → documented
fallback with a sanitized `LLMTimeoutError`, and invalid
`OPSPILOT_LLM_TIMEOUT` env values (`abc`, `0`, `500`) rejected by `LLMConfig`.
`.env.example` and `docs/ai-runtime.md` now state that the setting applies to
planner and report calls.

## 16. Frontend failure visibility

All changes follow the existing cards, badges and muted-panel palette; no page was
redesigned and no dependency was added.

- `reportGenerationState()` classifies a persisted report envelope from
  `generation_status`, `valid` and `citation_valid`, tolerating older records that
  lack those fields (treated as valid).
- **Failed generation:** the investigation page shows a red "Report generation
  failed" alert and the report tab shows a failure card with the sanitized
  `generation_status`, evidence status and sanitized validation/error strings. The
  placeholder summary, hypotheses, actions and citations are not rendered as an AI
  report. `provider_error` and `citation_validation_failed` both map here.
- **Insufficient evidence:** keeps its existing report layout, preceded by an
  explicit "Insufficient evidence — this is not a provider failure" note.
- **Valid report awaiting approval:** the pause banner states the report was
  generated and its citations validated and is ready for review.
- **Completed investigation:** the persisted final report and status render as
  before.
- **Approval dialog:** when the report failed, approval is disabled (with an
  explanatory alert and a submit guard); rejection remains available. Failed
  approval results render in the error palette rather than the success palette.
- **Planner degradation:** not rendered. The backend records `planner_error` in
graph state only; it is not part of the investigation API contract, so the UI has
no field to display. This is documented as a limitation rather than adding an
unrequested API field.

Frontend tests added: insufficient-evidence notice, failed-report state (no
placeholder rendered), citation-validation failure state, and approval blocked for
a failed report. Regenerated `frontend/src/lib/api/generated.ts` from the updated
OpenAPI snapshot (it now includes `report_valid` / `report_status`).

## 17. Live investigation — actual results (not mocked)

Harness: the existing Phase 21 script, copied to a Phase 21.2 run with its own
results file (`/tmp/phase21/phase212_live_investigation_results.json`); Phase 21
results were left untouched. It uses the synthetic INC-011 evaluation case, a
temporary SQLite app/checkpoint directory, the real runtime (pinned
`sentence-transformers/all-MiniLM-L6-v2` revision `1110a243…`, ChromaDB with 203
chunks, BM25, hybrid retrieval, RAG service, LangGraph), and the real Gemini API.

Environment: `OPSPILOT_AI_MODE=live`, `OPSPILOT_LLM_PROVIDER=gemini`,
`OPSPILOT_LLM_MODEL=gemini-3.1-flash-lite`, `OPSPILOT_LLM_TIMEOUT=90`,
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`. The credential was verified as
present (never printed or persisted; the harness's SQLite secret scan was clean).

| # | Request | Outcome | Latency | Prompt chars | Tokens (prompt/completion/total) |
| --- | --- | --- | --- | --- | --- |
| 1 | Planner (`InvestigationPlan`) | HTTP success; plan rejected by allow-list validation → deterministic fallback | 3.164 s | 878 | 525 / 301 / 826 |
| 2 | Report (`InvestigationReport`) | HTTP success; schema valid; citations valid | 11.464 s | 5205 | 2297 / 739 / 3036 |

Total real Gemini requests: **2**. No retries, no parallel runs, no additional
diagnostic generations.

**Planner detail.** The call succeeded but the model selected a tool outside the
allow-list, so `_validate_plan` raised `ValueError: unknown tool selected` and the
deterministic plan ran (`log_analyzer`, `http_error_classifier`,
`database_diagnostics`). Phase 21.1 observability worked exactly as intended:
state recorded `planner_used=false`, `fallback_used=true`,
`planner_error="ValueError: unknown tool selected"`, and the fallback warning. The
planner system prompt names the "fixed allow-list" but does not enumerate the tool
IDs, which is the likely cause; the prompt was deliberately not changed in this
phase because a live re-test was not permitted.

**Report.** `generation_status=success`, `valid=true`, `citation_valid=true`,
`provider=gemini`, `model=gemini-3.1-flash-lite`. The report is a real Gemini
assessment of connection-pool exhaustion with 1 hypothesis, 3 supporting evidence
items, 1 recommended action, limitations and missing information. Cited chunks:
`KB-008::chunk::0000::tok::0000`, `KB-008::chunk::0001::tok::0000`,
`KB-005::chunk::0000::tok::0001` — all five retrieved chunk IDs were supplied in
the prompt and every citation is a subset of them.

**Citation validation.** The validator agreed with the workflow
(`citation_validator_agrees=true`). A local copy with an injected
`KB-999::invented::token-000` reference was rejected with "Unknown citation chunk
ID", proving invented citations are blocked without further API calls.

**Human review and persistence.** The run paused with `workflow_status=awaiting_approval`,
a `pending` approval, `status=running`, and 8 evidence rows (5 knowledge-base + 3
diagnostic). A checkpoint-history `__interrupt__` write confirms the LangGraph
interrupt. Approval resumed the **same thread**, yielding `execution_status=completed`,
`workflow_status=completed`, `status=completed`, `last_error=null`, with the report
persisted (`valid=true`, `citation_valid=true`). Events: `investigation_started`,
`human_review_requested`, `approval_approved`, `workflow_resumed`,
`investigation_completed`.

**Recovery.** Fresh service objects over the same SQLite files restored the
completed investigation, preserved the thread ID, report summary, 5 retrieved
chunks and the approved/completed execution state; re-approval was refused as an
invalid state transition.

**Harness caveat.** The harness reported `ok=false` because its
`interrupted_for_human_review` probe looked for `__interrupt__` inside
`get_state().values`; LangGraph records it as a pending write instead. The
checkpoint history contains exactly one `__interrupt__` write and every other check
passed, so this is a measurement bug in the throwaway script, not a workflow
failure. It was not "fixed" by re-running the investigation.

## 18. Regression results

| Check | Result |
| --- | --- |
| Backend `python -m pytest` | **538 passed, 5 skipped** (Phase 21.1 baseline 532 + 6 new timeout/config tests) |
| Backend `ruff check .` | All checks passed |
| Frontend `npm test` | **25 passed** (8 files; +3 report-state, +1 approval-gating) |
| Frontend `npm run typecheck` | Passed |
| Frontend `npm run lint` | Passed |
| Frontend `npm run api:check` | Generated types match the FastAPI schema |
| Frontend `npm run build` | Passed (10 routes) |

Live tests remain opt-in (`OPSPILOT_LIVE_LLM_TESTS`); the 5 skips are unchanged.
No dependency was added, no key appears in source, logs or Git history, and the
live run used synthetic data only.

## 19. Remaining limitations and risks

1. **Planner prompt does not enumerate the allow-list.** A real Gemini plan can
   select a valid-sounding but unknown tool, forcing the deterministic fallback
   even when the call succeeds (observed live). The fallback is safe and now
   observable, but prompt/schema alignment is the next reliability improvement.
2. **Planner degradation is not exposed by the investigation API**, so the UI
   cannot warn that fallback planning was used. Surfacing `planner_used` /
   `planner_error` on the investigation record would close this.
3. **`OPSPILOT_LLM_TIMEOUT` still requires explicit configuration** for slow
   planner responses; the 30 s default is unchanged by design.
4. **The approval-blocking UI relies on the persisted report envelope**, not the
   start/approval response fields; older records without `valid` are treated as
   valid, matching backend semantics.
5. **One live report is not statistical evidence.** It proves the path works for
   INC-011 with this model today, not report quality or reliability over time.
6. **The throwaway harness's interrupt probe is wrong** and should not be reused
   as-is for future acceptance criteria.

## 20. Recommended next phase

Align the planner prompt/schema by enumerating the actual tool IDs (or constraining
`selected_tools` to the allow-list in the schema), expose planner degradation on the
investigation read model so the UI can show it, and only then run another single
live investigation to confirm the planner succeeds without falling back. Keep the
current 2-request bound and opt-in live testing.
