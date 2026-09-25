"""Application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from cache_service import __version__
from cache_service.api import router
from cache_service.config import settings
from cache_service.database import build_engine, init_db


def create_app(engine: Engine | None = None) -> FastAPI:
    """Build the API bound to ``engine``.

    Tests pass their own engine so lifespan, handlers and the transformer cache
    all talk to the same database. Production leaves ``engine`` empty and uses
    ``CACHE_SERVICE_DATABASE_URL``.
    """
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")
    app_engine = engine if engine is not None else build_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(app.state.engine)
        yield

    app = FastAPI(
        title="Cache Service",
        version=__version__,
        summary="Interleaves two string lists, caching every transformer result.",
        lifespan=lifespan,
    )
    app.state.engine = app_engine
    app.include_router(router)
    return app


app = create_app()
