import itertools

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service import payloads
from cache_service.models import Payload
from cache_service.payloads import get_or_create_payload, get_payload


async def test_interleaves_the_transformed_lists(
    session: AsyncSession, sample_request: dict[str, list[str]], sample_output: str
) -> None:
    payload, created = await get_or_create_payload(session, **sample_request)

    assert created
    assert payload.output == sample_output


async def test_repeated_input_reuses_the_identifier(
    session: AsyncSession, sample_request: dict[str, list[str]], transformer_calls: list[str]
) -> None:
    first, _ = await get_or_create_payload(session, **sample_request)
    transformer_calls.clear()

    second, created = await get_or_create_payload(session, **sample_request)

    assert second.id == first.id
    assert not created
    assert transformer_calls == [], "a known payload must not reach the transformer"


async def test_new_payloads_only_transform_unseen_strings(
    session: AsyncSession, transformer_calls: list[str]
) -> None:
    await get_or_create_payload(session, ["one", "two"], ["three", "four"])
    transformer_calls.clear()

    await get_or_create_payload(session, ["one", "five"], ["three", "six"])

    assert sorted(transformer_calls) == ["five", "six"]


async def test_a_string_repeated_across_lists_is_transformed_once(
    session: AsyncSession, transformer_calls: list[str]
) -> None:
    payload, _ = await get_or_create_payload(session, ["echo", "echo"], ["echo", "echo"])

    assert transformer_calls == ["echo"]
    assert payload.output == "ECHO, ECHO, ECHO, ECHO"


async def test_stores_the_inputs_alongside_the_output(
    session: AsyncSession, sample_request: dict[str, list[str]]
) -> None:
    payload, _ = await get_or_create_payload(session, **sample_request)

    assert payload.list_1 == sample_request["list_1"]
    assert payload.list_2 == sample_request["list_2"]


async def test_get_payload_returns_none_for_an_unknown_identifier(session: AsyncSession) -> None:
    assert await get_payload(session, "missing-id") is None


async def test_concurrent_creation_settles_on_one_identifier(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing request that loses the unique constraint adopts the stored identifier."""
    winner, _ = await get_or_create_payload(session, ["alpha"], ["beta"])
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

    loser, created = await get_or_create_payload(session, ["alpha"], ["beta"])

    assert loser.id == winner_id
    assert not created
