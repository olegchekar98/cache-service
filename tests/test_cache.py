import asyncio

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service import cache
from cache_service.cache import _LOOKUP_CHUNK_SIZE, transform_all
from cache_service.config import settings
from cache_service.hashing import digest_text
from cache_service.models import Payload, TransformCache


async def _store(session: AsyncSession, value: str, transformed: str | None = None) -> None:
    session.add(
        TransformCache(
            source_digest=digest_text(value),
            source=value,
            transformed=transformed if transformed is not None else value.upper(),
        )
    )
    await session.commit()


async def _rows(session: AsyncSession) -> dict[str, str]:
    rows = await session.exec(select(TransformCache))
    return {row.source: row.transformed for row in rows.all()}


def _miss_the_first_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the first cache lookup miss, as for a request that looked before a
    concurrent writer committed and then reads again after the conflict."""
    load = cache._load_cached
    first_lookup = True

    async def lookup(session: AsyncSession, digests: dict[str, str]) -> dict[str, str]:
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return {}
        return await load(session, digests)

    monkeypatch.setattr(cache, "_load_cached", lookup)


async def test_transforms_every_distinct_value_once(
    session: AsyncSession, transformer_calls: list[str]
) -> None:
    result = await transform_all(session, ["alpha", "beta", "alpha"])

    assert result == {"alpha": "ALPHA", "beta": "BETA"}
    assert sorted(transformer_calls) == ["alpha", "beta"]


async def test_reuses_cached_values_on_later_calls(
    session: AsyncSession, transformer_calls: list[str]
) -> None:
    await transform_all(session, ["alpha", "beta"])
    transformer_calls.clear()

    result = await transform_all(session, ["beta", "gamma"])

    assert result == {"beta": "BETA", "gamma": "GAMMA"}
    assert transformer_calls == ["gamma"], "cached values must not reach the transformer"


async def test_persists_results_for_future_requests(session: AsyncSession) -> None:
    await transform_all(session, ["alpha"])
    await session.commit()

    assert await _rows(session) == {"alpha": "ALPHA"}


async def test_cache_rows_commit_with_the_caller_transaction(session: AsyncSession) -> None:
    """On a server database the savepoint nests in the caller's transaction."""
    if session.bind.dialect.name == "sqlite":
        pytest.skip("SQLite's driver commits a savepoint opened outside a transaction")
    await transform_all(session, ["alpha"])
    await session.rollback()

    assert await _rows(session) == {}


async def test_handles_more_values_than_one_lookup_chunk(
    session: AsyncSession, transformer_calls: list[str]
) -> None:
    """Lookups are chunked to stay under the bound-parameter limit of SQLite."""
    values = [f"value {index}" for index in range(_LOOKUP_CHUNK_SIZE * 2 + 1)]

    result = await transform_all(session, values)

    assert len(result) == len(values)
    assert result[values[-1]] == values[-1].upper()
    assert len(transformer_calls) == len(values)


async def test_transformer_calls_overlap_up_to_the_limit(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "transformer_max_concurrency", 3)
    in_flight = 0
    peak = 0

    async def slow_transform(value: str) -> str:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return value.upper()

    monkeypatch.setattr(cache, "transform", slow_transform)

    result = await transform_all(session, [f"value {index}" for index in range(10)])

    assert len(result) == 10
    assert peak == 3


async def test_insert_race_rereads_the_winner_and_keeps_the_rest(
    session: AsyncSession,
    transformer_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conflict on one row must not drop the rest of the batch or skip a re-read."""
    await _store(session, "alpha")
    _miss_the_first_lookup(monkeypatch)

    # The winner's row is in the database, not in this session: the same as a
    # concurrent request that committed after our first lookup.
    session.expunge_all()

    result = await transform_all(session, ["alpha", "beta"])
    await session.commit()

    assert result == {"alpha": "ALPHA", "beta": "BETA"}
    assert await _rows(session) == {"alpha": "ALPHA", "beta": "BETA"}
    assert transformer_calls == ["alpha", "beta"]

    transformer_calls.clear()
    assert await transform_all(session, ["alpha", "beta"]) == {"alpha": "ALPHA", "beta": "BETA"}
    assert transformer_calls == [], "persisted leftovers must not hit the transformer again"


async def test_cache_write_does_not_finish_the_caller_transaction(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache savepoint rollback must not expunge or commit the caller's pending work."""
    await _store(session, "alpha")
    session.expunge_all()

    pending = Payload(
        id="pending-id",
        content_digest="pending-digest",
        list_1=["x"],
        list_2=["y"],
        output="X, Y",
    )
    session.add(pending)
    _miss_the_first_lookup(monkeypatch)

    await transform_all(session, ["alpha"])

    assert pending in session, "a cache conflict must not expunge the caller's pending work"
    await session.commit()
    assert await session.get(Payload, "pending-id") is not None
