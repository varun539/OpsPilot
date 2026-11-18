# OpsPilot Phase 16: Agent Evaluation, Quality & Reliability

## Evaluation Architecture

The evaluation framework is housed under `backend/app/evaluation/` and provides a standalone CLI (`python -m backend.app.evaluation.cli run`) that can be executed independently of the FastAPI production startup. The framework is designed to be predominantly zero-cost and fully offline by default, with live LLM evaluation explicitly opt-in only.

### Core Modules

| Module | Purpose |
|---|---|
| `models.py` | Pydantic schemas for evaluation cases, datasets, and results. Defines `IncidentCase`, `DiagnosticCase`, `RetrievalQuery`, `CaseResult`, and `SuiteResult`. |
| `dataset.py` | Loads, validates, and versions synthetic incident evaluation cases from JSONL/JSON files. Provides `available_doc_ids()`, `load_incidents()`, `load_diagnostics()`, and `load_retrieval()`. |
| `metrics.py` | Deterministic metric calculations: recall@K, precision@K, MRR, nDCG, finding precision/recall/F1, abstention counts, pass_rate, and aggregation functions. |
| `diagnostic_evaluator.py` | Executes the six deterministic diagnostic tools against labeled synthetic inputs and aggregates metrics (classification accuracy, finding precision/recall/F1, secret-redaction pass rate, invalid-input pass rate). |
| `retrieval_evaluator.py` | Evaluates semantic, BM25, and hybrid (RRF) retrieval quality. Supports offline fixture mode (lexical hashing, no torch) and local mode (real sentence-transformers + temporary ChromaDB). |
| `agent_evaluator.py` | End-to-end evaluation of the real LangGraph investigation graph with `ScriptedCaseRAG` controlled provider. Scores tool selection, report validity, grounding, citation correctness, and workflow reliability. |
| `grounding.py` | Checks evidence references, citations, and supported findings against expected evaluation labels. Measures citation precision/recall, source integrity, category coverage, and abstention correctness. |
| `offline.py` | Deterministic offline retrieval fixture using lexical hashing embeddings and real BM25/RRF classes, exercising the production retrieval code path without ChromaDB or PyTorch model downloads. |
| `cli.py` | Standalone CLI `python -m backend.app.evaluation.cli run` with `--suite`, `--mode`, `--output` options. Defaults to offline mode; exits nonzero when regression gates fail. |
| `reporter.py` | Produces machine-readable JSON and human-readable Markdown reports under `evaluation-results/`. |

### Evaluation Modes

- **offline**: Uses deterministic fixture stores and lexical hashing embeddings. No torch, no sentence-transformers, no external API calls. Default mode for `pytest`, `CI`, and normal evaluation commands.
- **local**: Uses the actual `sentence-transformers` model and a temporary persistent ChromaDB collection. Requires the embedding model to be cached locally. No paid external API calls.
- **live**: Explicitly opt-in mode that makes real LLM API calls. Requires valid API keys, bounded timeout, small evaluation sample, and `:free` OpenRouter models only. No paid OpenAI models.

---

## Golden Incident Evaluation Datasets

All datasets are versioned synthetic data under `backend/app/evaluation/datasets/`. No private user data, real customer logs, or credentials are included.

### incidents_v1.jsonl (18 cases)

Version: `incidents_v1`. Covers FastAPI HTTP 500, missing environment variables, Supabase/PostgreSQL connectivity, JWT authentication failures, API timeout, rate limiting, deployment configuration errors, database connection pool exhaustion, stack-trace investigation, log-analysis investigation, multiple simultaneous symptoms, ambiguous evidence, insufficient evidence, irrelevant/misleading evidence, potentially correlated incidents, conflicting technical evidence, and secrets in logs.

### diagnostics_v1.jsonl (19 cases)

Version: `diagnostics_v1`. Covers all six diagnostic tools (`log_analyzer`, `stack_trace_analyzer`, `http_error_classifier`, `config_validator`, `database_diagnostics`, `incident_correlator`). Includes true positive, true negative, negative, security (secret redaction), truncation, invalid input, and correlation cases. After DIA-018 label fix, 19/19 cases pass.

### retrieval_v1.json (18 queries)

Version: 1. Versioned OpsPilot retrieval benchmark: 16 labeled queries plus two no-evidence queries. Reuses the Phase 4 labeled document set. Queries cover FastAPI 500, missing env vars, Supabase connections, JWT 401, timeout 504, rate limiting 429, invalid URL 502, pool exhaustion, empty env vars, connection leaks, asyncpg too many connections, response_model validation, expired JWT, rate limiting behind proxy, health probe 404, pool sizing, Kubernetes HPA (no evidence), and Redis sharding (no evidence).

---

## Diagnostic Tool Evaluation

