"""Operator CLI:  python -m app.cli <command>

create-user EMAIL      create an account (password read from stdin/prompt, never argv)
set-password EMAIL     operator-assisted password reset (the ONLY recovery path until Phase-N email flow exists)
seed-dev               development ONLY: demo user + one project explicitly labelled as demo data
sweep-jobs             run the job-recovery sweep once
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.core import passwords
from app.core.config import get_settings
from app.core.security import normalize_email, validate_password
from app.db.models import Project, User
from app.db.session import get_sessionmaker
from app.repositories import users as users_repo
from app.services import audit_service

DEMO_TITLE = "[Demo data] Example review - safe to delete"


def _read_password(confirm: bool) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    if confirm and pw != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return pw


def create_user(email: str, password: str, *, actor: str = "cli") -> User:
    email_n = normalize_email(email)
    validate_password(password, email_n)
    with get_sessionmaker()() as db:
        if users_repo.get_by_email(db, email_n):
            raise SystemExit("User already exists.")
        u = users_repo.add(db, User(email=email_n, password_hash=passwords.hash_password(password)))
        audit_service.record(
            db,
            action="user.registered",
            entity_type="user",
            entity_id=u.id,
            actor_id=None,
            reason=f"created via {actor}",
        )
        db.commit()
        return u


def set_password(email: str, password: str) -> None:
    email_n = normalize_email(email)
    validate_password(password, email_n)
    with get_sessionmaker()() as db:
        u = users_repo.get_by_email(db, email_n)
        if u is None:
            raise SystemExit("No such user.")
        u.password_hash = passwords.hash_password(password)
        from datetime import UTC, datetime

        from sqlalchemy import update

        from app.db.models import AuthSession

        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )  # a reset must end existing sessions
        audit_service.record(
            db,
            action="auth.password_reset_by_operator",
            entity_type="user",
            entity_id=u.id,
            actor_id=None,
            reason="operator CLI",
        )
        db.commit()


def seed_dev() -> None:
    s = get_settings()
    if s.app_env != "development":
        raise SystemExit(f"Refusing to seed: APP_ENV is '{s.app_env}', not 'development'.")
    password = s.dev_seed_password
    if not password:
        raise SystemExit(
            "DEV_SEED_PASSWORD is not set. Copy .env.example to .env (it has a development placeholder) "
            "or set DEV_SEED_PASSWORD, then run seed-dev again."
        )
    with get_sessionmaker()() as db:
        email = normalize_email(s.dev_seed_email)
        u = users_repo.get_by_email(db, email)
        if u is None:
            u = users_repo.add(db, User(email=email, password_hash=passwords.hash_password(password)))
            audit_service.record(
                db,
                action="user.registered",
                entity_type="user",
                entity_id=u.id,
                actor_id=None,
                reason="dev seed (demo data)",
            )
        from app.repositories import projects as prepo

        if not any(p.title == DEMO_TITLE for p in prepo.list_for_owner(db, u.id, True, 200, 0)):
            p = prepo.add(
                db, Project(owner_id=u.id, title=DEMO_TITLE, topic_original="Demo data: not a real review topic.")
            )
            audit_service.record(
                db,
                action="project.created",
                entity_type="project",
                entity_id=p.id,
                actor_id=None,
                project_id=p.id,
                reason="dev seed (demo data)",
            )
        db.commit()
    print(f"Seeded demo user {s.dev_seed_email} (password from DEV_SEED_PASSWORD). Development only.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("create-user", "set-password"):
        sp = sub.add_parser(name)
        sp.add_argument("email")
    sub.add_parser("seed-dev")
    sub.add_parser("sweep-jobs")
    args = ap.parse_args(argv)
    if args.cmd == "create-user":
        u = create_user(args.email, _read_password(True))
        print(f"Created user {u.id}")
    elif args.cmd == "set-password":
        set_password(args.email, _read_password(True))
        print("Password updated; existing sessions revoked.")
    elif args.cmd == "seed-dev":
        seed_dev()
    elif args.cmd == "sweep-jobs":
        from app.jobs.celery_app import send_task
        from app.services import job_service

        with get_sessionmaker()() as db:
            print(job_service.sweep(db, get_settings(), send_task))


if __name__ == "__main__":
    main()
