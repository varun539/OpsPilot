# Phase 24 — Planner Intelligence & Evidence Quality Validation

## Repository
Local OpsPilot repository. Branch: Inspect current branch before starting.

## Phase 23 Verified Results

| Category | Result |
| --- | --- |
| Backend | 601 passed, 13 skipped |
| Frontend | 31 passed across 9 files |
| Ruff | passed |
| Typecheck | passed |
| Lint | passed |
| Offline evaluation CLI | exit 0 |
| Planner evaluation | 13/13 |
| Reliability | 17/17 |
| Negative-query dataset | 16 cases |
| Unauthorized tool execution | 0 |

## Real Gemini Model

- Provider/model designed: gemini-3.1-flash-lite
- Phase 21.2 generated one genuine report
- Phase 23 made zero live Gemini requests

## Phase 23 Findings (Updated)

1. **Planner prompt does not explicitly enumerate allowed tool IDs** — This was the root cause of the Phase 21.2 invalid tool selection. The `TOOL_DESCRIPTIONS` and `ALLOWED_TOOL_PROMPT` have been added to `backend/app/agent/planner.py` in this phase to address it.
2. Retrieval abstention candidate-presence clean rate remains 0.0 across 16 negative queries
3. Agent-level abstention accuracy was 1.0 on its 18-case fixture evaluation
4. Threshold experiments use lexical hashing and are not suitable for production calibration
5. Local real-embedding retrieval was not rerun because the pinned model was not cached
6. Human report-quality rubric exists, but no human ratings have been collected
7. npm run api:check could not execute on Windows because of a hardcoded .venv/bin/python path (fixed with dynamic interpreter resolution)
8. Three answerable held-out queries overlap with the development set (Finding 3)

## IMPORTANT SAFETY AND GIT RULES

- Preserve all existing work
- Do not reset, clean, stash or overwrite files
- Do not modify main directly
- No commits or pushes
- No live Gemini API requests by default
- No actual customer logs or sensitive data
- No paid APIs, grounding or external chargeable tools

## OBJECTIVE 1 — CROSS-PLATFORM OPENAPI VALIDATION

Inspect the OpenAPI generation and frontend api:check scripts.

Fix interpreter resolution so the check supports:

- Windows virtual environments
- Linux/macOS virtual environments
- OPSPILOT_PYTHON override
- The currently running Python interpreter where appropriate

Requirements:

- No hardcoded .venv/bin/python
- No hidden fallback that silently skips validation
- Deterministic schema generation
- Environment-specific Pydantic fields should be investigated and normalized only when appropriate
- Genuine API schema drift must still fail

Add tests for interpreter resolution and schema comparison.

Run the official npm run api:check on Windows.

## OBJECTIVE 2 — PLANNER TOOL SELECTION IMPROVEMENT

Inspect:

- Planner prompt
- Planner response schema
- Tool registry
- Allowlist validation
- Fallback behavior
- Argument validation

The previous live Gemini planner selected an unknown tool.

Improve planner context so it explicitly lists:

- Allowed tool IDs
- Tool descriptions
- Required arguments
- Argument types
- Tool-specific restrictions

Constrain structured outputs where the existing provider supports them.

Ensure the model cannot introduce arbitrary tool names.

Requirements:

- Unknown tool names rejected
- Invalid arguments rejected
- No direct execution of unauthorized tools
- Deterministic fallback retained
- Timeout and provider errors remain observable
- Error messages remain sanitized

Add adversarial planner tests:

- Unknown tool
- Correct tool with missing arguments
- Incorrect argument types
- Multiple conflicting tool selections
- Malformed structured output
- Empty planner response
- Refusal
- Timeout

Do not remove existing validation to improve the metrics.

## OBJECTIVE 3 — REAL EMBEDDING EVALUATION

Use the existing pinned model:

- sentence-transformers/all-MiniLM-L6-v2
- Pinned revision: 1110a243fdf4706b3f48f1d95db1a4f5529b4d41
- Use local_files_only where supported

Do not silently substitute lexical hashing or another embedding model.

If the model is not cached locally:

- Report that limitation
- Check available disk space
- Do not download it without checking that sufficient space exists

Re-evaluate:

- Semantic retrieval
- BM25
- Hybrid retrieval
- Recall@5
- Precision@5
- nDCG@5
- MRR
- Negative-query candidate presence
- Evidence relevance
- Document diversity

Clearly separate retrieval candidate presence from downstream agent abstention.

## OBJECTIVE 4 — HELD-OUT EVALUATION DATASET (UPDATED)

Create a separate held-out evaluation split.

**Actual dataset contents** (Finding 2):
- 19 total queries (previously reported as 18)
- 16 unanswerable queries (hq01–hq16)
- 3 answerable queries (hq16b, hq17, hq18) — **these overlap with retrieval_eval.json**

Requirements:

- Avoid leakage between training/development and evaluation queries
- Use independent labels (partial overlap exists — see Finding 3)
- Include answerable and unanswerable scenarios
- Include multiple incident categories
- Document labeling methodology
- Record dataset version and hashes
- Avoid manufacturing labels

Report metrics separately for:

- Development set
- Held-out answerable queries
- Held-out negative queries

Do not introduce a production abstention threshold from this experiment alone.

## OBJECTIVE 5 — HUMAN-RATED REPORT QUALITY

Use the existing:

- docs/phase23-report-quality-rubric.md

Evaluate the genuine Gemini report from Phase 21.2.

Prepare a scoring worksheet for two independent human raters.

Do not invent human scores or pretend the coding agent is an independent human evaluator.

If two real raters are unavailable:

- Prepare the worksheet
- Document the missing ratings
- Leave inter-rater agreement unreported

Measure:

- Factual grounding
- Citation usefulness
- Root-cause plausibility
- Evidence discrimination
- Actionability
- Appropriate abstention
- Technical accuracy
- Clarity
- Scope discipline

## OBJECTIVE 6 — LIVE PLANNER EVALUATION DESIGN

Inspect the existing opt-in live planner evaluation.

Default execution must make ZERO external API requests.

Keep the live evaluation disabled unless explicitly approved.

If later enabled:

- Gemini only
- Three synthetic scenarios
- Maximum five requests
- Sequential execution
- No retries
- No tool execution
- No report generation
- No parallel requests
- No secrets in logs

Do not execute live requests during this phase.

## OBJECTIVE 7 — REGRESSION

Run:

- python -m pytest -q
- ruff check .

Frontend:

- npm run test
- npm run typecheck
- npm run lint
- npm run api:check

Run the frontend production build if relevant files change.

Compare against Phase 23 baseline:

- 601 passed, 13 skipped
- Frontend 31 passed

Investigate regressions rather than weakening tests.

## OBJECTIVE 8 — DOCUMENTATION

Create:

- docs/phase24-planner-evidence-validation.md

Update:

- docs/phase23-reliability-and-intelligence.md

Include:

- Planner prompt changes
- Tool-selection benchmark results
- Retrieval results using the actual pinned embedding model
- Held-out evaluation methodology
- Human-rating status
- Cross-platform API check result
- Live API request count
- Remaining limitations

Clearly distinguish real model results from fixtures and mocked evaluations.

## FINAL REPORT

1. Phase 24 PASS / PARTIAL / BLOCKED
2. Cross-platform API validation
3. Planner changes and measured results
4. Tool allowlist verification
5. Real embedding evaluation
6. Held-out dataset metrics
7. Human-rating status
8. Live Gemini request count
9. Backend and frontend regression results
10. Exact files changed
11. Remaining limitations
12. Recommended Phase 25

Do not manufacture metrics or claim human evaluation without actual human ratings.