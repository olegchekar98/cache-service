"""Shared fixtures.

The database URL is pinned before the application is imported: the engine is
created at import time, and tests must never touch a real database file.
"""

import os
from collections.abc import Iterator

os.environ["CACHE_SERVICE_DATABASE_URL"] = "sqlite://"
os.environ["CACHE_SERVICE_TRANSFORMER_LATENCY_SECONDS"] = "0"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cache_service import cache
from cache_service.database import get_session
from cache_service.main import app
from cache_service.transformer import transform

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
def session() -> Iterator[Session]:
    """A session on an empty in-memory database, shared by every connection."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """A client wired to the app over ASGI.

    It subclasses httpx.Client, so it also stands in for the CLI's HTTP client
    and lets the CLI be exercised end to end without a running server.
    """
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def transformer_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every transformer invocation so cache behaviour can be asserted."""
    calls: list[str] = []

    def recording_transform(value: str) -> str:
        calls.append(value)
        return transform(value)

    monkeypatch.setattr(cache, "transform", recording_transform)
    return calls
