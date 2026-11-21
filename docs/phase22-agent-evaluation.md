# Phase 22 — Agent Quality, Evaluation & Reliability Benchmark

**Date:** 2026-11-19 · **Branch:** `release/opspilot-showcase` · **HEAD:** `0eb9a41b5c5fb27db5eaaafba6f2142c5ca58812`

**Status: PASS.** The existing evaluation framework was extended, not duplicated, with
planner, report-validation and reliability suites, new retrieval diversity metrics and
a durable interrupt regression test. **Zero external LLM requests** were made. Every
number below was measured in this working tree. Results labelled **offline** use
deterministic fixtures; the one table labelled **local** uses the already-cached pinned
embedding model and makes no network requests. Nothing was estimated.

## 1. Evaluation methodology

The framework under `backend/app/evaluation/` already provided diagnostics, retrieval,
agent and a reliability stub. Phase 22 extended it:

| Component | Phase 22 change |
| --- | --- |
| `datasets/planner_v1.jsonl` | 13 scripted planner behaviors with explicit control-flow expectations |
| `datasets/reports_v1.jsonl` | 9 candidate reports covering schema, citation, abstention and label failures |
| `planner_evaluator.py` | Runs each scripted plan through the real graph; measures the allow-list boundary, fallback observability and determinism |
| `report_evaluator.py` | Separates schema validity, citation validity, evidence support and recommendation grounding |
| `reliability_evaluator.py` | Replaces the stub with 8 real orchestration and persistence failure scenarios |
| `retrieval_evaluator.py` | Adds document coverage, distinct-document count, chunk concentration and an explicit abstention group |
| `cli.py` | Adds `--suite planner` and `--suite report`; `reliability` now executes real scenarios; the generated artifact carries the full per-method retrieval table instead of only recall |
| `tests/test_interrupt_checkpoint.py` | Durable regression for the LangGraph interrupt representation |

The graph, tools, allow-list, retrieval, checkpointer, interrupt, approval and resume
paths are real in the planner, agent and reliability suites. Only the LLM provider is
scripted. Live Gemini evaluation remains opt-in and was **not** executed.

Reproduction:

```bash
python -m backend.app.evaluation.cli run --suite all --mode offline --output /tmp/phase22-eval-offline
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  python -m backend.app.evaluation.cli run --suite retrieval --mode local --output /tmp/phase22-eval-local
```

Generated reports stay under `/tmp`; no evaluation artifacts were added to the repo.

**Measured vs qualitative:** tables and rates are measured. Statements about *why* a
metric behaves a certain way (for example, the hybrid diversity cap) are attributed to
the implementation and labelled where they are observations rather than scores.

## 2. Dataset and scenario coverage

| Dataset | Cases | Coverage |
| --- | --- | --- |
| `incidents_v1.jsonl` | 18 | Pool exhaustion (INC-008/011/015), HTTP 5xx (INC-001/017/018), configuration (INC-002/010), authentication (INC-004), deployment (INC-007), latency/timeout (INC-005), rate limiting (INC-006), DB connectivity (INC-003), application exception (INC-009), 4 abstention cases (INC-012/013/014/016), 2 adversarial (INC-017/018) |
| `planner_v1.jsonl` | 13 | Valid plans (3), unknown tools (2), too many tools, duplicates, missing argument, wrong argument type, empty output, malformed output, timeout, refusal |
| `reports_v1.jsonl` | 9 | Grounded, invented citation, schema invalid, insufficient evidence, insufficient+hypothesis, label mismatch, missing evidence, truncated JSON, uncited recommendation |
| `retrieval_v1.json` | 18 | 16 labeled queries + 2 no-evidence queries across 8 knowledge-base documents |
| `diagnostics_v1.jsonl` | 19 | All six deterministic tools, plus negative, security, truncation and invalid-input cases |
| Reliability scenarios | 8 | Success, planner timeout, report 503 pause, approval after failure, malformed, empty, refusal, restart recovery |

All eight requested incident categories are represented: pool exhaustion, HTTP 5xx,
authentication, deployment regression, latency degradation, resource exhaustion,
network failure and no-evidence. The labels are fixture expectations written for these
synthetic cases; they are not production ground truth and the sample is not
representative of real incidents.

