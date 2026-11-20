"""Health and AI-readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Never touches the AI runtime or external services."""
    return {"status": "ok", "service": "opspilot-api"}


@router.get("/health/ai")
def ai_health(request: Request) -> dict[str, Any]:
    """Report AI runtime readiness without secrets, paths or provider calls.

    A missing or empty knowledge collection is reported as a setup
    prerequisite (``ready: false``), not hidden.
    """
    runtime = getattr(request.app.state, "ai_runtime", None)
    if runtime is None or not hasattr(runtime, "status"):
        return {
            "mode": "disabled",
            "ready": False,
            "reason": "ai_runtime_not_assembled",
            "provider_configured": False,
            "provider": None,
            "provider_model": None,
            "embedding_model": None,
            "embedding_revision": None,
            "collection": None,
            "knowledge_chunks": 0,
            "checks": {
                "provider": False,
                "embedding_model": False,
                "vector_store": False,
                "knowledge_collection": False,
                "hybrid_retrieval": False,
                "rag_service": False,
            },
        }
    return runtime.status()