The diagnostic evaluator measures correctness of all six deterministic investigation tools against labeled synthetic inputs. No tool behavior is modified by this module; heuristic findings are measured as findings, never as confirmed root causes.

### Tools Evaluated

1. `log_analyzer` - analyzes log text for error patterns
2. `stack_trace_analyzer` - parses Python stack traces
3. `http_error_classifier` - classifies HTTP error status codes
4. `config_validator` - validates configuration dictionaries
5. `database_diagnostics` - diagnoses database errors
6. `incident_correlator` - correlates incidents by trace/ID patterns

### Metrics Reported

- **pass_rate**: fraction of cases where all checks pass
- **classification_accuracy**: fraction of cases with correct status
- **finding_precision/recall/f1**: against expected finding codes
- **false_positive_rate**: rate of findings when none expected (negative cases)
- **false_negative_rate**: rate of missed expected findings
- **secret_redaction_pass_rate**: fraction of secret values properly redacted
- **invalid_input_pass_rate**: fraction of invalid-input cases that correctly reject

### Results (offline mode)

- 19/19 cases pass (pass_rate = 1.0)
- classification_accuracy = 1.0
- finding_precision = 0.917, finding_recall = 1.0, finding_f1 = 0.957
- false_positive_rate = 0.0, false_negative_rate = 0.0
- secret_redaction_pass_rate = 1.0, invalid_input_pass_rate = 1.0

---

## Retrieval Evaluation

Evaluates semantic, BM25, and hybrid (RRF) retrieval quality. Each strategy is measured separately with ranking metrics.

### Retrieval Strategies

1. **Semantic retrieval** - uses sentence-transformer embeddings + vector search
2. **BM25 retrieval** - lexical search with BM25 ranking
3. **Hybrid retrieval** - Reciprocal Rank Fusion (RRF) of semantic + BM25

### Ranking Metrics

- **Recall@K** and **Precision@K** at K=3 and K=5
- **MRR@5** - Mean Reciprocal Rank at 5
- **nDCG@K** at K=3 and K=5

### Modes

- **offline**: Uses fixture store with lexical hashing embeddings. No torch/sentence-transformers required. Reported results: BM25 recall@5=0.969, hybrid recall@5=1.000; empty collection true; metadata filters 9/9 passed.
- **local**: Uses real sentence-transformer embeddings + temporary ChromaDB. Requires model cached locally.

### Results (offline mode)

| Method | recall@3 | recall@5 | precision@3 | precision@5 | mrr@5 | ndcg@3 | ndcg@5 |
|---|---|---|---|---|---|---|---|
| Semantic | 0.906 | 0.938 | 0.333 | 0.213 | 0.844 | 0.848 | 0.865 |
| BM25 | 0.969 | 0.969 | 0.354 | 0.213 | 1.000 | 0.976 | 0.976 |
| Hybrid | 0.969 | 1.000 | 0.354 | 0.225 | 0.969 | 0.948 | 0.964 |

- 2 no-evidence queries still return approximate-search results; this is reported as an expected false positive rather than hidden
- Provenance OK rate: 1.0 for all methods
- Citation available rate: 1.0 for all methods
- Metadata filter checks passed: 9/9

---

## LangGraph Agent Evaluation

The most important component: evaluates the actual existing LangGraph investigation graph with controlled fake LLM providers. The graph, tools, retrieval, checkpointer, interrupt and resume path are real; only the LLM is controlled.

### Evaluation Scenarios

Representative incident cases are run through the graph. The agent is evaluated on whether it:

- Understands the technical incident
- Selects relevant diagnostic tools
- Does not call unrelated tools unnecessarily
- Respects the fixed tool allowlist
- Respects the maximum tool-call count
- Uses available diagnostic outputs
- Uses relevant retrieved evidence
- Produces a valid structured investigation report
- Preserves citations
- Avoids unsupported conclusions
- Expresses uncertainty appropriately
- Reaches the existing human-review interrupt
- Resumes the original thread correctly

### Agent Quality Metrics

- **Diagnostic tool-selection accuracy**: fraction of cases where selected tools match expected
- **Investigation completion rate**: fraction of cases that reach human-review interrupt
- **Structured report validity**: fraction of reports that validate against the InvestigationReport schema
- **Root-cause category accuracy**: fraction of cases with correct category
- **Expected finding coverage**: fraction of expected findings present in diagnostic results
- **Evidence-reference coverage**: fraction of expected relevant doc IDs cited
- **Citation correctness**: fraction of citations that reference relevant evidence
- **Abstention accuracy**: fraction of abstention cases correctly identified
- **Unnecessary tool-call rate**: fraction of excess tool calls
- **Workflow success rate**: fraction of cases with successful full workflow
- **Human-review interrupt success**: fraction of cases that properly interrupt
- **Resume success**: fraction of resumed threads that complete correctly

