"""FastAPI dependencies for OpsPilot orchestration."""

from __future__ import annotations

from fastapi import Request

from backend.app.orchestration.service import InvestigationOrchestrationService


def get_orchestration_service(request: Request) -> InvestigationOrchestrationService:
    return request.app.state.orchestration_service
