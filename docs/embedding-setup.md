# Embedding setup (CPU only)

OpsPilot embeds knowledge-base chunks on CPU with
`sentence-transformers/all-MiniLM-L6-v2`. Everything below is CPU-only: no CUDA
toolkit, no GPU drivers, no model-weight download during tests or CI.

Verified versions:

| Package | Version |
|---|---|
| Python | 3.14.2 |
| torch | 2.15.0+cpu |
| sentence-transformers | 6.3.0 |
| transformers | 5.22.2 |
| tokenizers | 0.23.3 |

## Installation

Install PyTorch from the CPU wheel index **first**:

```bash
python -m pip install "torch==2.15.0+cpu" --index-url https://download.pytorch.org/whl/cpu
```

Then install the embedding dependencies:

```bash
python -m pip install -r requirements-embeddings.txt
```

Both files in this repository exist to keep that order enforced rather than
assumed:

* `constraints-embeddings-cpu.txt` contains the single constraint
  `torch==2.15.0+cpu`.
* `requirements-embeddings.txt` pulls that file in with `-c`, and does not list
  `torch` as a direct requirement (a plain `torch` requirement resolves to the
  default PyPI wheel, which on Linux pulls in the CUDA runtime).

The two mechanisms do different jobs:

| Mechanism | Job |
|---|---|
| `--index-url https://download.pytorch.org/whl/cpu` (step 1) | supplies the CPU-specific `+cpu` wheel, which does not exist on PyPI |
| `torch==2.15.0+cpu` in the constraints file (step 2) | restricts which version may satisfy `sentence-transformers`' `torch>=2.2` |

Because PyPI publishes `2.15.0` and not `2.15.0+cpu`, and PEP 440 compares the
local version label, the plain PyPI wheel does **not** satisfy the constraint.
Skipping step 1 therefore fails the install instead of silently selecting a
CUDA build:

```text
ERROR: Cannot install torch>=2.2 because these package versions have conflicting dependencies.
    The user requested (constraint) torch==2.15.0+cpu
ERROR: ResolutionImpossible: ...
```

The constraint never installs anything on its own; it only vetoes a torch build
that is not the CPU one. When the CPU build is already present, the same
command is a no-op:

```text
Requirement already satisfied: torch>=2.2 ... (2.15.0+cpu)
```

## Confirming CPU execution

```bash
python -c 'import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())'
```

Expected output in this configuration:

```
2.15.0+cpu
None
False
```

`torch.version.cuda` is `None` and `torch.cuda.is_available()` is `False`; that
is correct here, not a problem to fix.

## Using the cached tokenizer offline

The token-aware chunker only needs the tokenizer, so it can run before any
model weights are fetched:

```python
from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(
    "sentence-transformers/all-MiniLM-L6-v2",
    use_fast=True,
    local_files_only=True,
)
```

`local_files_only=True` guarantees no download happens. The tokenizer must be a
fast tokenizer: `backend.app.ingestion.token_chunker` needs sub-token character
offsets, which slow tokenizers cannot provide.

### Tokenizer revision

Token counts are only reproducible against an immutable tokenizer revision, so
every `TokenDocumentChunk` records one:

| Field | Example |
|---|---|
| `tokenizer_name` | `sentence-transformers/all-MiniLM-L6-v2` |
| `tokenizer_revision` | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |

transformers 5.x does not expose `_commit_hash` for a tokenizer loaded from the
local cache, and `tokenizer.files_loaded` is empty, so the revision is read from
the resolved cache path instead: files live under
`models--sentence-transformers--all-MiniLM-L6-v2/snapshots/<commit>/`, and that
`<commit>` directory name *is* the revision the files were fetched from. The
value above comes from this machine's cache; it is not hard-coded in the code.

When the weights are loaded later, pass the same commit so tokenization and
weights come from one snapshot:

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer(
    "sentence-transformers/all-MiniLM-L6-v2",
    revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    device="cpu",
)
```

If a tokenizer is loaded from a plain local directory, no commit exists and
`tokenizer_revision` is `None`. That is recorded as unknown rather than
invented.

## Token-aware chunking

```python
from backend.app.ingestion import chunk_documents, ingest_knowledge_base
from backend.app.ingestion.token_chunker import token_chunk_documents

documents = ingest_knowledge_base()          # 8 documents
parents = chunk_documents(documents)         # 156 character chunks
chunks = token_chunk_documents(parents, tokenizer)  # <= 240 tokens each
```

### Budget and overlap

The two defaults measure different things:

| Setting | Value | Counts special tokens? |
|---|---|---|
| `DEFAULT_MAX_TOKENS` | 240 | **Yes** — content plus the model's `[CLS]`/`[SEP]` |
| `DEFAULT_TOKEN_OVERLAP` | 32 | **No** — content tokens only |

`max_tokens` is the hard limit the embedding model sees, so the two special
tokens are inside it. `token_overlap` is a cap on how many *content* tokens may
be repeated from the previous chunk; special tokens are never part of the
overlap, and the real overlap can be smaller when a boundary or the remaining
budget prevents it.

Each chunk stores both values, so a chunk is self-describing: `max_tokens`,
`token_overlap`, `token_count`, `tokenizer_name` and `tokenizer_revision`.

### Cut policy

1. If the rest of the parent chunk fits, it is emitted unchanged.
2. Otherwise a cut is only taken at a paragraph break (or, failing that, a line
   break) that fills at least 70% of the content window. Paragraph breaks win
   over line breaks.
3. If the preferred region has no structural boundary, the latest word boundary
   is used; a raw token boundary is the last resort.

A cut is never placed directly after an ATX heading, so a heading stays with the
body it introduces. Chunks that already fit are byte-identical to the character
chunker's output.

### Tokenizer compatibility

The boundary logic reads the untokenized text *between* tokens. That is valid
for WordPiece offsets (`BertTokenizer` with `use_fast=True`, the tokenizer used
here); byte-level BPE tokenizers such as GPT-2/Llama absorb the leading space
into the token, so their gaps are always empty. `token_chunk_document_chunk`
validates this on a probe string and raises `ValueError` for unsupported offset
semantics instead of silently degrading to raw token cuts.

## Scope

- Embedding weights are not downloaded and no embeddings are generated yet.
- ChromaDB and any vector store are out of scope for this stage.
