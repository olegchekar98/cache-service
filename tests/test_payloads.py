import asyncio
import itertools
from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service import payloads
from cache_service import transformer as transformer_module
from cache_service.models import Payload
from cache_service.payloads import get_or_create_payload, get_payload
from cache_service.transformer import TransformerClient
from conftest import requires_server_database


async def test_interleaves_the_transformed_lists(
    session: AsyncSession,
    transformer: TransformerClient,
    sample_request: dict[str, list[str]],
    sample_output: str,
) -> None:
    payload, created = await get_or_create_payload(session, transformer, **sample_request)

    assert created
    assert payload.output == sample_output


async def test_repeated_input_reuses_the_identifier(
    session: AsyncSession,
    transformer: TransformerClient,
    sample_request: dict[str, list[str]],
    transformer_calls: list[str],
) -> None:
    first, _ = await get_or_create_payload(session, transformer, **sample_request)
    transformer_calls.clear()

    second, created = await get_or_create_payload(session, transformer, **sample_request)

    assert second.id == first.id
    assert not created
    assert transformer_calls == [], "a known payload must not reach the transformer"


async def test_new_payloads_only_transform_unseen_strings(
    session: AsyncSession, transformer: TransformerClient, transformer_calls: list[str]
) -> None:
    await get_or_create_payload(session, transformer, ["one", "two"], ["three", "four"])
    transformer_calls.clear()

    await get_or_create_payload(session, transformer, ["one", "five"], ["three", "six"])

    assert sorted(transformer_calls) == ["five", "six"]


async def test_a_string_repeated_across_lists_is_transformed_once(
    session: AsyncSession, transformer: TransformerClient, transformer_calls: list[str]
) -> None:
    payload, _ = await get_or_create_payload(
        session, transformer, ["echo", "echo"], ["echo", "echo"]
    )

    assert transformer_calls == ["echo"]
    assert payload.output == "ECHO, ECHO, ECHO, ECHO"


async def test_stores_the_inputs_alongside_the_output(
    session: AsyncSession, transformer: TransformerClient, sample_request: dict[str, list[str]]
) -> None:
    payload, _ = await get_or_create_payload(session, transformer, **sample_request)

    assert payload.list_1 == sample_request["list_1"]
    assert payload.list_2 == sample_request["list_2"]


async def test_no_transaction_is_open_while_the_transformer_runs(
    session: AsyncSession, transformer: TransformerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow transformer must not hold a pooled connection idle in a transaction."""
    open_during_call: list[bool] = []

    async def observing_transform(value: str, latency_seconds: float = 0.0) -> str:
        open_during_call.append(session.in_transaction())
        return value.upper()

    monkeypatch.setattr(transformer_module, "transform", observing_transform)

    await get_or_create_payload(session, transformer, ["alpha"], ["beta"])

    assert open_during_call == [False, False]


async def test_get_payload_returns_none_for_an_unknown_identifier(session: AsyncSession) -> None:
    assert await get_payload(session, "missing-id") is None


async def test_a_losing_request_adopts_the_stored_identifier(
    session: AsyncSession, transformer: TransformerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing request that loses the unique constraint adopts the stored identifier."""
    winner, _ = await get_or_create_payload(session, transformer, ["alpha"], ["beta"])
    winner_id = winner.id
    session.expunge_all()

    find_by_digest = payloads._find_by_digest
    lookups = itertools.count()

    async def miss_on_first_lookup(session: AsyncSession, content_digest: str) -> Payload | None:
        # Mimics a request that looked before the winner committed, then retried after.
        if next(lookups) == 0:
            return None
        return await find_by_digest(session, content_digest)

    monkeypatch.setattr(payloads, "_find_by_digest", miss_on_first_lookup)

    loser, created = await get_or_create_payload(session, transformer, ["alpha"], ["beta"])

    assert loser.id == winner_id
    assert not created


class WaitingClient(TransformerClient):
    """Counts the requests that have reached the transformer."""

    def __init__(self) -> None:
        super().__init__(max_concurrency=1_000)
        self.waiting = 0

    async def transform_many(self, values: Sequence[str]) -> dict[str, str]:
        self.waiting += 1
        return await super().transform_many(values)


@requires_server_database
async def test_concurrent_identical_requests_settle_on_one_identifier(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real concurrency: independent sessions, one transformer client per process.

    Calls are held open until every request waits on them, so the requests overlap
    however slowly their connections open.
    """
    requests = 5
    list_1 = [f"left {index}" for index in range(50)]
    list_2 = [f"right {index}" for index in range(50)]
    calls: list[str] = []
    release = asyncio.Event()

    async def held_transform(value: str, latency_seconds: float = 0.0) -> str:
        calls.append(value)
        await release.wait()
        return value.upper()

    monkeypatch.setattr(transformer_module, "transform", held_transform)
    transformer = WaitingClient()

    async def request() -> tuple[str, bool]:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            payload, created = await get_or_create_payload(session, transformer, list_1, list_2)
            return payload.id, created

    pending = asyncio.gather(*(request() for _ in range(requests)))
    async with asyncio.timeout(10):
        while transformer.waiting < requests or len(calls) < len(list_1) + len(list_2):
            await asyncio.sleep(0.01)
    # The last request's tasks are created but join their shared calls on the next turns.
    for _ in range(3):
        await asyncio.sleep(0)
    release.set()
    results = await pending

    assert len({identifier for identifier, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert sorted(calls) == sorted(list_1 + list_2), "each string once across all requests"
    await engine.dispose()
