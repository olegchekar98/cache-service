"""Payload generation and deduplication."""

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service.cache import transform_all
from cache_service.hashing import digest_lists
from cache_service.models import Payload

logger = logging.getLogger(__name__)

SEPARATOR = ", "


async def get_or_create_payload(
    session: AsyncSession, list_1: Sequence[str], list_2: Sequence[str]
) -> tuple[Payload, bool]:
    """Return the payload for the given lists, and whether it was newly created.

    Inputs that have been seen before resolve to the stored payload, so the same
    identifier is handed out again and the transformer is not called at all.
    """
    content_digest = digest_lists(list_1, list_2)
    existing = await _find_by_digest(session, content_digest)
    if existing is not None:
        return existing, False

    payload = Payload(
        id=str(uuid.uuid4()),
        content_digest=content_digest,
        list_1=list(list_1),
        list_2=list(list_2),
        output=await _render(session, list_1, list_2),
    )
    session.add(payload)
    try:
        await session.commit()
    except IntegrityError:
        # Two concurrent requests generated the same payload. The digest is unique,
        # so the identifier that was committed first wins and both callers receive it.
        await session.rollback()
        concurrent = await _find_by_digest(session, content_digest)
        if concurrent is None:
            raise
        logger.debug("payload creation raced with a concurrent request")
        return concurrent, False

    return payload, True


async def get_payload(session: AsyncSession, payload_id: str) -> Payload | None:
    return await session.get(Payload, payload_id)


async def _render(session: AsyncSession, list_1: Sequence[str], list_2: Sequence[str]) -> str:
    """Interleave the two lists after transforming every string exactly once."""
    interleaved = [value for pair in zip(list_1, list_2, strict=True) for value in pair]
    transformed = await transform_all(session, interleaved)
    return SEPARATOR.join(transformed[value] for value in interleaved)


async def _find_by_digest(session: AsyncSession, content_digest: str) -> Payload | None:
    result = await session.exec(select(Payload).where(Payload.content_digest == content_digest))
    return result.first()
