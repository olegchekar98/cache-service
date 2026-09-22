import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from cache_service.cache import _LOOKUP_CHUNK_SIZE, transform_all
from cache_service.models import TransformCache


def test_transforms_every_distinct_value_once(
    session: Session, transformer_calls: list[str]
) -> None:
    result = transform_all(session, ["alpha", "beta", "alpha"])

    assert result == {"alpha": "ALPHA", "beta": "BETA"}
    assert sorted(transformer_calls) == ["alpha", "beta"]


def test_reuses_cached_values_on_later_calls(
    session: Session, transformer_calls: list[str]
) -> None:
    transform_all(session, ["alpha", "beta"])
    transformer_calls.clear()

    result = transform_all(session, ["beta", "gamma"])

    assert result == {"beta": "BETA", "gamma": "GAMMA"}
    assert transformer_calls == ["gamma"], "cached values must not reach the transformer"


def test_persists_results_for_future_requests(session: Session) -> None:
    transform_all(session, ["alpha"])

    stored = session.exec(select(TransformCache)).all()

    assert [(row.source, row.transformed) for row in stored] == [("alpha", "ALPHA")]


def test_handles_more_values_than_one_lookup_chunk(
    session: Session, transformer_calls: list[str]
) -> None:
    """Lookups are chunked to stay under the bound-parameter limit of SQLite."""
    values = [f"value {index}" for index in range(_LOOKUP_CHUNK_SIZE * 2 + 1)]

    result = transform_all(session, values)

    assert len(result) == len(values)
    assert result[values[-1]] == values[-1].upper()
    assert len(transformer_calls) == len(values)


def test_returns_results_when_a_concurrent_request_cached_them_first(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate key from a racing writer must not fail the request."""
    commits = iter([IntegrityError("INSERT", {}, Exception("duplicate key"))])

    def commit_once_conflicting() -> None:
        raise next(commits)

    monkeypatch.setattr(session, "commit", commit_once_conflicting)

    assert transform_all(session, ["alpha"]) == {"alpha": "ALPHA"}
