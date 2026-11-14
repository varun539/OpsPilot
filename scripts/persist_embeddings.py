"""Persist the existing OpsPilot embeddings into a local ChromaDB collection.

Runs the full project workflow end to end:

1. Ingest the eight technical documents.
2. Produce character-based parent chunks.
3. Load the pinned MiniLM tokenizer (local cache only).
4. Produce token-aware chunks.
5. Load the pinned SentenceTransformer on CPU (local cache only).
6. Encode every token chunk in batches.
7. Upsert the resulting vectors into a persistent Chroma collection.
8. Report counts, dimensions, and storage status.

The operation is idempotent: rerunning it upserts by stable chunk id, so no
duplicate records are created. Vectors are stored on disk under ``chroma/``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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
from backend.app.vectorstore import ChromaVectorStore  # noqa: E402

BATCH_SIZE = 16
EXPECTED_DOCUMENTS = 8
EXPECTED_TOKEN_CHUNKS = 203


def main() -> None:
    started = time.perf_counter()

    # 1. Ingest documents.
    documents = ingest_knowledge_base()
    assert len(documents) == EXPECTED_DOCUMENTS, len(documents)

    # 2. Character-based chunks.
    parents = chunk_documents(documents)

    # 3. Pinned tokenizer (local cache only).
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        MODEL_ID,
        use_fast=True,
        local_files_only=True,
        revision=MODEL_REVISION,
    )

    # 4. Token-aware chunks.
    token_chunks = token_chunk_documents(parents, tokenizer)
    assert len(token_chunks) == EXPECTED_TOKEN_CHUNKS, len(token_chunks)

    # 5. Load model on CPU (local cache only).
    model_load_start = time.perf_counter()
    model = load_embedding_model(
        device="cpu",
        local_files_only=True,
        model_id=MODEL_ID,
        revision=MODEL_REVISION,
    )
    model_load_time = time.perf_counter() - model_load_start

    max_seq_length = int(model.max_seq_length)
    dimension = int(
        getattr(model, "get_embedding_dimension", model.get_sentence_embedding_dimension)()
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

    assert len(results) == len(token_chunks)

    # 7. Persist into ChromaDB.
    store_start = time.perf_counter()
    with ChromaVectorStore() as store:
        upserted = store.upsert(results, token_chunks=token_chunks, batch_size=100)
        final_count = store.count()
    store_time = time.perf_counter() - store_start

    total_time = time.perf_counter() - started

    print("=" * 72)
    print("OpsPilot ChromaDB Persistence Report")
    print("=" * 72)
    print(f"Model ID:                 {MODEL_ID}")
    print(f"Model revision:           {MODEL_REVISION}")
    print("Device:                   cpu")
    print(f"Embedding dimension:      {dimension}")
    print(f"Model max_seq_length:     {max_seq_length}")
    print(f"Documents:                {len(documents)}")
    print(f"Parent chunks:            {len(parents)}")
    print(f"Token chunks:             {len(token_chunks)}")
    print(f"Generated embeddings:     {len(results)}")
    print(f"Upserted records:         {upserted}")
    print(f"Collection count:         {final_count}")
    print(f"Vector shape:             ({len(results)}, {dimension})")
    print(f"Model load time (s):      {model_load_time:.3f}")
    print(f"Encode time (s):          {encode_time:.3f}")
    print(f"Store time (s):           {store_time:.3f}")
    print(f"Total time (s):           {total_time:.3f}")
    print("Status:                   OK")
    print("=" * 72)


if __name__ == "__main__":
    main()