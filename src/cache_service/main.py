"""Application factory, served with ``uvicorn --factory cache_service.main:create_app``."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from cache_service import __version__
from cache_service.api import router
from cache_service.config import Settings
from cache_service.database import build_engine, init_db
from cache_service.transformer import TransformerClient


def create_app(settings: Settings | None = None, engine: AsyncEngine | None = None) -> FastAPI:
    """Build the API from ``settings``, bound to ``engine``.

    Both default to what the environment describes. Tests pass their own, so
    nothing is read or connected at import time. The app owns the engine either
    way and disposes of it on shutdown.
    """
    settings = settings if settings is not None else Settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db(app.state.engine, settings.database_startup_timeout_seconds)
        yield
        await app.state.engine.dispose()

    app = FastAPI(
        title="Cache Service",
        version=__version__,
        summary="Interleaves two string lists, caching every transformer result.",
        lifespan=lifespan,
    )
    app.state.engine = engine if engine is not None else build_engine(settings.database_url)
    app.state.transformer = TransformerClient(
        max_concurrency=settings.transformer_max_concurrency,
        latency_seconds=settings.transformer_latency_seconds,
    )
    app.include_router(router)
    return app
