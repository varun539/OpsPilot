"""Production AI runtime assembly for OpsPilot.

The package is deliberately import-light: only configuration and error types
are exported eagerly. The embedding model, ChromaDB client and retrieval stack
are imported inside :func:`backend.app.ai.runtime.assemble_ai_runtime` so that
an application running with ``OPSPILOT_AI_MODE=disabled`` never imports
PyTorch, sentence-transformers or ChromaDB.
"""

from __future__ import annotations

from .config import AIMode, AIRuntimeConfig, AIRuntimeConfigurationError
from .runtime import AIRuntime, assemble_ai_runtime, disabled_ai_runtime

__all__ = [
    "AIMode",
    "AIRuntimeConfig",
    "AIRuntimeConfigurationError",
    "AIRuntime",
    "assemble_ai_runtime",
    "disabled_ai_runtime",
]
