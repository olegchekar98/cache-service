"""Database tables."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TransformCache(SQLModel, table=True):
    """One cached transformer result.

    The primary key is a digest rather than the raw input so that arbitrarily
    long strings stay within the index size limits of engines like PostgreSQL.
    """

    __tablename__ = "transform_cache"

    source_digest: str = Field(primary_key=True, max_length=64)
    source: str
    transformed: str
    created_at: datetime = Field(default_factory=_utcnow)


class Payload(SQLModel, table=True):
    """A generated payload, deduplicated on a digest of its two input lists.

    The inputs are stored next to the output so a payload can be traced back to
    the request that produced it.
    """

    __tablename__ = "payload"

    id: str = Field(primary_key=True, max_length=36)
    content_digest: str = Field(unique=True, index=True, max_length=64)
    list_1: list[str] = Field(sa_column=Column(JSON, nullable=False))
    list_2: list[str] = Field(sa_column=Column(JSON, nullable=False))
    output: str
    created_at: datetime = Field(default_factory=_utcnow)
