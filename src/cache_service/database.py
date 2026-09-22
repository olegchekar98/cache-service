"""Database engine and session handling."""

import logging
import time
from collections.abc import Generator

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from cache_service.config import settings

logger = logging.getLogger(__name__)

_RETRY_INTERVAL_SECONDS = 0.5


def _build_engine(url: str) -> Engine:
    # SQLite binds a connection to the thread that created it, which breaks the
    # threadpool FastAPI uses to run synchronous endpoints.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = _build_engine(settings.database_url)


def init_db() -> None:
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


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
