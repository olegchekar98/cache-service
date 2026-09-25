import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service.cache import _LOOKUP_CHUNK_SIZE, load_cached, store
from cache_service.hashing import digest_text
from cache_service.models import Payload, TransformCache
from conftest import requires_server_database


async def _commit_as_another_writer(session: AsyncSession, value: str, transformed: str) -> None:
    """Store a row, then forget it, so this session only finds it in the database."""
    session.add(
        TransformCache(source_digest=digest_text(value), source=value, transformed=transformed)
    )
    await session.commit()
    session.expunge_all()


async def _rows(session: AsyncSession) -> dict[str, str]:
    rows = await session.exec(select(TransformCache))
    return {row.source: row.transformed for row in rows.all()}


async def test_load_cached_returns_only_stored_values(session: AsyncSession) -> None:
    await _commit_as_another_writer(session, "alpha", "ALPHA")

    assert await load_cached(session, ["alpha", "beta", "alpha"]) == {"alpha": "ALPHA"}


async def test_stored_results_are_found_by_later_lookups(session: AsyncSession) -> None:
    await store(session, {"alpha": "ALPHA", "beta": "BETA"})
    await session.commit()

    assert await _rows(session) == {"alpha": "ALPHA", "beta": "BETA"}
    assert await load_cached(session, ["beta"]) == {"beta": "BETA"}


async def test_lookups_span_more_than_one_chunk(session: AsyncSession) -> None:
    """Lookups are chunked to stay under the bound-parameter limit of SQLite."""
    values = [f"value {index}" for index in range(_LOOKUP_CHUNK_SIZE * 2 + 1)]
    await store(session, {value: value.upper() for value in values})
    await session.commit()

    cached = await load_cached(session, values)

    assert len(cached) == len(values)
    assert cached[values[-1]] == values[-1].upper()


async def test_a_conflict_keeps_the_stored_row_and_inserts_the_rest(
    session: AsyncSession,
) -> None:
    """The row a concurrent writer committed wins; the rest of the batch is not lost."""
    await _commit_as_another_writer(session, "alpha", "ALPHA from the winner")

    stored = await store(session, {"alpha": "ALPHA", "beta": "BETA"})
    await session.commit()

    assert stored == {"alpha": "ALPHA from the winner", "beta": "BETA"}
    assert await _rows(session) == {"alpha": "ALPHA from the winner", "beta": "BETA"}


async def test_a_conflict_does_not_touch_the_caller_transaction(session: AsyncSession) -> None:
    """A savepoint rollback must not expunge or commit the caller's pending work."""
    await _commit_as_another_writer(session, "alpha", "ALPHA")
    pending = Payload(
        id="pending-id", content_digest="pending-digest", list_1=["x"], list_2=["y"], output="X, Y"
    )
    session.add(pending)

    await store(session, {"alpha": "ALPHA"})

    assert pending in session, "a cache conflict must not expunge the caller's pending work"
    await session.commit()
    assert await session.get(Payload, "pending-id") is not None


@requires_server_database
async def test_cache_rows_commit_with_the_caller_transaction(session: AsyncSession) -> None:
    await store(session, {"alpha": "ALPHA"})
    await session.rollback()

    assert await _rows(session) == {}


@requires_server_database
async def test_overlapping_writers_in_opposite_order_do_not_deadlock(
    engine: AsyncEngine,
) -> None:
    """Two transactions storing the same values, listed in opposite orders."""
    values = [f"value {index}" for index in range(200)]
    both_connected = asyncio.Barrier(2)

    async def write(order: list[str]) -> dict[str, str]:
        async with AsyncSession(engine, expire_on_commit=False) as writer:
            await load_cached(writer, order[:1])  # open the transaction first
            await both_connected.wait()
            stored = await store(writer, {value: value.upper() for value in order})
            await writer.commit()
            return stored

    first, second = await asyncio.gather(write(values), write(values[::-1]))

    assert first == second == {value: value.upper() for value in values}
    async with AsyncSession(engine) as reader:
        assert len(await load_cached(reader, values)) == len(values)
    await engine.dispose()
