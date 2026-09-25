"""Database engine and session handling.

The engine lives on the FastAPI app (``app.state.engine``), not as a module
global. Lifespan and ``get_session`` then use the bind ``create_app`` was given.
"""

import logging
import time
from asyncio import sleep
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

_RETRY_INTERVAL_SECONDS = 0.5


def build_engine(url: str, **options: Any) -> AsyncEngine:
    # SQLite keeps the driver's deferred BEGIN on purpose. Emitting BEGIN up front
    # would make savepoints nest as on PostgreSQL, but every request reads before
    # it writes, and SQLite fails such a read-to-write upgrade under contention
    # with "database is locked" instead of waiting for the lock.
    return create_async_engine(url, **options)


async def init_db(engine: AsyncEngine, timeout_seconds: float) -> None:
    """Create missing tables, waiting up to ``timeout_seconds`` for the database.

    Creating tables on startup is adequate for an append-only schema; a
    deployment that needs to evolve the schema would use Alembic migrations.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            return
        # asyncpg raises plain OSError while the host is still unreachable.
        except (OperationalError, OSError):
            if time.monotonic() >= deadline:
                raise
            logger.warning("database is not reachable yet, retrying")
            await sleep(_RETRY_INTERVAL_SECONDS)


async def is_reachable(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (DBAPIError, OSError):
        logger.warning("readiness check could not reach the database", exc_info=True)
        return False
    return True


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    # Expiring on commit would make every later attribute access a lazy load,
    # which async sessions cannot perform implicitly.
    async with AsyncSession(request.app.state.engine, expire_on_commit=False) as session:
        yield session
