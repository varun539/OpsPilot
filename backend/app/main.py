"""OpsPilot API application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.app.ai import (
    AIRuntimeConfig,
    assemble_ai_runtime,
    disabled_ai_runtime,
)
from backend.app.api.dependencies import get_incident_service  # noqa: F401
from backend.app.api.exceptions import register_exception_handlers
from backend.app.api.middleware import (
    BodySizeLimitMiddleware,
    apply_security_headers,
    configured_max_request_bytes,
    sanitize_request_id,
)
from backend.app.api.rate_limit import (
    env_rate_limit_requests,
    env_rate_limit_window_seconds,
)
from backend.app.api.router import api_router, root_router
from backend.app.auth.config import AuthSettings, validate_runtime_configuration
from backend.app.auth.dependencies import get_current_principal  # noqa: F401
from backend.app.incidents import IncidentService
from backend.app.orchestration.service import InvestigationOrchestrationService


def _request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):  # type: ignore[override]
        # Client-supplied values are reflected into a response header and error
        # envelopes, so only a conservative identifier shape is echoed.
        request_id = sanitize_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        apply_security_headers(response.headers, path=request.url.path)
        return response


def _build_orchestration(
    app: FastAPI,
    *,
    llm_provider: Any = None,
    retrieval_engine: Any = None,
    rag_service: Any = None,
    ai_runtime: Any = None,
) -> InvestigationOrchestrationService:
    return InvestigationOrchestrationService(
        incident_service=app.state.incident_service,
        checkpoint_db_path=getattr(app.state, "checkpoint_db_path", None),
        checkpointer=getattr(app.state, "checkpointer", None),
        in_memory=getattr(app.state, "in_memory", False),
        agent=getattr(app.state, "agent", None),
        llm_provider=llm_provider,
        retrieval_engine=retrieval_engine,
        rag_service=rag_service,
        ai_runtime=ai_runtime,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_configuration(app.state.auth_settings)
    service = getattr(app.state, "incident_service", None)
    if service is None:
        db_path = getattr(app.state, "db_path", None)
        service = IncidentService() if db_path is None else IncidentService(db_path=db_path)
        app.state.incident_service = service

    runtime = getattr(app.state, "ai_runtime", None)
    if runtime is None and not getattr(app.state, "ai_runtime_injected", False):
        config = AIRuntimeConfig.from_env()
        if config.mode == "live":
            # Assemble before building the graph; failures abort startup rather
            # than silently degrading to deterministic reports.
            runtime = assemble_ai_runtime(config)
            app.state.ai_runtime = runtime
            previous = getattr(app.state, "orchestration_service", None)
            if previous is not None:
                try:
                    previous.close()
                except Exception:
                    pass
            app.state.orchestration_service = _build_orchestration(
                app,
                llm_provider=runtime.provider,
                retrieval_engine=runtime.retrieval_engine,
                rag_service=runtime.rag_service,
                ai_runtime=runtime,
            )
        else:
            runtime = disabled_ai_runtime(config)
            app.state.ai_runtime = runtime

    orchestration = getattr(app.state, "orchestration_service", None)
    if orchestration is None:
        orchestration = _build_orchestration(
            app,
            llm_provider=getattr(app.state, "llm_provider", None),
            retrieval_engine=getattr(app.state, "retrieval_engine", None),
            rag_service=getattr(app.state, "rag_service", None),
            ai_runtime=runtime,
        )
        app.state.orchestration_service = orchestration
    elif runtime is not None:
        # Disabled or explicitly injected runtime: keep the existing graph and
        # mark it so new investigations respect the runtime state.
        orchestration.ai_runtime = runtime

    try:
        yield
    finally:
        orchestration = getattr(app.state, "orchestration_service", None)
        if orchestration is not None and hasattr(orchestration, "close"):
            try:
                orchestration.close()
            except Exception:
                pass
        runtime = getattr(app.state, "ai_runtime", None)
        if runtime is not None and hasattr(runtime, "close"):
            try:
                runtime.close()
            except Exception:
                pass
        service = getattr(app.state, "incident_service", None)
        if service is not None:
            database = getattr(service, "database", None)
            if database is not None:
                try:
                    database.close()
                except Exception:
                    pass


def create_app(
    *,
    db_path: str | None = None,
    auth_settings: AuthSettings | None = None,
    host: str | None = None,
    checkpoint_db_path: str | None = None,
    checkpoint_path: str | None = None,
    checkpoint_db: str | None = None,
    checkpointer: Any = None,
    in_memory: bool = False,
    agent: Any = None,
    llm_provider: Any = None,
    retrieval_engine: Any = None,
    search_engine: Any = None,
    rag_service: Any = None,
    ai_runtime: Any = None,
    orchestration_service: InvestigationOrchestrationService | None = None,
    rate_limit_requests: int | None = None,
    rate_limit_window_seconds: float | None = None,
    max_request_bytes: int | None = None,
) -> FastAPI:
    app = FastAPI(
        title="OpsPilot API",
        description="Agentic AI Support and Incident Investigation System",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.state.db_path = db_path
    if db_path is None:
        app.state.incident_service = IncidentService()
    else:
        app.state.incident_service = IncidentService(db_path=db_path)
    app.state.checkpoint_db_path = checkpoint_db_path or checkpoint_path or checkpoint_db
    app.state.checkpointer = checkpointer
    app.state.in_memory = in_memory
    app.state.agent = agent
    app.state.llm_provider = llm_provider
    app.state.retrieval_engine = retrieval_engine or search_engine
    app.state.rag_service = rag_service
    app.state.ai_runtime = ai_runtime
    # Any injected AI dependency (or an injected runtime) disables automatic
    # environment assembly, preserving the test seams from earlier phases.
    app.state.ai_runtime_injected = ai_runtime is not None or any(
        dependency is not None
        for dependency in (
            agent,
            llm_provider,
            retrieval_engine,
            search_engine,
            rag_service,
            orchestration_service,
        )
    )
    if ai_runtime is not None:
        llm_provider = llm_provider or getattr(ai_runtime, "provider", None)
        retrieval_engine = retrieval_engine or getattr(ai_runtime, "retrieval_engine", None)
        rag_service = rag_service or getattr(ai_runtime, "rag_service", None)
    app.state.orchestration_service = orchestration_service
    if orchestration_service is None:
        app.state.orchestration_service = InvestigationOrchestrationService(
            incident_service=app.state.incident_service,
            checkpoint_db_path=app.state.checkpoint_db_path,
            checkpointer=checkpointer,
            in_memory=in_memory,
            agent=agent,
            llm_provider=llm_provider,
            retrieval_engine=retrieval_engine or search_engine,
            rag_service=rag_service,
            ai_runtime=ai_runtime,
        )
    if auth_settings is None:
        auth_settings = AuthSettings()
    if host is not None:
        auth_settings.host = host
    if (
        auth_settings.auth_mode == "development"
        and auth_settings.dev_auth_enabled
        and not auth_settings.is_loopback_host()
    ):
        raise RuntimeError("development authentication cannot be enabled on a public bind address")
    app.state.auth_settings = auth_settings

    # Request body ceiling. Added before CORS so CORS stays the outermost
    # middleware and still decorates 413 responses with the right headers.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=(
            configured_max_request_bytes()
            if max_request_bytes is None
            else int(max_request_bytes)
        ),
    )

    app.state.rate_limit_settings = {
        "limit": (
            env_rate_limit_requests()
            if rate_limit_requests is None
            else max(0, int(rate_limit_requests))
        ),
        "window_seconds": (
            env_rate_limit_window_seconds()
            if rate_limit_window_seconds is None
            else max(0.001, float(rate_limit_window_seconds))
        ),
    }
    app.state.rate_limiters = {}

    # CORS: conservative defaults.
    origins = auth_settings.cors_origins
    allow_credentials = True
    if "*" in origins:
        allow_credentials = False
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=allow_credentials,
            allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )

    _request_id_middleware(app)
    register_exception_handlers(app)

    app.include_router(root_router)
    app.include_router(api_router)

    # OpenAPI bearer auth scheme.
    def customize_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        schemes["HTTPBearer"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        schema["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = customize_openapi  # type: ignore[assignment]

    return app


app = create_app()