**Step 4 coverage map** (each behavior is measured somewhere in the suite):

| Required behavior | Where measured | Result |
| --- | --- | --- |
| Correct report schema | Report `schema_valid_rate`; RPT-001/004/006/009 | 6/9 valid by design (3 invalid fixtures) |
| Evidence-backed hypotheses | Report citation checks; RPT-001 vs RPT-002 | Grounded citations valid; invented citation rejected |
| Evidence-backed recommendations | `recommendation_grounding_rate`; RPT-001 vs RPT-009 | 0.5 (one cited action, one uncited) |
| Citation precision / validity | Report citation metrics; agent citation precision/recall | 1.0 / 1.0 on grounded rows; 1.0 / 1.0 E2E |
| Invented citation rejection | RPT-002 | Rejected as `citation_failure` |
| Missing evidence | RPT-007 (cites IDs with no supplied evidence) | Rejected as `citation_failure` |
| Contradictory evidence | INC-016 (agent suite abstention) | Abstained correctly; semantic contradiction not machine-scored |
| Insufficient evidence | RPT-004 vs RPT-005 | Clean abstention accepted; abstention+hypothesis rejected |
| Empty output | REL-006 + Phase 21.1 tests | Blocked, no report |
| Malformed JSON | REL-005 + RPT-008 | Blocked / schema failure |
| Truncated output | RPT-008 + Phase 21.1 truncation tests | Schema failure, not a success |
| Provider refusal | REL-007 + Phase 21.1 tests | Blocked, no report |

## 3. Retrieval metrics

Measured with `retrieval_v1.json` (18 queries: 16 labeled, 2 no-evidence).
Precision@5 divides by K, so a query with two strong hits scores 0.4, not 1.0.

**Local mode — real pinned embeddings + temporary ChromaDB:**

| Method | Recall@5 | Precision@5 | nDCG@5 | MRR | Doc coverage@5 | Distinct docs@5 | Max chunks/doc | Chunk concentration |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Semantic | 0.9688 | 0.2125 | 0.9708 | 1.000 | 0.9688 | 1.556 | 4.444 | 0.889 |
| BM25 | 0.9688 | 0.2125 | 0.9758 | 1.000 | 0.9688 | 1.722 | 4.056 | 0.811 |
| Hybrid | **1.0000** | 0.2250 | **0.9950** | 1.000 | **1.0000** | 3.167 | 2.167 | 0.433 |

The diversity columns are first measured here. Semantic and BM25 concentrate results
in 1.6–1.7 distinct documents, with up to 4.4 chunks from one document; hybrid returns
3.2 distinct documents and cuts the largest single-document share to 0.43. That
quantifies the existing Phase 18 diversity cap instead of merely asserting it.

**Offline fixture mode** (lexical hashing embeddings, same queries): hybrid recall@5
1.000, nDCG@5 0.9642, distinct docs 3.611, concentration 0.389. Offline semantic
recall is 0.9375; its `q10` miss is the lexical-fixture artifact documented in
Phase 19, not a production retrieval result.

**Abstention group (kept separate):** both no-evidence queries returned non-empty
results under all three methods in both modes (`abstention_clean_rate = 0.0`, 2/2
returned results). This is reported as the known approximate-search false-positive
behavior, not folded into recall. No similarity threshold was introduced on two
samples.

**Pass rate:** 48/54 local and 47/54 offline. The 6 local failures are exactly the two
no-evidence queries × three methods; the seventh offline failure is `q10:semantic`.
The CLI therefore emits an overall gate warning in `--mode offline`; that behavior is
pre-existing and reflects the strict no-evidence rule.

Every column above is written to the generated `evaluation.json`
(`summary.retrieval.methods`) and rendered as tables in `evaluation.md`, so the
precision, nDCG, MRR, diversity and abstention figures are reproducible from the
artifact rather than only from an in-process call.

Known artifact of the metric definition: precision@5 divides by K=5, so a query with
two labeled relevant documents scores at most 0.4. The low precision values are a
denominator effect, not poor ranking; nDCG@5 and MRR are the ranking-sensitive
comparisons here.

