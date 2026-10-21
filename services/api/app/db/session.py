from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(s.database_url, pool_size=s.db_pool_size, pool_timeout=s.db_pool_timeout_s,
                         pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Services commit explicitly; anything uncommitted is rolled back on exit."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
