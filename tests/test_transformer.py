import asyncio

import pytest

from cache_service import transformer
from cache_service.transformer import TransformerClient


class FakeService:
    """Replaces the transformer service: records calls and can hold them open."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.finished: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self.release = asyncio.Event()
        self.failing: set[str] = set()

    async def transform(self, value: str, latency_seconds: float = 0.0) -> str:
        self.calls.append(value)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if value in self.failing:
                raise RuntimeError(f"transformer rejected {value}")
            await self.release.wait()
            self.finished.append(value)
            return value.upper()
        finally:
            self.in_flight -= 1


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(transformer, "transform", fake.transform)
    return fake


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_transform_uppercases_input() -> None:
    assert await transformer.transform("first string") == "FIRST STRING"


async def test_transform_applies_the_configured_latency() -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()

    await transformer.transform("value", latency_seconds=0.05)

    assert loop.time() - started >= 0.05


async def test_transform_many_calls_once_per_distinct_value(service: FakeService) -> None:
    service.release.set()
    client = TransformerClient(max_concurrency=10)

    result = await client.transform_many(["alpha", "beta", "alpha"])

    assert result == {"alpha": "ALPHA", "beta": "BETA"}
    assert sorted(service.calls) == ["alpha", "beta"]


async def test_the_limit_applies_across_requests(service: FakeService) -> None:
    client = TransformerClient(max_concurrency=3)

    first = asyncio.create_task(client.transform_many([f"a{index}" for index in range(5)]))
    second = asyncio.create_task(client.transform_many([f"b{index}" for index in range(5)]))
    await _settle()

    assert service.in_flight == 3, "two requests must share one limit, not get three each"

    service.release.set()
    await asyncio.gather(first, second)
    assert service.peak == 3


async def test_concurrent_requests_share_a_call_for_the_same_value(service: FakeService) -> None:
    client = TransformerClient(max_concurrency=10)

    first = asyncio.create_task(client.transform_many(["shared", "one"]))
    second = asyncio.create_task(client.transform_many(["shared", "two"]))
    await _settle()
    service.release.set()

    assert await first == {"shared": "SHARED", "one": "ONE"}
    assert await second == {"shared": "SHARED", "two": "TWO"}
    assert sorted(service.calls) == ["one", "shared", "two"]


async def test_a_failure_cancels_the_other_calls(service: FakeService) -> None:
    service.failing.add("bad")
    client = TransformerClient(max_concurrency=10)

    with pytest.raises(RuntimeError, match="rejected bad"):
        await client.transform_many(["slow", "bad"])

    await _settle()
    assert service.in_flight == 0, "the slow call must be cancelled, not left running"
    assert service.finished == []


async def test_simultaneous_failures_are_all_reported(service: FakeService) -> None:
    service.failing.update({"bad", "worse"})
    client = TransformerClient(max_concurrency=10)

    with pytest.raises(ExceptionGroup) as raised:
        await client.transform_many(["bad", "worse"])

    assert len(raised.value.exceptions) == 2


async def test_a_departing_waiter_leaves_the_shared_call_running(service: FakeService) -> None:
    client = TransformerClient(max_concurrency=10)

    leaving = asyncio.create_task(client.transform_many(["shared"]))
    staying = asyncio.create_task(client.transform_many(["shared"]))
    await _settle()
    leaving.cancel()
    await _settle()
    service.release.set()

    assert await staying == {"shared": "SHARED"}
    assert service.calls == ["shared"]


async def test_a_call_nobody_waits_for_is_cancelled(service: FakeService) -> None:
    client = TransformerClient(max_concurrency=10)

    request = asyncio.create_task(client.transform_many(["orphan"]))
    await _settle()
    request.cancel()
    await _settle()

    assert service.in_flight == 0
    service.release.set()
    await _settle()
    assert service.finished == []
