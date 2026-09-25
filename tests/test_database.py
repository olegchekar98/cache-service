from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel

from cache_service import database
from cache_service.config import settings


def test_init_db_waits_for_an_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """A database that is still starting up must not crash the service."""
    monkeypatch.setattr(settings, "database_startup_timeout_seconds", 5)
    monkeypatch.setattr("cache_service.database.time.sleep", lambda _: None)
    attempts = 0

    def fail_once(*_: object, **__: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(SQLModel.metadata, "create_all", fail_once)

    database.init_db(MagicMock())

    assert attempts == 2


def test_init_db_gives_up_once_the_timeout_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "database_startup_timeout_seconds", 0)

    def always_fail(*_: object, **__: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(SQLModel.metadata, "create_all", always_fail)

    with pytest.raises(OperationalError):
        database.init_db(MagicMock())
