import asyncio
import logging
import os
from typing import Any, cast

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from cache_service import database
from cache_service.database import build_engine, is_reachable
from conftest import requires_server_database


class UnresponsiveEngine:
    """Stands in for an engine whose host accepts packets but never answers."""

    def connect(self) -> "UnresponsiveEngine":
        return self

    async def __aenter__(self) -> None:
        await asyncio.Event().wait()

    async def __aexit__(self, *_: Any) -> None:
        return None


def _missing_database_engine() -> AsyncEngine:
    """The configured server, but a database that does not exist on it."""
    url = make_url(os.environ["CACHE_SERVICE_TEST_DATABASE_URL"])
    return build_engine(url.set(database="no_such_database").render_as_string(hide_password=False))


# `session` disposes of the engine once the test is done.
@pytest.mark.usefixtures("session")
async def test_init_db_waits_for_an_unreachable_database(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database that is still starting up must not crash the service."""
    monkeypatch.setattr(database, "_RETRY_INTERVAL_SECONDS", 0)
    attempts = 0

    def fail_once(*_: object, **__: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(SQLModel.metadata, "create_all", fail_once)

    await database.init_db(engine, timeout_seconds=5)

    assert attempts == 2


@pytest.mark.usefixtures("session")
async def test_init_db_gives_up_once_the_timeout_passes(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_fail(*_: object, **__: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(SQLModel.metadata, "create_all", always_fail)

    with pytest.raises(OSError, match="connection refused"):
        await database.init_db(engine, timeout_seconds=0)


@pytest.mark.usefixtures("session")
async def test_a_working_database_is_reachable(engine: AsyncEngine) -> None:
    assert await is_reachable(engine)


async def test_an_unopenable_database_is_not_reachable() -> None:
    engine = build_engine("sqlite+aiosqlite:////nonexistent-directory/cache.db")

    assert not await is_reachable(engine)
    await engine.dispose()


async def test_an_unresponsive_database_fails_the_check_quickly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(database, "_READY_TIMEOUT_SECONDS", 0.05)

    async with asyncio.timeout(1):
        assert not await is_reachable(cast(AsyncEngine, UnresponsiveEngine()))

    [record] = [r for r in caplog.records if r.name == "cache_service.database"]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "readiness check failed: TimeoutError"
    assert record.exc_info is None, "a repeated health check must not log a traceback"


@requires_server_database
async def test_a_missing_server_database_is_not_reachable() -> None:
    """Server errors raised while connecting are not translated by SQLAlchemy."""
    engine = _missing_database_engine()

    assert not await is_reachable(engine)
    await engine.dispose()


@requires_server_database
async def test_init_db_retries_server_errors_raised_while_connecting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Such as "the database system is starting up" while a container initialises."""
    from asyncpg import InvalidCatalogNameError

    monkeypatch.setattr(database, "_RETRY_INTERVAL_SECONDS", 0.05)
    engine = _missing_database_engine()

    with pytest.raises(InvalidCatalogNameError):
        await database.init_db(engine, timeout_seconds=0.2)

    retries = [r for r in caplog.records if "retrying" in r.getMessage()]
    assert retries, "a server error must be retried until the timeout, not raised at once"
    await engine.dispose()
