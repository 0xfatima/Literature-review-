from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import passwords
from app.core.config import Settings
from app.core.errors import Conflict, RateLimited, Unauthorized
from app.core.rate_limit import RateLimiter
from app.core.security import (email_fingerprint, hash_token, new_session_token, normalize_email, validate_password)
from app.db.models import AuthSession, User
from app.repositories import users as users_repo
from app.services import audit_service

log = logging.getLogger(__name__)
INVALID_CREDENTIALS = "Incorrect email or password."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def register(db: Session, settings: Settings, limiter: RateLimiter, *, email: str, password: str,
             client_key: str) -> User:
    allowed, retry = limiter.hit(f"register:{client_key}", settings.rate_limit_register_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry)
    email_n = normalize_email(email)
    validate_password(password, email_n)
    if users_repo.get_by_email(db, email_n):
        raise Conflict("An account with this email already exists.", code="email_taken")
    user = User(email=email_n, password_hash=passwords.hash_password(password))
    try:
        users_repo.add(db, user)
        audit_service.record(db, action="user.registered", entity_type="user", entity_id=user.id, actor_id=user.id)
        db.commit()
    except IntegrityError:  # concurrent registration of the same email
        db.rollback()
        raise Conflict("An account with this email already exists.", code="email_taken") from None
    log.info("user registered", extra={"user_id": str(user.id)})
    return user


def login(db: Session, settings: Settings, limiter: RateLimiter, *, email: str, password: str,
          client_key: str, user_agent: str | None) -> tuple[User, str, datetime]:
    email_n = normalize_email(email)
    fp = email_fingerprint(email_n)
    # Two buckets: per-account (stops targeted guessing from many IPs) and per-client.
    for key in (f"login:acct:{fp}", f"login:ip:{client_key}"):
        allowed, retry = limiter.hit(key, settings.rate_limit_login_per_min, 60)
        if not allowed:
            raise RateLimited(retry)
    user = users_repo.get_by_email(db, email_n)
    ok = passwords.verify_password(user.password_hash if user else None, password)  # always one Argon2 verify
    if not ok or user is None or user.status != "active":
        audit_service.record(db, action="auth.login_failed", entity_type="user", entity_id=user.id if user else None,
                             actor_id=None, after={"email_fingerprint": fp})
        db.commit()  # persist the audit event even though we then raise
        raise Unauthorized(INVALID_CREDENTIALS, code="invalid_credentials")
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    token = new_session_token()
    expires = _utcnow() + timedelta(hours=settings.session_ttl_hours)
    users_repo.add_session(db, AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=expires,
                                           user_agent=(user_agent or "")[:200] or None))
    audit_service.record(db, action="auth.login_succeeded", entity_type="user", entity_id=user.id, actor_id=user.id)
    db.commit()
    return user, token, expires


def resolve_session(db: Session, token: str | None) -> tuple[User, AuthSession] | None:
    if not token:
        return None
    sess = users_repo.get_session_by_token_hash(db, hash_token(token))
    if sess is None or sess.revoked_at is not None or sess.expires_at <= _utcnow():
        return None
    user = users_repo.get_by_id(db, sess.user_id)
    if user is None or user.status != "active":
        return None
    return user, sess


def logout(db: Session, user: User, sess: AuthSession) -> None:
    sess.revoked_at = _utcnow()
    audit_service.record(db, action="auth.logout", entity_type="user", entity_id=user.id, actor_id=user.id)
    db.commit()
