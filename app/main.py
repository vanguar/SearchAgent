from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.core.config import Settings
from app.core.logging import logger, setup_logging
from app.web.routes import WEB_ROUTERS


def create_app() -> FastAPI:
    """Application factory for FastAPI startup."""
    settings = Settings()
    setup_logging()

    app = FastAPI(title=settings.app_name, version=settings.app_version, debug=settings.debug)
    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

    app.include_router(health_router)
    for web_router in WEB_ROUTERS:
        app.include_router(web_router)

    logger.info(
        "application_initialized app_name=%s app_version=%s",
        settings.app_name,
        settings.app_version,
    )
    return app
