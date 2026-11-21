"""Checkpoint helpers for OpsPilot LangGraph investigations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from backend.app.incidents.database import restrict_file_permissions

# Restrict checkpoint deserialization to LangGraph's safe built-in types.
STRICT_SERDE = JsonPlusSerializer(allowed_msgpack_modules=None)


def checkpoint_db_path() -> Path:
    raw = os.getenv("OPSPILOT_CHECKPOINT_DB", "").strip()
    if raw:
        return Path(raw)
    return Path("data") / "opspilot_checkpoints.sqlite"


class SQLiteCheckpointer:
    """Context-managed SQLite checkpointer with explicit lifecycle."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else checkpoint_db_path()
        self._context_manager: Any = None
        self.saver: SqliteSaver | None = None

    def open(self) -> SqliteSaver:
        if self.saver is not None:
            return self.saver
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._context_manager = SqliteSaver.from_conn_string(str(self.db_path))
        self.saver = self._context_manager.__enter__()
        # Enforce restrictive deserialization even though from_conn_string
        # creates its own default serializer.
        self.saver.serde = STRICT_SERDE
        self.saver.jsonplus_serde = STRICT_SERDE
        # Checkpoints can contain incident text and diagnostic evidence; keep
        # the database readable only by the service account.
        restrict_file_permissions(self.db_path)
        return self.saver

    def close(self) -> None:
        if self._context_manager is not None:
            try:
                self._context_manager.__exit__(None, None, None)
            finally:
                self._context_manager = None
                self.saver = None

    def __enter__(self) -> "SQLiteCheckpointer":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def create_checkpointer(*, db_path: str | Path | None = None, in_memory: bool = False) -> Any:
    """Create a LangGraph checkpointer."""
    if in_memory:
        return InMemorySaver(serde=STRICT_SERDE)
    manager = SQLiteCheckpointer(db_path)
    return manager.open()


def in_memory_checkpointer() -> InMemorySaver:
    return InMemorySaver()
