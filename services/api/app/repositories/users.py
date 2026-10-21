from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, User


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def add(db: Session, user: User) -> User:
    db.add(user)
    db.flush()
    return user


def add_session(db: Session, sess: AuthSession) -> AuthSession:
    db.add(sess)
    db.flush()
    return sess


def get_session_by_token_hash(db: Session, token_hash: str) -> AuthSession | None:
    return db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