## 4. Planner metrics

Every scenario runs the real `plan_investigation` node, allow-list filter, tool
execution and human-review interrupt. Provider responses are scripted fixtures, so
these results measure **control-flow correctness**, not live model tool choice.

| Metric | Measured |
| --- | --- |
| Scenarios | 13 |
| Valid-selection cases | 3 / 3 correct (1.000) |
| Invalid-selection cases | 10 |
| Invalid-selection rejection rate | 10 / 10 (1.000) |
| Fallback activation rate | 10 / 13 (0.769) |
| Fallback observability rate | 1.000 |
| Determinism rate | 1.000 |
| Interrupt rate | 1.000 |
| Unauthorized tool requests | 2 |
| Unauthorized tools in graph state | **0** |
| Unauthorized tools executed | **0** |
| Planner provider calls | 13 (one per scenario, no retries) |

The requests for `shell_executor` and `deploy_fixer` were rejected by allow-list
validation; the deterministic bounded fallback ran and the rejection was recorded.
Missing rationale, wrong argument type, empty output, malformed output, timeout and
refusal all fell back with a sanitized `planner_error` and never reached execution.
The fallback observability check requires both a non-empty `planner_error` and a
deterministic-fallback warning in state.

The Phase 21.2 live observation (Gemini selected an unknown tool, which raised
`ValueError: unknown tool selected`) is the reason this suite exists; the fixture
reproduces that control path without calling a provider.

## 5. Report validation metrics

| Metric | Measured |
| --- | --- |
| Cases | 9 |
| Passed / outcome accuracy | 9/9 (1.000) |
| Schema-valid rate | 6/9 (0.667) |
| Evidence-supported rate | 9/9 (1.000) |
| Malformed payloads | 1 |
| Recommendation grounding rate | 0.5 |

Outcome distribution: `valid_grounded` 2, `citation_failure` 2, `schema_failure` 3,
`insufficient_evidence` 1, `label_mismatch` 1.

The low schema rate is **by design**: three candidate payloads are deliberately
invalid to prove rejection. The important separation is outcome accuracy 9/9, which
includes `RPT-006` — a schema-valid, citation-valid report whose category is wrong,
classified as `label_mismatch`. Schema validity is not factual correctness.
`RPT-005` is rejected because an `insufficient` status cannot carry a root-cause
hypothesis. `RPT-008` (truncated JSON) is a schema failure, not an empty success.

Evidence-backed hypotheses are enforced through citation validation on
`supporting_chunk_ids`; the invented ID in `RPT-002` fails. Recommendation grounding is
reported separately because `RPT-009` is still a valid grounded report even though its
recommended action is uncited — folding that into "valid" would hide the gap.

## 6. Citation metrics

| Metric | Measured |
| --- | --- |
| Citation-valid rate (report suite) | 4/9 (0.444) |
| Citation precision (grounded rows) | 1.000 |
| Citation recall (grounded rows) | 1.000 |
| Invented citations rejected | 1 (`KB-999::invented::token-000`) |
| Real IDs against empty evidence | 1 rejected (`RPT-007`) |
| Unknown citation entries | 2 |
| E2E citation precision / recall | 1.000 / 1.000 |
| E2E citation/source integrity rate | 1.000 |

Overall citation validity is low for the same deliberate reason as schema validity:
two fixtures are citation failures and three cannot be schema-validated. On the rows
where citation scoring is meaningful, every cited chunk exists in the supplied
evidence and every expected document is cited. An abstention report with no citations
(`RPT-004`) is correctly not treated as a citation failure.

## 7. End-to-end success rate

`evaluate_agent()` runs all 18 cases through the real LangGraph graph, retrieval,
tools, checkpointer, interrupt, approval and resume. The report provider is a scripted
case RAG, so report *content* is synthetic by construction.

