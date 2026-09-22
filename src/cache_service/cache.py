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
    cached; repeated and previously seen values are served from the database.
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
    transformed = {value: transform(value) for value in values}
    session.add_all(
        TransformCache(source_digest=digests[value], source=value, transformed=result)
        for value, result in transformed.items()
    )
    try:
        # Committed on its own, ahead of the payload, so that expensive transformer
        # results are kept even if payload creation later fails.
        session.commit()
    except IntegrityError:
        # A concurrent request cached at least one of these values first. Its rows
        # are equivalent, so the results are still correct; the ones that did not
        # conflict are simply recomputed on a future request.
        session.rollback()
        logger.debug("transform cache write raced with a concurrent request")
    return transformed


def _chunked(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
