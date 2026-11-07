from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import passwords
from app.core.config import Settings
from app.core.email_address import validate_deliverable
from app.core.errors import AppError, Conflict, RateLimited, Unauthorized, ValidationFailed
from app.core.rate_limit import RateLimiter
from app.core.security import email_fingerprint, hash_token, new_session_token, normalize_email, validate_password
from app.db.models import AuthSession, AuthToken, User
from app.providers.email import Mail
from app.repositories import users as users_repo
from app.services import audit_service

log = logging.getLogger(__name__)
INVALID_CREDENTIALS = "Incorrect email or password."
INVALID_LINK = "This link is invalid or has expired. Request a new one."
SendMail = Callable[[Mail], None]  # schedules a message (FastAPI BackgroundTasks in the API)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def register(
    db: Session, settings: Settings, limiter: RateLimiter, *, email: str, password: str, client_key: str
) -> User:
    allowed, retry = limiter.hit(f"register:{client_key}", settings.rate_limit_register_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry)
    email_n = normalize_email(email)
    validate_deliverable(
        email_n,
        check_dns=settings.email_check_dns,
        timeout_s=settings.email_dns_timeout_s,
        allow_reserved=settings.app_env.lower() in ("development", "test"),
    )
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


def login(
    db: Session,
    settings: Settings,
    limiter: RateLimiter,
    *,
    email: str,
    password: str,
    client_key: str,
    user_agent: str | None,
) -> tuple[User, str, datetime]:
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
        audit_service.record(
            db,
            action="auth.login_failed",
            entity_type="user",
            entity_id=user.id if user else None,
            actor_id=None,
            after={"email_fingerprint": fp},
        )
        db.commit()  # persist the audit event even though we then raise
        raise Unauthorized(INVALID_CREDENTIALS, code="invalid_credentials")
    if settings.email_verification_required and user.email_verified_at is None:
        raise EmailNotVerified()
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    token = new_session_token()
    expires = _utcnow() + timedelta(hours=settings.session_ttl_hours)
    users_repo.add_session(
        db,
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=expires,
            user_agent=(user_agent or "")[:200] or None,
        ),
    )
    audit_service.record(db, action="auth.login_succeeded", entity_type="user", entity_id=user.id, actor_id=user.id)
    db.commit()
    return user, token, expires


class EmailNotVerified(AppError):
    status_code = 403
    code = "email_not_verified"

    def __init__(self) -> None:
        super().__init__("Confirm your email address first: open the link we sent you, or ask for a new one.")


# ---------- one-time e-mail tokens (verification and password reset) ----------


def _issue(db: Session, user: User, purpose: str, ttl: timedelta) -> str:
    """Creates a token and invalidates earlier unused tokens of the same purpose (only the newest link works)."""
    now = _utcnow()
    db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = new_session_token()
    db.add(
        AuthToken(
            user_id=user.id, purpose=purpose, token_hash=hash_token(token), email=user.email, expires_at=now + ttl
        )
    )
    db.flush()
    return token


def _consume(db: Session, token: str, purpose: str) -> tuple[AuthToken, User]:
    if not token or len(token) > 200:
        raise ValidationFailed(INVALID_LINK, details=[{"field": "token", "issue": "invalid"}])
    row = db.scalar(
        select(AuthToken)
        .where(AuthToken.token_hash == hash_token(token), AuthToken.purpose == purpose)
        .with_for_update()
    )
    user = users_repo.get_by_id(db, row.user_id) if row is not None else None
    if (
        row is None
        or user is None
        or row.used_at is not None
        or row.expires_at <= _utcnow()
        or row.email != user.email  # the address changed after the link was sent
        or user.status != "active"
    ):
        raise ValidationFailed(INVALID_LINK, details=[{"field": "token", "issue": "invalid_or_expired"}])
    row.used_at = _utcnow()
    return row, user


def _verification_mail(settings: Settings, user: User, token: str) -> Mail:
    link = f"{settings.app_base_url.rstrip('/')}/verify-email?token={token}"
    hours = settings.email_verification_ttl_hours
    return Mail(
        to=user.email,
        subject="Confirm your email address - Systematic Review Scientist",
        text=(
            f"Hello,\n\nPlease confirm that this is your email address by opening the link below:\n\n{link}\n\n"
            f"The link works once and expires in {hours} hours. If you did not create an account, ignore this message."
        ),
    )