| Metric | Measured |
| --- | --- |
| Cases / passed | 18 / 18 |
| Success rate | 1.000 |
| Tool-selection accuracy | 1.000 |
| Workflow completion rate | 1.000 |
| Valid-report rate | 1.000 |
| Root-cause category accuracy | 1.000 |
| Finding coverage | 1.000 |
| Citation-valid / integrity rate | 1.000 |
| Citation precision / recall | 1.000 / 1.000 |
| Abstention accuracy | 1.000 (4 TP, 14 TN, 0 FP, 0 FN) |
| Interrupt / resume success | 1.000 / 1.000 |
| Unnecessary tool-call rate | 0.000 |
| Allow-list violations | 0 |
| Security violations | 0 |
| Adversarial cases | 2 |

These results show workflow, validation and grounding conformance to fixture labels.
They do **not** show that a live LLM writes good reports: report content is
case-labelled and tool selection comes from the deterministic fallback because no LLM
is used.

## 8. Failure handling results

| Metric | Measured |
| --- | --- |
| Scenarios / passed | 8 / 8 |
| Correct failure-classification rate | 1.000 |
| Failed-report scenarios | 6 |
| Failed reports completed | **0** |
| Failed-report blocking rate | 1.000 |
| Successful-investigation rate | 1.000 |
| Evidence retention rate | 1.000 |
| Planner-degradation observability rate | 1.000 |
| Recovery failure blocked | true |

The six failure scenarios cover report HTTP 503 (pause and approval-after-failure),
malformed output, empty output, refusal and restart-then-approve. In every case the
report was persisted with `generation_status=provider_error`, `valid=false`, the
investigation ended `failed` (or remained paused where no approval was given),
`last_error` was sanitized, the human decision was preserved and evidence was
retained. A planner timeout did not prevent a valid report and completed successfully
with the fallback recorded.

## 9. Human approval and persistence results

`tests/test_interrupt_checkpoint.py` replaces the incorrect Phase 21.2 probe:

- `agent.investigate(...)` returns `__interrupt__`; the checkpoint contains exactly
  one `__interrupt__` pending write whose payload lists `approval_choices` and the
  investigation ID. The assertion reads the pending write, **not**
  `get_state().values`.
- Before resume the workflow is not `completed` and no approval decision is recorded.
- Resume runs on the same thread and completes with `execution_status=completed`.
- A second approval after completion raises `InvalidStateTransitionError`; the
  investigation remains `completed` with exactly one investigation and one approval.
- Existing durable tests for restart recovery, persistence failure and concurrent
  approval remain unchanged and pass.

## 10. Comparison against previous baselines

| Phase 19 baseline | Phase 22 local | Delta |
| --- | --- | --- |
| Semantic recall@5 0.969 | 0.9688 | 0.000 (0.96875 rounds to 0.969) |
| BM25 recall@5 0.969 | 0.9688 | 0.000 (0.96875 rounds to 0.969) |
| Hybrid recall@5 1.000 | 1.0000 | 0.000 |
| Hybrid nDCG@5 0.995 | 0.9950 | 0.000 |

No retrieval code changed in Phase 22, so identical values are expected rather than
re-labelled. The new diversity and abstention columns add information that the Phase 19
baseline did not report.

| Regression | Previous | Phase 22 |
| --- | --- | --- |
| Backend tests | 538 passed, 5 skipped | **554 passed, 5 skipped** |
| Backend lint | clean | clean |
| Frontend tests | 25 passed across 8 files | 25 passed across 8 files |
| Frontend typecheck / lint / api:check / build | clean | clean (all four re-run) |
| Live Gemini requests | 2 in Phase 21.2 | **0** |

The 16 new tests are 4 planner, 4 report, 4 reliability, 2 interrupt and 2 CLI wiring
tests. No existing test was removed, skipped or weakened.

The frontend files in the working tree are the uncommitted Phase 21.2 changes, not
Phase 22 edits, but they were re-verified rather than assumed: `vitest run` (25/25),
`tsc --noEmit`, `eslint .`, `api:check` and `next build` all exit 0.

## 11. Known limitations

1. **Scripted providers.** Planner, report and reliability metrics measure control flow
   and validation, not live model quality. Phase 21.2 remains the only live evidence,
   and it is a single INC-011 sample.
