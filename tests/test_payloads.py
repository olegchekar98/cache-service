import itertools

import pytest
from sqlmodel import Session

from cache_service import payloads
from cache_service.models import Payload
from cache_service.payloads import get_or_create_payload, get_payload


def test_interleaves_the_transformed_lists(
    session: Session, sample_request: dict[str, list[str]], sample_output: str
) -> None:
    payload, created = get_or_create_payload(session, **sample_request)

    assert created
    assert payload.output == sample_output


def test_repeated_input_reuses_the_identifier(
    session: Session, sample_request: dict[str, list[str]], transformer_calls: list[str]
) -> None:
    first, _ = get_or_create_payload(session, **sample_request)
    transformer_calls.clear()

    second, created = get_or_create_payload(session, **sample_request)

    assert second.id == first.id
    assert not created
    assert transformer_calls == [], "a known payload must not reach the transformer"


def test_new_payloads_only_transform_unseen_strings(
    session: Session, transformer_calls: list[str]
) -> None:
    get_or_create_payload(session, ["one", "two"], ["three", "four"])
    transformer_calls.clear()

    get_or_create_payload(session, ["one", "five"], ["three", "six"])

    assert sorted(transformer_calls) == ["five", "six"]


def test_a_string_repeated_across_lists_is_transformed_once(
    session: Session, transformer_calls: list[str]
) -> None:
    payload, _ = get_or_create_payload(session, ["echo", "echo"], ["echo", "echo"])

    assert transformer_calls == ["echo"]
    assert payload.output == "ECHO, ECHO, ECHO, ECHO"


def test_stores_the_inputs_alongside_the_output(
    session: Session, sample_request: dict[str, list[str]]
) -> None:
    payload, _ = get_or_create_payload(session, **sample_request)

    assert payload.list_1 == sample_request["list_1"]
    assert payload.list_2 == sample_request["list_2"]


def test_get_payload_returns_none_for_an_unknown_identifier(session: Session) -> None:
    assert get_payload(session, "missing-id") is None


def test_concurrent_creation_settles_on_one_identifier(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing request that loses the unique constraint adopts the stored identifier."""
    winner, _ = get_or_create_payload(session, ["alpha"], ["beta"])

    find_by_digest = payloads._find_by_digest
    lookups = itertools.count()

    def miss_on_first_lookup(session: Session, content_digest: str) -> Payload | None:
        # Mimics a request that looked before the winner committed, then retried after.
        if next(lookups) == 0:
            return None
        return find_by_digest(session, content_digest)

    monkeypatch.setattr(payloads, "_find_by_digest", miss_on_first_lookup)

    loser, created = get_or_create_payload(session, ["alpha"], ["beta"])

    assert loser.id == winner.id
    assert not created