def _bucket(limiter: RateLimiter, settings: Settings, kind: str, email_n: str, client_key: str) -> None:
    n = settings.rate_limit_password_reset_per_hour
    for key in (f"{kind}:acct:{email_fingerprint(email_n)}", f"{kind}:ip:{client_key}"):
        allowed, retry = limiter.hit(key, n, 3600)
        if not allowed:
            raise RateLimited(retry)


def send_verification(db: Session, settings: Settings, user: User, send: SendMail) -> None:
    """Sends (schedules) a fresh verification link. Commits."""
    token = _issue(db, user, "verify_email", timedelta(hours=settings.email_verification_ttl_hours))
    audit_service.record(db, action="auth.verification_sent", entity_type="user", entity_id=user.id, actor_id=user.id)
    db.commit()
    send(_verification_mail(settings, user, token))


def request_verification(
    db: Session, settings: Settings, limiter: RateLimiter, send: SendMail, *, email: str, client_key: str
) -> None:
    """Always succeeds silently (never reveals whether an account exists or is already verified)."""
    email_n = normalize_email(email)
    _bucket(limiter, settings, "verify", email_n, client_key)
    user = users_repo.get_by_email(db, email_n)
    if user is None or user.status != "active" or user.email_verified_at is not None:
        return
    send_verification(db, settings, user, send)


def confirm_verification(db: Session, *, token: str) -> User:
    row, user = _consume(db, token, "verify_email")
    if user.email_verified_at is None:
        user.email_verified_at = _utcnow()
    audit_service.record(db, action="auth.email_verified", entity_type="user", entity_id=user.id, actor_id=user.id)
    db.commit()
    return user


def request_password_reset(
    db: Session, settings: Settings, limiter: RateLimiter, send: SendMail, *, email: str, client_key: str
) -> None:
    """Always succeeds silently: the response must not reveal whether an account exists."""
    email_n = normalize_email(email)
    _bucket(limiter, settings, "reset", email_n, client_key)
    user = users_repo.get_by_email(db, email_n)
    if user is None or user.status != "active":
        log.info("password reset requested for unknown or disabled account")
        return
    minutes = settings.password_reset_ttl_minutes
    token = _issue(db, user, "reset_password", timedelta(minutes=minutes))
    audit_service.record(
        db, action="auth.password_reset_requested", entity_type="user", entity_id=user.id, actor_id=None
    )
    db.commit()
    link = f"{settings.app_base_url.rstrip('/')}/reset-password?token={token}"
    send(
        Mail(
            to=user.email,
            subject="Reset your password - Systematic Review Scientist",
            text=(
                f"Hello,\n\nSomeone (hopefully you) asked to reset the password for this account. Open this link to "
                f"choose a new password:\n\n{link}\n\nThe link works once and expires in {minutes} minutes. If you "
                "did not ask for this, ignore this message: your password has not been changed."
            ),
        )
    )


def confirm_password_reset(db: Session, *, token: str, password: str) -> User:
    """Sets the new password, signs out every session, and marks the address verified (the user proved access)."""
    row, user = _consume(db, token, "reset_password")
    validate_password(password, user.email)
    user.password_hash = passwords.hash_password(password)
    if user.email_verified_at is None:
        user.email_verified_at = _utcnow()
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_utcnow())
    )
    audit_service.record(
        db, action="auth.password_reset_completed", entity_type="user", entity_id=user.id, actor_id=user.id
    )
    db.commit()
    return user


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


def purge_sessions(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Retention for session rows (Phase 14): ended sessions are kept SESSION_RETENTION_DAYS for investigation, then
    deleted. The audit trail of sign-ins and sign-outs is unaffected (audit_events are append-only)."""
    cutoff = (now or _utcnow()) - timedelta(days=max(0, settings.session_retention_days))
    n = users_repo.delete_sessions_ended_before(db, cutoff)
    db.commit()
    return n
