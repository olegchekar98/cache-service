"""Database engine and session handling."""

from collections.abc import Generator

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from cache_service.config import settings


def _build_engine(url: str) -> Engine:
    # SQLite binds a connection to the thread that created it, which breaks the
    # threadpool FastAPI uses to run synchronous endpoints.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = _build_engine(settings.database_url)


def init_db() -> None:
    """Create missing tables.

    Adequate for a service with an append-only schema; a deployment that needs
    to evolve the schema would replace this with Alembic migrations.
    """
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
