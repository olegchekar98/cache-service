"""Database engine and session handling.

The engine lives on the FastAPI app (``app.state.engine``), not as a module
global. Lifespan and ``get_session`` then use the bind ``create_app`` was given.
"""

import logging
import time
from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from cache_service.config import settings

logger = logging.getLogger(__name__)

_RETRY_INTERVAL_SECONDS = 0.5


def build_engine(url: str) -> Engine:
    # SQLite binds a connection to the thread that created it, which breaks the
    # threadpool FastAPI uses to run synchronous endpoints.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(engine: Engine) -> None:
    """Create missing tables, waiting for the database to become reachable.

    Creating tables on startup is adequate for an append-only schema; a
    deployment that needs to evolve the schema would use Alembic migrations.
    """
    deadline = time.monotonic() + settings.database_startup_timeout_seconds
    while True:
        try:
            SQLModel.metadata.create_all(engine)
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            logger.warning("database is not reachable yet, retrying")
            time.sleep(_RETRY_INTERVAL_SECONDS)


def get_session(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.engine) as session:
        yield session
