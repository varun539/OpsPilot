"""API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.routes import dashboard as dashboard_routes
from backend.app.api.routes import health as health_routes
from backend.app.api.routes import incidents as incidents_routes
from backend.app.api.routes import investigations as investigations_routes
from backend.app.api.routes import orchestration as orchestration_routes
from backend.app.api.routes import tickets as tickets_routes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(incidents_routes.router)
api_router.include_router(dashboard_routes.router)
api_router.include_router(tickets_routes.router)
api_router.include_router(investigations_routes.router)
api_router.include_router(orchestration_routes.router)

root_router = APIRouter()
root_router.include_router(health_routes.router)