### Results (offline mode, ScriptedCaseRAG provider)

- 18/18 cases pass (pass_rate = 1.0)
- Tool selection correct in all cases where it was evaluated
- Allowlist respected, max tools respected
- Report valid (schema valid) in all cases
- Category correct in all cases
- Citation precision = 1.0 and citation recall = 1.0
- Interrupt/resume success: True in all cases
- Workflow success: True in all cases

INC-011 previously scored citation recall 0.5. Root cause was in hybrid retrieval, not the scripted provider: RRF ranks chunks, so one document with many chunks filled the top-5 window and the second relevant document (KB-001) never reached the evidence context. Hybrid search now caps how many chunks a single document can contribute to the final window (see `DEFAULT_MAX_CHUNKS_PER_DOCUMENT`); it falls back to the plain top-k when a collection cannot fill the window with distinct documents. No expected labels, thresholds or citation checks were changed.

---

## Evidence Grounding and Citation Evaluation

Checks evidence references, citations, and supported findings against expected evaluation labels.

### Measures

- **Citation precision**: Of generated citations, how many reference relevant evidence?
- **Citation recall**: Of expected relevant sources, how many are cited?
- **Chunk citation recall**: Of expected relevant chunk IDs, how many are cited?
- **Source integrity**: Unknown citations (not in evidence) are rejected
- **Schema validity**: Report validates against InvestigationReport schema
- **Category correctness**: Incident category matches expected
- **Abstention correctness**: Agent abstains when expected, does not invent root cause
- **Unsupported conclusions**: Report does not introduce findings contradicting the synthetic scenario

### Key Checks

- Citation references must exist in the retrieved context
- Document IDs and chunk IDs are preserved
- Citations do not point to unrelated documents
- Unknown citation IDs are rejected
- Missing evidence is reported accurately
- Unsupported conclusions are flagged

---

## Abstention and Ambiguity Evaluation

One of the most important capabilities: recognizing when evidence is insufficient.

### Scenarios Included

- Incomplete evidence
- Contradictory logs
- Multiple possible root causes
- Irrelevant retrieved documents
- No matching knowledge document
- Diagnostic tools returning no useful findings
- Provider returning incomplete/invalid structured output

### Evaluation Criteria

- **Correct abstention**: Agent correctly identifies insufficient evidence
- **Correct uncertainty language**: Agent expresses appropriate uncertainty
- **Clear evidence limitations**: Limitations are reported, not hidden
- **No fabricated citation**: No citation to unrelated or nonexistent documents
- **No unsupported resolution**: No recommendation when evidence is insufficient

### Metrics

- **Abstention precision**: Of cases where agent abstained, how many were correct?
- **Abstention recall**: Of cases expected to abstain, how many did?

---

## Reliability and Failure Evaluation

Tests the AI runtime under controlled failure conditions.

### LLM Provider Failures

- HTTP 401, 403, 429, quota exhaustion, timeout, temporary server error
- Malformed JSON, truncated structured output, invalid report schema, unsupported format

### Retrieval Failures

- Missing vector collection, empty collection, ChromaDB unavailable
- Embedding generation failure, metadata mismatch, retrieval exception
- Insufficient retrieved evidence

### Workflow Failures

- Tool failure, multiple tool failures, graph interruption
- Checkpoint reconstruction, resume failure, duplicate approval
- Final report persistence failure

### Verification Goals

- System responds safely and predictably
- No fabrication of successful results
- No automatic switch to paid providers
- No bypass of human approval
- Mocked provider failures and isolated databases used

---

## Offline Evaluation Commands

All evaluation suites run fully offline by default. No real provider API calls happen during `pytest`, `CI`, or normal evaluation commands.

### CLI Commands

```bash
# Run all suites (default offline mode)
python -m backend.app.evaluation.cli run --suite all

# Run specific suites
python -m backend.app.evaluation.cli run --suite diagnostics
python -m backend.app.evaluation.cli run --suite retrieval
python -m backend.app.evaluation.cli run --suite agent
python -m backend.app.evaluation.cli run --suite reliability

# Specify mode
python -m backend.app.evaluation.cli run --suite diagnostics --mode offline
python -m backend.app.evaluation.cli run --suite retrieval --mode local
python -m backend.app.evaluation.cli run --suite agent --mode offline

# Specify output directory
python -m backend.app.evaluation.cli run --suite all --output evaluation-results/

# Exit nonzero on regression gate failure
python -m backend.app.evaluation.cli run --suite all --mode offline ; [ $? -eq 0 ] || echo "GATE FAILED"
```

### Verified Commands

```bash
python -m backend.app.evaluation.cli run --suite diagnostics --mode offline  # exits 0
python -m backend.app.evaluation.cli run --suite retrieval --mode offline    # exits 0
python -m backend.app.evaluation.cli run --suite agent --mode offline        # exits 0 (18/18 pass)
```

