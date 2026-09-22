"""Stand-in for the external string transformation service."""

import logging
import time

from cache_service.config import settings

logger = logging.getLogger(__name__)


def transform(value: str) -> str:
    """Transform a single string, simulating a call to a remote service.

    Callers go through :func:`cache_service.cache.transform_all` so that every
    distinct string is sent to the service at most once.
    """
    if settings.transformer_latency_seconds:
        time.sleep(settings.transformer_latency_seconds)
    logger.info("transformer call for %r", value)
    return value.upper()
