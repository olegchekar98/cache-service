"""Shared fixtures.

Tests build the app from their own ``Settings`` and engine, so nothing depends
on the developer's environment or on objects created at import time.

The suite runs on in-memory SQLite, or on ``CACHE_SERVICE_TEST_DATABASE_URL``
when set. The API client runs the app on its own event loop, so API tests let
the app's lifespan create the tables; ``session`` creates them on the test's loop.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.pool import StaticPool

from cache_service import transformer as transformer_module
from cache_service.config import Settings
from cache_service.database import build_engine
from cache_service.main import create_app
from cache_service.transformer import TransformerClient

SERVER_DATABASE_URL = os.environ.get("CACHE_SERVICE_TEST_DATABASE_URL")

requires_server_database = pytest.mark.skipif(
    SERVER_DATABASE_URL is None,
    reason="needs CACHE_SERVICE_TEST_DATABASE_URL: in-memory SQLite has a single connection",
)

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
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]  # _env_file is a pydantic-settings hook
        _env_file=None,
        database_url="sqlite+aiosqlite://",
        transformer_latency_seconds=0,
        transformer_max_concurrency=10,
    )


@pytest.fixture
def engine() -> AsyncEngine:
    """An in-memory SQLite engine, or the server database, reset for this test.

    NullPool keeps server connections from being shared between the event loops
    of the test and of the API client.
    """
    if SERVER_DATABASE_URL is None:
        return build_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    engine = build_engine(SERVER_DATABASE_URL, poolclass=NullPool)
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
def transformer() -> TransformerClient:
    return TransformerClient(max_concurrency=10)


@pytest.fixture
def app(settings: Settings, engine: AsyncEngine) -> FastAPI:
    return create_app(settings, engine)


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
    """Record every call that reaches the transformer service."""
    calls: list[str] = []
    transform = transformer_module.transform

    async def recording_transform(value: str, latency_seconds: float = 0.0) -> str:
        calls.append(value)
        return await transform(value, latency_seconds)

    monkeypatch.setattr(transformer_module, "transform", recording_transform)
    return calls
