"""Shared fixtures.

Tests pass their engine into ``create_app``, so lifespan, handlers and the
transformer cache all use the same bind. StaticPool keeps every connection on
one in-memory database.

The API client runs the app on its own event loop, so API tests let the app's
lifespan create the tables; ``session`` creates them on the test's loop instead.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

# Force these: setdefault would keep a developer shell's non-zero transformer delay
# and make the large-batch test look hung.
os.environ["CACHE_SERVICE_DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["CACHE_SERVICE_TRANSFORMER_LATENCY_SECONDS"] = "0"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.pool import StaticPool

from cache_service import cache
from cache_service.config import settings
from cache_service.database import build_engine
from cache_service.main import create_app
from cache_service.transformer import transform

settings.transformer_latency_seconds = 0.0

# The example from the task description, used across the suite.
SAMPLE_LIST_1 = ["first string", "second string", "third string"]
SAMPLE_LIST_2 = ["other string", "another string", "last string"]
SAMPLE_OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
)


@pytest.fixture
def sample_request() -> dict[str, list[str]]:
    return {"list_1": SAMPLE_LIST_1, "list_2": SAMPLE_LIST_2}


@pytest.fixture
def sample_output() -> str:
    return SAMPLE_OUTPUT


@pytest.fixture
def engine() -> AsyncEngine:
    """An in-memory SQLite engine, or ``CACHE_SERVICE_TEST_DATABASE_URL`` when set.

    A server database is reset per test. NullPool keeps connections from being
    shared between the event loops of the test and of the API client.
    """
    url = os.environ.get("CACHE_SERVICE_TEST_DATABASE_URL")
    if url is None:
        return build_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    engine = build_engine(url, poolclass=NullPool)
    asyncio.run(_reset_schema(engine))
    return engine


async def _reset_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.drop_all)
        await connection.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.fixture
def app(engine: AsyncEngine) -> FastAPI:
    return create_app(engine=engine)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A client wired to the app over ASGI.

    It subclasses httpx.Client, so it also stands in for the CLI's HTTP client
    and lets the CLI be exercised end to end without a running server.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def transformer_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every transformer invocation so cache behaviour can be asserted."""
    calls: list[str] = []

    async def recording_transform(value: str) -> str:
        calls.append(value)
        return await transform(value)

    monkeypatch.setattr(cache, "transform", recording_transform)
    return calls
