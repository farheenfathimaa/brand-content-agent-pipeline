"""FastAPI entrypoint. Run with `uvicorn app.main:app --reload`."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import router
from app.config import Settings
from app.container import Container, build_container
from app.errors import AppError

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    application = FastAPI(
        title="Brand Content Agent Pipeline",
        version="1.0.0",
        description=(
            "Fictional-brand content pipeline: brief, draft, guardrails, critic "
            "revision, human approval, translation, staging, and publishing. "
            "Mock mode needs zero API keys."
        ),
    )
    application.state.container = container or build_container(settings)
    application.include_router(router, prefix="/api/v1")

    @application.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @application.get("/", tags=["system"])
    def root() -> dict:
        return {
            "name": "brand-content-agent-pipeline",
            "mode": application.state.container.settings.app_mode,
            "docs": "/docs",
            "health": "/health",
            "api": "/api/v1",
            "note": "Alder Grove is a fictional brand used for demonstration.",
        }

    @application.get("/health", tags=["system"])
    def root_health() -> dict:
        return application.state.container.health()

    return application


app = create_app()
