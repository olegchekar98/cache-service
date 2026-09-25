"""Database engine and session handling.

The engine lives on the FastAPI app (``app.state.engine``), not as a module
global. Lifespan and ``get_session`` then use the bind ``create_app`` was given.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

try:
    from asyncpg import PostgresError
except ImportError:  # asyncpg is only installed with the postgres extra
    _SERVER_ERRORS: tuple[type[Exception], ...] = ()
else:
    # SQLAlchemy does not translate errors asyncpg raises while connecting, such as
    # "the database system is starting up", into its own exception types.
    _SERVER_ERRORS = (PostgresError,)

# asyncpg raises plain OSError while the host is unreachable, and TimeoutError
# (an OSError) when it does not answer.
_UNREACHABLE: tuple[type[Exception], ...] = (OperationalError, OSError, *_SERVER_ERRORS)
_READY_CHECK_FAILURES: tuple[type[Exception], ...] = (DBAPIError, *_UNREACHABLE)

logger = logging.getLogger(__name__)

_RETRY_INTERVAL_SECONDS = 0.5
# Below the container health check's 3 s timeout, and far below asyncpg's 60 s
# connect timeout, so an unresponsive host cannot pile up hanging checks.
_READY_TIMEOUT_SECONDS = 2.0


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
        except _UNREACHABLE as error:
            if time.monotonic() >= deadline:
                raise
            logger.warning("database is not reachable yet, retrying: %s", _describe(error))
            await asyncio.sleep(_RETRY_INTERVAL_SECONDS)


async def is_reachable(engine: AsyncEngine) -> bool:
    try:
        async with asyncio.timeout(_READY_TIMEOUT_SECONDS), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except _READY_CHECK_FAILURES as error:
        # One line per failed check: the health check repeats it every 30 seconds.
        logger.warning("readiness check failed: %s", _describe(error))
        return False
    return True


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    # Expiring on commit would make every later attribute access a lazy load,
    # which async sessions cannot perform implicitly.
    async with AsyncSession(request.app.state.engine, expire_on_commit=False) as session:
        yield session