2. **Fixture labels.** All "ground truth" is synthetic labels created for these cases.
   The agent suite's 1.000 scores describe conformance to those labels, not accuracy on
   real incidents.
3. **Semantic contradiction is not scored.** INC-016 exercises contradictory evidence
   and the agent suite abstains correctly, but the report evaluator cannot judge
   whether prose over-claims across conflicting citations.
4. **No-evidence abstention is unfiltered.** Both negative retrieval queries return
   results under every method; no threshold was introduced on two samples.
5. **Small corpus.** Eight documents and 18 queries limit generalization, and offline
   lexical-hashing embeddings produce artifacts (`q10`).
6. **Recommendation grounding is reported separately.** One uncited action remains in
   an otherwise valid report so the gap is visible instead of being hidden in a
   combined score.
7. **Planner labels describe expected control flow,** not optimal tool choice; a valid
   fallback plan can differ from the labeled tool set and still be reasonable.
8. **E2E success is workflow success.** Report text is case-labelled and the planner
   path is deterministic fallback, so natural-language quality is not measured.
9. **CLI overall gate warns offline** because the two no-evidence queries count as
   retrieval failures. That is a documented strictness choice, not a regression.
10. **Local mode is environment-dependent.** It needs the pinned model in the local
    cache and a working `chromadb` Rust extension. A venv whose `chromadb` wheel was
    installed without its compiled `.so` fails at client construction; the suite raises
    rather than silently falling back, which is the intended behavior.

## 12. Recommendations

1. Add an **opt-in live planner evaluation** (one request per scenario, bounded sample)
   to measure real tool selection while preserving the existing request bounds.
2. Introduce a **human-rated report-quality rubric** for a small sample instead of
   inferring semantic correctness from citations alone.
3. Run a **similarity-threshold experiment** for no-evidence queries with more negative
   examples before changing default retrieval behavior.
4. Expose `planner_used` / `planner_error` on the investigation read model so the
   existing frontend states can surface planner degradation.
5. Add **simultaneous retrieval failure** and **non-LLM graph exceptions** to the
   reliability suite; they are unit-covered but not yet scored end to end.
6. Keep the planner, report and reliability suites in CI as deterministic regression
   gates; each runs in seconds.

## 13. Phase 23 addendum (2026-11-20)

Phase 23 preserved every measurement above and changed how the evaluator reports
them. The historical numbers are left in place; this addendum records what
supersedes them.

**Limitation #9 is resolved.** A no-evidence query returning candidates is no
longer counted as a retrieval failure. Retrieval now emits three independent
gates — `ranking`, `integrity` and `abstention` — and the CLI exits 0 when only
the advisory abstention gate fails. `--require-abstention` restores blocking
behaviour for teams that want it. Ranking metrics for no-evidence rows are
`None`, so they cannot contaminate Recall@K or nDCG.

**Abstention is split into four stages.** The 0.0 retrieval clean rate and 1.0
end-to-end abstention accuracy are both valid because they measure candidate
presence and the agent/report decision respectively. The stages, owners and
denominators are now named in the abstention suite and pinned by
`tests/test_abstention_evaluation.py`.

**The no-evidence sample grew from 2 to 16 queries** in
`datasets/retrieval_negatives_v1.json`, covering six categories with auditable
labels and a recorded methodology. At the candidate-presence operating point all
16 still return candidates under semantic, BM25 and hybrid retrieval, so every
method reports false-answer rate 1.0 and abstention accuracy 0.5294. The
similarity-threshold sweep is marked non-interpretable in offline mode; no
production threshold is recommended.

**Reliability coverage grew from 8 to 17 scenarios**, including retrieval
exceptions, diagnostic-tool exceptions, invalid checkpoint state, persistence
failure, malformed input, serialization failure, duplicate approval and an
unexpected graph-node exception. All 17 pass with zero false completions.

**Planner degradation is now exposed** through `planner_used`,
`planner_status` and a redacted `planner_error` on the investigation read model,
with a frontend warning and additive SQLite migration for historical rows.

See `docs/phase23-reliability-and-intelligence.md` for measured Phase 23 results
and `docs/phase23-report-quality-rubric.md` for the still-unpopulated human
quality protocol.
