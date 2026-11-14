# RAG Setup

OpsPilot's Phase 5 RAG pipeline combines hybrid retrieval with an OpenAI structured generation provider.

## Architecture

```text
Incident question
  -> HybridSearchEngine
  -> ContextBuilder
  -> OpenAIProvider
  -> InvestigationReport
  -> CitationValidator
  -> RAGResponse
```

## Installation

```bash
python -m pip install -r requirements.txt
```

The OpenAI SDK is pinned to `openai==3.51.0` for compatibility with Python 3.14.

## Configuration

Set these environment variables:

```bash
export OPENAI_API_KEY="..."
export OPSPILOT_LLM_MODEL="gpt-6-astra"
export OPSPILOT_LLM_TIMEOUT="30"
export OPSPILOT_LLM_MAX_OUTPUT_TOKENS="2000"
```

The default model is `gpt-6-astra` and can be overridden with `OPSPILOT_LLM_MODEL`.

## Running an investigation

```python
from backend.app.llm.openai_provider import OpenAIProvider
from backend.app.rag.generator import RAGService
from backend.app.retrieval.hybrid_search import HybridSearchEngine

engine = HybridSearchEngine()
provider = OpenAIProvider()  # reads OPENAI_API_KEY from the environment
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

All standard tests run offline using fake providers and mocked OpenAI clients. No live API requests are made during the test suite.

## Security and cost

- API keys are never logged or committed.
- Retrieved documents and user input are treated as untrusted data.
- Recommended actions require human review and are not executed automatically.