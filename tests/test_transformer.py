import pytest

from cache_service import transformer
from cache_service.config import settings


def test_transform_uppercases_input() -> None:
    assert transformer.transform("first string") == "FIRST STRING"


def test_transform_applies_the_configured_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(settings, "transformer_latency_seconds", 0.25)
    monkeypatch.setattr("cache_service.transformer.time.sleep", slept.append)

    transformer.transform("value")

    assert slept == [0.25]
