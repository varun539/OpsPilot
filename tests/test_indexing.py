"""Tests for the repeatable knowledge-base indexing command."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.indexing import index_knowledge_base
from backend.app.indexing.__main__ import main as cli_main
from backend.app.vectorstore import ChromaVectorStore
from tests.ai_fakes import StubEmbeddingModel

DOC_ONE = """---
id: KB-001
title: Connection pool exhaustion
category: database
component: api
severity: high
tags: [database, pool]
source_type: synthetic
last_updated: 2026-01-01
---

FastAPI returns HTTP 500 when the database connection pool is exhausted.
Inspect pool saturation and slow queries before raising the limit.
"""

DOC_TWO = """---
id: KB-002
title: Missing environment variables
category: configuration
component: api
severity: medium
tags: [configuration]
source_type: synthetic
---

A missing JWT secret causes startup failures. Verify the environment file.
"""


def _write_kb(directory: Path, *documents: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for index, content in enumerate(documents, start=1):
        (directory / f"KB-{index:03d}.md").write_text(content, encoding="utf-8")
    return directory


def _store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(persist_directory=tmp_path / "chroma")


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    kb = _write_kb(tmp_path / "kb", DOC_ONE, DOC_TWO)
    result = index_knowledge_base(
        directory=kb,
        dry_run=True,
        model=StubEmbeddingModel(),
        tokenizer=StubEmbeddingModel().tokenizer,
    )

    assert result.dry_run is True
    assert result.documents == 2
    assert result.parent_chunks >= 2
    assert result.token_chunks >= 2
    assert result.upserted == 0
    assert result.collection_count == 0
    assert not (tmp_path / "chroma").exists()


def test_indexing_is_idempotent_and_preserves_metadata(tmp_path: Path) -> None:
    kb = _write_kb(tmp_path / "kb", DOC_ONE, DOC_TWO)
    model = StubEmbeddingModel()
    store = _store(tmp_path)
    try:
        first = index_knowledge_base(directory=kb, store=store, model=model)
        assert first.documents == 2
        assert first.upserted == first.token_chunks
        assert first.collection_count == first.token_chunks

        ids_before = set(store.get(include=["metadatas"])["ids"])
        second = index_knowledge_base(directory=kb, store=store, model=model)
        ids_after = set(store.get(include=["metadatas"])["ids"])

        assert second.upserted == first.token_chunks
        assert second.collection_count == first.collection_count
        assert ids_after == ids_before

        record = store.get(ids=["KB-001::chunk::0000::tok::0000"])
        assert record["ids"] == ["KB-001::chunk::0000::tok::0000"]
        metadata = record["metadatas"][0]
        assert metadata["doc_id"] == "KB-001"
        assert metadata["title"] == "Connection pool exhaustion"
        assert metadata["source_path"] == "KB-001.md"
        assert metadata["tokenizer_name"] == "stub-fast-tokenizer"
        assert "pool" in record["documents"][0].lower()
    finally:
        store.close()


def test_records_persist_across_store_reconstruction(tmp_path: Path) -> None:
    kb = _write_kb(tmp_path / "kb", DOC_ONE)
    model = StubEmbeddingModel()
    store = _store(tmp_path)
    result = index_knowledge_base(directory=kb, store=store, model=model)
    store.close()

    reopened = _store(tmp_path)
    try:
        assert reopened.count() == result.collection_count
        assert reopened.get(limit=1)["ids"]
    finally:
        reopened.close()


def test_invalid_document_preserves_existing_records(tmp_path: Path) -> None:
    kb = _write_kb(tmp_path / "kb", DOC_ONE)
    model = StubEmbeddingModel()
    store = _store(tmp_path)
    try:
        good = index_knowledge_base(directory=kb, store=store, model=model)
        (kb / "KB-999.md").write_text("no frontmatter here", encoding="utf-8")

        with pytest.raises(Exception, match="KB-999.md"):
            index_knowledge_base(directory=kb, store=store, model=model)

        assert store.count() == good.collection_count
    finally:
        store.close()


def test_cli_dry_run_and_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.indexing.indexer as indexer

    monkeypatch.setattr(indexer, "_load_tokenizer", lambda **_: StubEmbeddingModel().tokenizer)
    kb = _write_kb(tmp_path / "kb", DOC_ONE)
    assert cli_main(["--dry-run", "--knowledge-base", str(kb)]) == 0
    output = capsys.readouterr().out
    assert '"dry_run": true' in output
    assert '"documents": 1' in output

    assert cli_main(["--knowledge-base", str(tmp_path / "missing")]) == 1
    assert "indexing failed" in capsys.readouterr().err