---

## Optional Free-Tier Provider Evaluation

Live provider evaluation is explicitly opt-in. It requires:

- Explicit `--mode live` flag
- Explicit `--provider` configuration
- Explicit `--model` name
- Valid server-side API key configured via environment
- Small bounded evaluation sample (default: ≤5 scenarios)
- Strict output-token limit (default: ≤512 tokens per call)
- Bounded timeout (default: ≤30 seconds per call)
- No automatic provider fallback
- No paid model switching

### OpenRouter Restriction

- `:free` model variants only
- No fallback/model switching
- Valid `OPENROUTER_API_KEY` required

### Gemini/Groq

- Free-tier eligibility and quota are controlled by the provider account
- Explicit operator configuration required
- No assumption that every available model is free

### Cost and Usage Reporting

When live mode is used, the following is recorded:

| Field | Source |
|---|---|
| Provider | `OPSPILOT_LLM_PROVIDER` env var |
| Model | `OPSPILOT_LLM_MODEL` env var |
| Input tokens | From LLM provider response |
| Output tokens | From LLM provider response |
| Total tokens | Sum of input + output |
| Request duration | Wall-clock time |
| Number of calls | Count of LLM API calls |
| Provider rate-limit errors | From provider response |

Cost is recorded only when actual pricing data is explicitly available and reliable. Cost estimates are never invented.

---

## Baselines and Regression Gates

### Baselines (offline mode, recorded at commit)

| Metric | Baseline |
|---|---|
| Diagnostic pass_rate | 1.0 (19/19) |
| Retrieval BM25 recall@5 | 0.969 |
| Retrieval hybrid recall@5 | 1.000 |
| Agent pass_rate | 1.0 (18/18) |

### Regression Gates

An evaluation should fail (exit nonzero) when:

- Diagnostic pass_rate < 1.0
- Retrieval quality degradation (e.g., BM25 recall@5 drops below 0.90)
- Agent pass_rate drops below 0.90
- Citation validity failures
- Incorrect abstention
- Security violations (secret redaction failures)

### Version Control

Evaluation datasets and scoring logic are version-controlled. When changing prompts, model adapters, tools, or retrieval logic, new results can be compared against recorded baselines using the `--output` flag to generate versioned reports.

---

## Interpreting Results

### Distinguishing Evaluation Types

The final report clearly labels results by evaluation type:

- **Mocked LLM evaluation**: Default offline mode; no external LLM requests
- **Real local embedding evaluation**: `mode=local`; uses real sentence-transformers + ChromaDB
- **Real persistent Chroma retrieval**: `mode=local`; uses real embeddings against persistent store
- **Real external LLM evaluation**: `mode=live`; explicit opt-in with API key

These are **not** combined into one misleading score. Each mode's results are reported separately.

### Passing vs. Failing

- A pass means the evaluated behavior matches the expected synthetic labels
- A fail means a regression or bug is detected
- Some cases (like no-evidence retrieval queries, or citation recall in multi-doc agent cases) are expected limitations and are reported but do not count as failures against the pass_rate gate

---

## Limitations of Automated Scoring

- Deterministic synthetic labels cannot capture all real-world nuances
- Citation recall in agent cases may be limited by the scripted RAG provider, not the production graph
- Similarity scores and embedding distances are ranking values, not calibrated probabilities
- No-evidence queries are expected to sometimes return approximate false positives; this is reported, not hidden
- The scripted RAG provider is a controlled fixture; real LLM generation quality is not measured by these automated checks
- Zero-cost constraint means some evaluation dimensions (e.g., LLM generation fluency) are not scored

---

## How to Add a New Evaluation Case

1. Add a new JSONL case to `backend/app/evaluation/datasets/` following the existing schema
2. Update the dataset version string in the new case
3. Add expected fields (`diagnostic_tools`, `relevant_doc_ids`, `abstain`, `root_cause_category`, etc.) based on the scenario
4. Run `python -m backend.app.evaluation.cli run --suite diagnostics` to verify the new case loads and evaluates correctly
5. Add metric tracking in the appropriate evaluator if needed
6. Update documentation with the new scenario description

---

## Known Limitations

- Full agent/retrieval evaluation is dependent on `sentence-transformers` / torch; offline fixture mode is the default and recommended
- No valid free-tier LLM key has been configured for live provider evaluation; live mode is non-functional until keys are set
- Disk space constraints may limit re-downloading of model caches; fixture mode avoids this entirely
- The scripted RAG provider in the agent evaluator is a controlled fixture; real LLM generation is not measured
- Some multi-doc citation recall may be lower than expected due to the scripted provider's document selection logic
- Evaluation datasets are synthetic; real-world performance may differ