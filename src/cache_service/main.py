"""Application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cache_service import __version__
from cache_service.api import router
from cache_service.config import settings
from cache_service.database import init_db


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")
    app = FastAPI(
        title="Cache Service",
        version=__version__,
        summary="Interleaves two string lists, caching every transformer result.",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
