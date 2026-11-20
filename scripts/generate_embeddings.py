"""Generate real embeddings for every token-aware chunk in the knowledge base.

Runs the full project workflow end to end:

1. Ingest the eight technical documents.
2. Produce character-based parent chunks.
3. Load the pinned MiniLM tokenizer.
4. Produce token-aware chunks.
5. Load the pinned SentenceTransformer on CPU.
6. Encode every token chunk in batches.
7. Validate vectors and provenance.
8. Print a detailed report.

Vectors are kept in memory only; nothing is persisted or committed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import transformers  # noqa: E402

from backend.app.embeddings import (  # noqa: E402
    MODEL_ID,
    MODEL_REVISION,
    EmbeddingEncoder,
    load_embedding_model,
)
from backend.app.ingestion import (  # noqa: E402
    chunk_documents,
    ingest_knowledge_base,
    token_chunk_documents,
)

BATCH_SIZE = 16
EXPECTED_DOCUMENTS = 8


def main() -> None:
    started = time.perf_counter()

    # 1. Ingest documents.
    documents = ingest_knowledge_base()
    assert len(documents) == EXPECTED_DOCUMENTS, len(documents)

    # 2. Character-based chunks.
    parents = chunk_documents(documents)

    # 3. Pinned tokenizer.
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        MODEL_ID,
        use_fast=True,
        local_files_only=True,
        revision=MODEL_REVISION,
    )

    # 4. Token-aware chunks.
    token_chunks = token_chunk_documents(parents, tokenizer)
    assert len(token_chunks) == 203, len(token_chunks)

    # 5. Load model on CPU (weights may download on first run).
    model_load_start = time.perf_counter()
    model = load_embedding_model(
        device="cpu",
        local_files_only=False,
        model_id=MODEL_ID,
        revision=MODEL_REVISION,
    )
    model_load_time = time.perf_counter() - model_load_start

    max_seq_length = int(model.max_seq_length)
    dimension = int(
        getattr(model, "get_embedding_dimension", model.get_sentence_embedding_dimension)()
    )

    # Re-tokenize every chunk with the model's own tokenizer, special tokens on,
    # truncation disabled, to confirm nothing exceeds the model limit.
    max_measured = 0
    for chunk in token_chunks:
        measured = len(
            model.tokenizer(chunk.text, add_special_tokens=True, truncation=False)[
                "input_ids"
            ]
        )
        if measured > max_measured:
            max_measured = measured
        if measured > max_seq_length:
            raise RuntimeError(
                f"chunk {chunk.token_chunk_id} has {measured} tokens > "
                f"model limit {max_seq_length}"
            )

    # 6. Encode in batches.
    encoder = EmbeddingEncoder(
        model,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )

    encode_start = time.perf_counter()
    results = encoder.encode(token_chunks)
    encode_time = time.perf_counter() - encode_start

    # 7. Validate.
    assert len(results) == len(token_chunks)
    token_chunk_ids = [c.token_chunk_id for c in token_chunks]
    result_ids = [r.token_chunk_id for r in results]
    assert result_ids == token_chunk_ids, "ordering or id mismatch"

    assert len(set(result_ids)) == len(result_ids), "duplicate chunk ids"

    # Provenance check.
    doc_ids = {d.doc_id for d in documents}
    assert {r.doc_id for r in results} <= doc_ids

    vectors = np.stack([r.embedding for r in results], axis=0)
    assert vectors.shape == (len(token_chunks), dimension)
    assert vectors.dtype == np.float32

    assert np.all(np.isfinite(vectors))
    norms = np.linalg.norm(vectors, axis=1)
    assert np.all(norms > 0)
    assert np.allclose(norms, 1.0, atol=1e-5)

    # Repeated encoding of a small sample for numerical consistency.
    sample = token_chunks[:5]
    first = np.stack([r.embedding for r in encoder.encode(sample)], axis=0)
    second = np.stack([r.embedding for r in encoder.encode(sample)], axis=0)
    max_diff = float(np.max(np.abs(first - second)))
    assert max_diff < 1e-6, max_diff

    # 8. Report.
    total_time = time.perf_counter() - started
    throughput = len(token_chunks) / encode_time if encode_time > 0 else float("inf")

    try:
        import resource

        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:  # pragma: no cover - platform dependent
        rss_mb = float("nan")

    print("=" * 72)
    print("OpsPilot Embedding Generation Report")
    print("=" * 72)
    print(f"Model ID:                 {MODEL_ID}")
    print(f"Model revision:           {MODEL_REVISION}")
    print("Device:                   cpu")
    print(f"torch.version.cuda:       {torch.version.cuda}")
    print(f"torch.cuda.is_available:  {torch.cuda.is_available()}")
    print(f"Embedding dimension:      {dimension}")
    print(f"Model max_seq_length:     {max_seq_length}")
    print(f"Documents:                {len(documents)}")
    print(f"Parent chunks:            {len(parents)}")
    print(f"Token chunks:             {len(token_chunks)}")
    print(f"Generated embeddings:     {len(results)}")
    print(f"Batch size:               {BATCH_SIZE}")
    print(f"Vector shape:             {vectors.shape}")
    print(f"Vector dtype:             {vectors.dtype}")
    print(f"Max measured tokens:      {max_measured}")
    print(f"Max L2 norm deviation:    {float(np.max(np.abs(norms - 1.0))):.3e}")
    print(f"Repeated-encode max diff: {max_diff:.3e}")
    print(f"Model load time (s):      {model_load_time:.3f}")
    print(f"Encode time (s):          {encode_time:.3f}")
    print(f"Total time (s):           {total_time:.3f}")
    print(f"Throughput (chunks/s):    {throughput:.2f}")
    print(f"Peak RSS (MB):            {rss_mb:.1f}")
    print("Status:                   OK")
    print("=" * 72)


if __name__ == "__main__":
    main()
