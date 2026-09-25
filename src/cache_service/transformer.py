"""Client for the external string transformation service, and its stand-in."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


async def transform(value: str, latency_seconds: float = 0.0) -> str:
    """Stand-in for the remote service: uppercase the value after a simulated delay."""
    if latency_seconds:
        await asyncio.sleep(latency_seconds)
    logger.debug("transformer call for a %d-character string", len(value))
    return value.upper()


@dataclass
class _SharedCall:
    task: asyncio.Task[str]
    waiters: int = 0


class TransformerClient:
    """Process-wide access to the transformer.

    The concurrency limit applies to the whole process rather than per request,
    and concurrent requests for the same string share a single call.
    """

    def __init__(self, *, max_concurrency: int, latency_seconds: float = 0.0) -> None:
        self._limit = asyncio.Semaphore(max_concurrency)
        self._latency_seconds = latency_seconds
        self._calls: dict[str, _SharedCall] = {}

    async def transform_many(self, values: Sequence[str]) -> dict[str, str]:
        """Transform every distinct value; if one call fails, the others are cancelled.

        A single failure is raised as itself rather than wrapped in the
        ExceptionGroup that TaskGroup raises, so callers can catch its type.
        """
        try:
            async with asyncio.TaskGroup() as group:
                tasks = {
                    value: group.create_task(self._transform_shared(value))
                    for value in dict.fromkeys(values)
                }
        except ExceptionGroup as failures:
            if len(failures.exceptions) == 1:
                raise failures.exceptions[0] from None
            raise
        return {value: task.result() for value, task in tasks.items()}

    async def _transform_shared(self, value: str) -> str:
        call = self._calls.get(value)
        # A call whose last waiter left is being cancelled and cannot be joined.
        if call is None or call.task.cancelling():
            call = _SharedCall(asyncio.create_task(self._call(value)))
            self._calls[value] = call
            shared = call
            call.task.add_done_callback(lambda _: self._forget(value, shared))

        call.waiters += 1
        try:
            # shield: one waiter being cancelled must not cancel the call for the others.
            return await asyncio.shield(call.task)
        finally:
            call.waiters -= 1
            if call.waiters == 0:
                call.task.cancel()

    async def _call(self, value: str) -> str:
        async with self._limit:
            return await transform(value, self._latency_seconds)

    def _forget(self, value: str, call: _SharedCall) -> None:
        if self._calls.get(value) is call:
            del self._calls[value]
