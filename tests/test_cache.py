import pytest
from sqlmodel import Session, select

from cache_service import cache
from cache_service.cache import _LOOKUP_CHUNK_SIZE, transform_all
from cache_service.hashing import digest_text
from cache_service.models import Payload, TransformCache


def _store(session: Session, value: str, transformed: str | None = None) -> None:
    session.add(
        TransformCache(
            source_digest=digest_text(value),
            source=value,
            transformed=transformed if transformed is not None else value.upper(),
        )
    )
    session.commit()


def _rows(session: Session) -> dict[str, str]:
    return {row.source: row.transformed for row in session.exec(select(TransformCache)).all()}


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
    session.commit()

    assert _rows(session) == {"alpha": "ALPHA"}


def test_handles_more_values_than_one_lookup_chunk(
    session: Session, transformer_calls: list[str]
) -> None:
    """Lookups are chunked to stay under the bound-parameter limit of SQLite."""
    values = [f"value {index}" for index in range(_LOOKUP_CHUNK_SIZE * 2 + 1)]

    result = transform_all(session, values)

    assert len(result) == len(values)
    assert result[values[-1]] == values[-1].upper()
    assert len(transformer_calls) == len(values)


def test_insert_race_rereads_the_winner_and_keeps_the_rest(
    session: Session,
    transformer_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conflict on one row must not drop the rest of the batch or skip a re-read."""
    _store(session, "alpha")

    load = cache._load_cached
    first_lookup = True

    def miss_the_first_lookup(session: Session, digests: dict[str, str]) -> dict[str, str]:
        # The loser looked before the winner committed, then reads again after the conflict.
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return {}
        return load(session, digests)

    monkeypatch.setattr(cache, "_load_cached", miss_the_first_lookup)

    # The winner's row is in the database, not in this session: the same as a
    # concurrent request that committed after our first lookup.
    session.expunge_all()

    result = transform_all(session, ["alpha", "beta"])
    session.commit()

    assert result == {"alpha": "ALPHA", "beta": "BETA"}
    assert _rows(session) == {"alpha": "ALPHA", "beta": "BETA"}
    assert transformer_calls == ["alpha", "beta"]

    transformer_calls.clear()
    assert transform_all(session, ["alpha", "beta"]) == {"alpha": "ALPHA", "beta": "BETA"}
    assert transformer_calls == [], "persisted leftovers must not hit the transformer again"


def test_cache_write_does_not_finish_the_caller_transaction(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache savepoint rollback must not expunge or commit the caller's pending work."""
    _store(session, "alpha")
    session.expunge_all()

    pending = Payload(
        id="pending-id",
        content_digest="pending-digest",
        list_1=["x"],
        list_2=["y"],
        output="X, Y",
    )
    session.add(pending)

    load = cache._load_cached
    first_lookup = True

    def miss_the_first_lookup(session: Session, digests: dict[str, str]) -> dict[str, str]:
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return {}
        return load(session, digests)

    monkeypatch.setattr(cache, "_load_cached", miss_the_first_lookup)

    transform_all(session, ["alpha"])

    assert pending in session, "a cache conflict must not expunge the caller's pending work"
    session.commit()
    assert session.get(Payload, "pending-id") is not None
