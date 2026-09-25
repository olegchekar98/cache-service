import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from cache_service import database
from cache_service.database import build_engine, is_reachable


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
