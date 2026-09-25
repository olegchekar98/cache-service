"""Payload generation and deduplication."""

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service.cache import load_cached, store
from cache_service.hashing import digest_lists
from cache_service.models import Payload
from cache_service.transformer import TransformerClient

logger = logging.getLogger(__name__)

SEPARATOR = ", "


async def get_or_create_payload(
    session: AsyncSession,
    transformer: TransformerClient,
    list_1: Sequence[str],
    list_2: Sequence[str],
) -> tuple[Payload, bool]:
    """Return the payload for the given lists, and whether it was newly created.

    Inputs that have been seen before resolve to the stored payload, so the same
    identifier is handed out again and the transformer is not called at all.
    """
    content_digest = digest_lists(list_1, list_2)
    existing = await _find_by_digest(session, content_digest)
    if existing is not None:
        return existing, False

    interleaved = [value for pair in zip(list_1, list_2, strict=True) for value in pair]
    texts = await load_cached(session, interleaved)
    # Nothing has been written yet. Ending the read-only transaction returns the
    # connection to the pool, instead of holding it idle while the transformer runs.
    await session.commit()

    missing = [value for value in dict.fromkeys(interleaved) if value not in texts]
    if missing:
        texts |= await store(session, await transformer.transform_many(missing))

    payload = Payload(
        id=str(uuid.uuid4()),
        content_digest=content_digest,
        list_1=list(list_1),
        list_2=list(list_2),
        output=SEPARATOR.join(texts[value] for value in interleaved),
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


async def _find_by_digest(session: AsyncSession, content_digest: str) -> Payload | None:
    result = await session.exec(select(Payload).where(Payload.content_digest == content_digest))
    return result.first()
