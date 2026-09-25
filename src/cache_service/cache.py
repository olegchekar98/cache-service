"""Persistent cache of transformer results.

Reading and writing are separate steps so the caller can release its database
connection while the transformer runs. Neither step commits or rolls back: the
transaction belongs to the caller.
"""

import logging
from collections.abc import Iterable, Iterator, Mapping

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service.hashing import digest_text
from cache_service.models import TransformCache

logger = logging.getLogger(__name__)

# SQLite allows a limited number of bound parameters per statement (999 on older
# builds), so lookups are issued in chunks rather than one huge IN clause.
_LOOKUP_CHUNK_SIZE = 500


async def load_cached(session: AsyncSession, values: Iterable[str]) -> dict[str, str]:
    """Return the stored transformation of every value that has one."""
    return await _load(session, {value: digest_text(value) for value in dict.fromkeys(values)})


async def store(session: AsyncSession, transformed: Mapping[str, str]) -> dict[str, str]:
    """Insert transformer results and return the text stored for each value.

    A concurrent writer may already have stored some of the values. After a unique
    conflict the savepoint is rolled back, those rows are read back and win, and
    only the values still missing are inserted again.
    """
    digests = {value: digest_text(value) for value in transformed}
    stored = dict(transformed)
    # Every writer inserts in digest order, so two transactions storing overlapping
    # values lock the rows in the same order and cannot deadlock each other.
    pending = sorted(transformed, key=digests.__getitem__)

    while pending:
        rows = [
            TransformCache(source_digest=digests[value], source=value, transformed=stored[value])
            for value in pending
        ]
        try:
            # A savepoint rather than session.rollback(), which would also discard
            # the caller's work. flush(rows) keeps autoflush from pulling the
            # caller's pending objects into the savepoint and dropping them with it.
            async with session.begin_nested():
                session.add_all(rows)
                await session.flush(rows)
        except IntegrityError:
            winners = await _load(session, {value: digests[value] for value in pending})
            if not winners:
                # The conflict was not on one of our keys; retrying would loop forever.
                raise
            stored.update(winners)
            pending = [value for value in pending if value not in winners]
            logger.debug("transform cache raced; %d values still unstored", len(pending))
            continue
        break

    return stored


async def _load(session: AsyncSession, digests: dict[str, str]) -> dict[str, str]:
    by_digest: dict[str, str] = {}
    for chunk in _chunked(list(digests.values()), _LOOKUP_CHUNK_SIZE):
        statement = select(TransformCache).where(col(TransformCache.source_digest).in_(chunk))
        rows = await session.exec(statement)
        by_digest.update({row.source_digest: row.transformed for row in rows})
    return {value: by_digest[digest] for value, digest in digests.items() if digest in by_digest}


def _chunked(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
