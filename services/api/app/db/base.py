from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CursorResult, MetaData, Result, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names make Alembic migrations/diffs stable.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def uuid_pk() -> Mapped[uuid.UUID]:
    # Non-guessable UUIDv4 generated in the application (spec §8).
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def now_col() -> Mapped[datetime]:
    from sqlalchemy import DateTime

    return mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


def affected_rows(result: Result[Any]) -> int:
    """Rows matched by an UPDATE/DELETE. Session.execute() is typed as returning Result, but DML
    statements always return a CursorResult, which is the only Result subclass that carries rowcount."""
    if not isinstance(result, CursorResult):
        raise TypeError("affected_rows() requires the result of a DML statement")
    return int(result.rowcount)
