# RAG Setup

OpsPilot's RAG pipeline combines hybrid retrieval with one explicitly selected
zero-cost LLM provider (Gemini, Groq or OpenRouter).

## Architecture

```text
Incident question
  -> HybridSearchEngine
  -> ContextBuilder
  -> create_llm_provider()
  -> InvestigationReport
  -> CitationValidator
  -> RAGResponse
```

## Installation

```bash
python -m pip install -r requirements.txt
```

The OpenAI SDK is pinned to `openai==3.51.0` for the Groq/OpenRouter
Chat Completions adapters and the retained OpenAI test adapter.

## Configuration

Set these server-side environment variables:

```bash
export OPSPILOT_LLM_PROVIDER="gemini"       # gemini | groq | openrouter
export OPSPILOT_LLM_MODEL="gemini-3.5-flash" # free-tier model for your account
export GEMINI_API_KEY="..."
export OPSPILOT_LLM_TIMEOUT="30"
export OPSPILOT_LLM_MAX_OUTPUT_TOKENS="1200"
```

`OPSPILOT_LLM_MODEL` has no default: choose a model your account offers on a
zero-cost quota. For Groq use `GROQ_API_KEY`; for OpenRouter use
`OPENROUTER_API_KEY` and a model ID ending in `:free`. `OPSPILOT_LLM_API_KEY`
is a provider-neutral alternative to the three key variables.
`OPENAI_API_KEY` is ignored and OpenAI is not selectable in the production
factory.

## Running an investigation

```python
from backend.app.llm.config import LLMConfig
from backend.app.llm.factory import create_llm_provider
from backend.app.rag.generator import RAGService
from backend.app.retrieval.hybrid_search import HybridSearchEngine

engine = HybridSearchEngine()
config = LLMConfig.from_env()
provider = create_llm_provider(config)
service = RAGService(retrieval_engine=engine, llm_provider=provider)
response = service.investigate("API returns 500 after deployment")
```

## Report schema

`InvestigationReport` contains:

- `summary`
- `incident_category`
- `affected_components`
- `evidence_status` (`sufficient` or `insufficient`)
- `root_cause_hypotheses`
- `supporting_evidence`
- `recommended_actions`
- `missing_information`
- `limitations`

Evidence strength and risk levels are qualitative labels, not calibrated probabilities.

## Citation validation

Every chunk ID cited by the model is checked against the chunks supplied in the current retrieved context. Unknown or cross-query citations cause validation failure.

## Insufficient evidence

If no evidence is retrieved, OpsPilot returns an insufficient-evidence report without calling the LLM. Retrieved nearest neighbors are not treated as proof that relevant knowledge exists.

## Testing without an API key

All standard tests run offline using fake providers and mocked HTTP/SDK
clients. No live API requests are made during the test suite. A real smoke
test exists only behind `OPSPILOT_LIVE_LLM_TESTS=1` and a configured free-tier
key.

## Security and cost

- API keys are never logged or committed, and `OPENAI_API_KEY` is not read.
- OpenRouter model IDs must end in `:free`; there is no paid fallback.
- Retrieved documents and user input are treated as untrusted data.
- Recommended actions require human review and are not executed automatically.
- Free-tier providers may log or review prompts. Use synthetic incident data
  only; see `docs/ai-runtime.md` for the full data-handling list.