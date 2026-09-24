"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import candidates, exports, images, investigations, sessions, system
from app.api.security import SecurityMiddleware
from app.config import PROJECT_ROOT, Settings, get_settings
from app.container import Container, build_container
from app.infrastructure.security.redaction import configure_logging

logger = logging.getLogger(__name__)
STATIC_DIR = PROJECT_ROOT / "app" / "static"


async def _retention_loop(container: Container) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.to_thread(container.retention.purge)
        except Exception:
            logger.exception("Retention purge failed")


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    settings = container.settings if container else (settings or get_settings())
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container or build_container(settings)
        app.state.container = c
        interrupted = c.repo.mark_interrupted()
        if interrupted:
            logger.info("Marked %d investigation(s) as INTERRUPTED after restart", interrupted)
        await asyncio.to_thread(c.retention.purge)
        if settings.app_env != "production" and settings.host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning("Development mode bound to a non-local interface — do not expose this publicly.")
        if settings.host not in ("127.0.0.1", "localhost", "::1") and settings.app_api_token is None:
            logger.warning("No APP_API_TOKEN set while listening on %s: anyone who can reach the port can use the API.",
                           settings.host)
        task = asyncio.create_task(_retention_loop(c))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if container is None:
                await c.aclose()
            else:
                await c.runner.shutdown()

    app = FastAPI(
        title="Person Intel Agent",
        description="Image-first candidate discovery and verification. Results are candidate matches that must be "
                    "verified independently.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    if container is not None:
        app.state.container = container
    app.add_middleware(SecurityMiddleware, settings=settings)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-API-Token"])
    for module in (system, investigations, images, candidates, exports, sessions):
        app.include_router(module.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
