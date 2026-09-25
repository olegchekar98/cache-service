import pytest

from cache_service import transformer
from cache_service.config import settings


async def test_transform_uppercases_input() -> None:
    assert await transformer.transform("first string") == "FIRST STRING"


async def test_transform_applies_the_configured_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(settings, "transformer_latency_seconds", 0.25)
    monkeypatch.setattr(transformer, "sleep", record_sleep)

    await transformer.transform("value")

    assert slept == [0.25]
