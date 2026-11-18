"""End-to-end investigation orchestration endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from backend.app.api.dependencies import require_operator, require_viewer
from backend.app.api.schemas import ErrorResponse
from backend.app.incidents.models import Approval
from backend.app.orchestration.dependencies import get_orchestration_service
from backend.app.orchestration.schemas import (
    InvestigationApprovalRequest,
    InvestigationApprovalResponse,
    InvestigationStartRequest,
    InvestigationStartResponse,
)
from backend.app.orchestration.service import InvestigationOrchestrationService

router = APIRouter(tags=["investigations"])


@router.post(
    "/incidents/{incident_id}/investigations",
    response_model=InvestigationStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an investigation for an incident",
    responses={
        202: {"description": "Investigation is awaiting human approval"},
        200: {"description": "Investigation completed synchronously"},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def start_investigation(
    response: Response,
    incident_id: str = Path(..., min_length=1),
    payload: InvestigationStartRequest | None = None,
    service: InvestigationOrchestrationService = Depends(get_orchestration_service),
    principal: Any = Depends(require_operator),
) -> InvestigationStartResponse:
    result = service.start_investigation(
        incident_id, payload or InvestigationStartRequest(), principal
    )
    response.status_code = (
        status.HTTP_202_ACCEPTED
        if result.workflow_status == "awaiting_approval"
        else status.HTTP_200_OK
    )
    return result


@router.post(
    "/investigations/{investigation_id}/approval",
    response_model=InvestigationApprovalResponse,
    summary="Approve or reject an investigation and resume the workflow",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def decide_investigation(
    payload: InvestigationApprovalRequest,
    investigation_id: str = Path(..., min_length=1),
    service: InvestigationOrchestrationService = Depends(get_orchestration_service),
    principal: Any = Depends(require_operator),
) -> InvestigationApprovalResponse:
    return service.approve_investigation(
        investigation_id,
        payload.decision,
        reason=payload.reason,
        principal=principal,
    )


@router.get(
    "/investigations/{investigation_id}/approval",
    response_model=Approval,
    summary="Retrieve the approval record for an investigation",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def get_investigation_approval(
    investigation_id: str = Path(..., min_length=1),
    service: InvestigationOrchestrationService = Depends(get_orchestration_service),
    principal: Any = Depends(require_viewer),
) -> Approval:
    service.get_investigation(investigation_id)
    approval = service.get_approval(investigation_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    return approval
