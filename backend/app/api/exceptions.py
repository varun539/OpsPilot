"""API exception handling and error responses."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.auth.exceptions import AuthConfigurationError, InvalidTokenError
from backend.app.incidents.exceptions import (
    DuplicateRecordError,
    IncidentError,
    IncidentNotFoundError,
    InvalidInputError,
    InvalidStateTransitionError,
    InvestigationNotFoundError,
    TicketNotFoundError,
)
from backend.app.orchestration.exceptions import (
    AIServiceUnavailableError,
    InvestigationResumeError,
    InvestigationStartError,
    OrchestrationError,
)


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if existing:
        return str(existing)
    value = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = value
    return value


def _error_response(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = _request_id(request)
        code = "http_error"
        if exc.status_code == 401:
            code = "unauthorized"
        elif exc.status_code == 403:
            code = "forbidden"
        elif exc.status_code == 404:
            code = "not_found"
        return _error_response(exc.status_code, code, str(exc.detail), request_id)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = _request_id(request)
        return _error_response(422, "validation_error", "request validation failed", request_id)

    @app.exception_handler(IncidentNotFoundError)
    async def incident_not_found_handler(
        request: Request, exc: IncidentNotFoundError
    ) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(404, "not_found", "record not found", rid)

    @app.exception_handler(TicketNotFoundError)
    async def ticket_not_found_handler(request: Request, exc: TicketNotFoundError) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(404, "not_found", "record not found", rid)

    @app.exception_handler(InvestigationNotFoundError)
    async def investigation_not_found_handler(
        request: Request, exc: InvestigationNotFoundError
    ) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(404, "not_found", "record not found", rid)

    @app.exception_handler(InvalidInputError)
    async def invalid_input_handler(request: Request, exc: InvalidInputError) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(400, "invalid_input", str(exc), rid)

    @app.exception_handler(InvalidStateTransitionError)
    async def invalid_transition_handler(
        request: Request, exc: InvalidStateTransitionError
    ) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(409, "conflict", str(exc), rid)

    @app.exception_handler(DuplicateRecordError)
    async def duplicate_handler(request: Request, exc: DuplicateRecordError) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(409, "conflict", "duplicate record", rid)

    @app.exception_handler(IncidentError)
    async def incident_error_handler(request: Request, exc: IncidentError) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(400, "incident_error", "incident operation failed", rid)

    @app.exception_handler(InvestigationStartError)
    async def investigation_start_handler(
        request: Request, exc: InvestigationStartError
    ) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(502, "investigation_failed", str(exc), rid)

    @app.exception_handler(AIServiceUnavailableError)
    async def ai_unavailable_handler(
        request: Request, exc: AIServiceUnavailableError
    ) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(503, "ai_unavailable", str(exc), rid)

    @app.exception_handler(InvestigationResumeError)
    async def investigation_resume_handler(
        request: Request, exc: InvestigationResumeError
    ) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(502, "resume_failed", str(exc), rid)

    @app.exception_handler(OrchestrationError)
    async def orchestration_handler(request: Request, exc: OrchestrationError) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(500, "orchestration_error", "investigation failed", rid)

    @app.exception_handler(AuthConfigurationError)
    async def auth_config_handler(request: Request, exc: AuthConfigurationError) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(401, "unauthorized", "authentication not configured", rid)

    @app.exception_handler(InvalidTokenError)
    async def invalid_token_handler(request: Request, exc: InvalidTokenError) -> JSONResponse:
        rid = _request_id(request)
        return _error_response(401, "unauthorized", "invalid or expired token", rid)

    @app.exception_handler(Exception)
    async def unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
        # Preserve diagnostics in server logs; avoid stack trace to client.
        rid = _request_id(request)
        return _error_response(500, "internal_error", "internal server error", rid)
