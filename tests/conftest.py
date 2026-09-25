"""Shared fixtures.

Tests pass their engine into ``create_app``, so lifespan, handlers and the
transformer cache all use the same bind. StaticPool keeps every connection on
one in-memory database, which FastAPI's worker threads need for SQLite.
"""

import os
from collections.abc import Iterator

# Force these: setdefault would keep a developer shell's non-zero transformer delay
# and make the large-batch test look hung (1000 * 200ms).
os.environ["CACHE_SERVICE_DATABASE_URL"] = "sqlite://"
os.environ["CACHE_SERVICE_TRANSFORMER_LATENCY_SECONDS"] = "0"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cache_service import cache
from cache_service.config import settings
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
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def app(engine: Engine) -> FastAPI:
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

    def recording_transform(value: str) -> str:
        calls.append(value)
        return transform(value)

    monkeypatch.setattr(cache, "transform", recording_transform)
    return calls
