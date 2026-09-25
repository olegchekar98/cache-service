"""Persistent cache in front of the transformer service."""

import logging
from collections.abc import Iterator, Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from cache_service.hashing import digest_text
from cache_service.models import TransformCache
from cache_service.transformer import transform

logger = logging.getLogger(__name__)

# SQLite allows a limited number of bound parameters per statement (999 on older
# builds), so lookups are issued in chunks rather than one huge IN clause.
_LOOKUP_CHUNK_SIZE = 500


def transform_all(session: Session, values: Sequence[str]) -> dict[str, str]:
    """Return the transformed text for every value, keyed by the original string.

    The transformer is called once per distinct value that is not already
    stored. Writes go through a savepoint so a unique conflict rolls back only
    the cache insert, not the caller's payload transaction.
    """
    digests = {value: digest_text(value) for value in dict.fromkeys(values)}
    results = _load_cached(session, digests)
    missing = [value for value in digests if value not in results]
    if missing:
        results.update(_transform_and_cache(session, missing, digests))
    logger.debug(
        "%d distinct values, %d served from cache", len(digests), len(results) - len(missing)
    )
    return results


def _load_cached(session: Session, digests: dict[str, str]) -> dict[str, str]:
    by_digest: dict[str, str] = {}
    for chunk in _chunked(list(digests.values()), _LOOKUP_CHUNK_SIZE):
        statement = select(TransformCache).where(col(TransformCache.source_digest).in_(chunk))
        by_digest.update({row.source_digest: row.transformed for row in session.exec(statement)})
    return {value: by_digest[digest] for value, digest in digests.items() if digest in by_digest}


def _transform_and_cache(
    session: Session, values: Sequence[str], digests: dict[str, str]
) -> dict[str, str]:
    """Persist transformer results, retrying only the rows that are still missing.

    A concurrent writer may have stored a subset of this batch. After a unique
    conflict the savepoint is rolled back, the winner's rows are re-read, and
    only the leftovers are inserted — without calling the transformer again.
    """
    results: dict[str, str] = {}
    pending = list(values)

    while pending:
        for value in pending:
            if value not in results:
                results[value] = transform(value)

        try:
            # begin_nested is a SAVEPOINT: failure here must not session.rollback()
            # the caller's payload work sitting in the same session. flush(rows)
            # is required so autoflush does not write those pending caller objects
            # into this savepoint and then drop them on rollback.
            rows = [
                TransformCache(
                    source_digest=digests[value],
                    source=value,
                    transformed=results[value],
                )
                for value in pending
            ]
            with session.begin_nested():
                session.add_all(rows)
                session.flush(rows)
        except IntegrityError:
            cached = _load_cached(session, {value: digests[value] for value in pending})
            remaining = [value for value in pending if value not in cached]
            if len(remaining) == len(pending):
                # The conflict was not one of our keys; retrying would loop forever.
                raise
            results.update(cached)
            pending = remaining
            logger.debug("transform cache raced; %d values still unstored", len(pending))
            continue
        break

    return results


def _chunked(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
