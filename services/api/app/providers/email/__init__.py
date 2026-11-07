"""Outgoing e-mail. `build_mailer(settings)` is the only place a mailer is constructed.

console: logs the message (development). Outside development/test the body (which carries one-time links) is never
logged, only the fact that a message would have been sent. smtp: sends via smtplib (STARTTLS or implicit TLS).
none: drops the message. Sending failures are logged and never raised to the user (responses must not reveal
whether an account exists, and a broken mail server must not break sign-up).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass
class Mail:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    name: str

    def send(self, mail: Mail) -> bool: ...


class ConsoleMailer:
    name = "console"

    def __init__(self, show_body: bool):
        self.show_body = show_body
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        if self.show_body:
            log.warning(
                "EMAIL (console backend, not sent)\nTo: %s\nSubject: %s\n\n%s", mail.to, mail.subject, mail.text
            )
        else:
            log.warning("EMAIL_BACKEND=console outside development: a message was NOT sent (body suppressed)")
        return True


class NullMailer:
    name = "none"

    def send(self, mail: Mail) -> bool:
        log.info("e-mail disabled (EMAIL_BACKEND=none); message dropped")
        return False


class SmtpMailer:
    name = "smtp"

    def __init__(self, settings: Any):
        self.s = settings

    def send(self, mail: Mail) -> bool:
        s = self.s
        sender = s.smtp_from or s.smtp_username
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg.set_content(mail.text)
        ctx = ssl.create_default_context()
        try:
            if s.smtp_use_ssl:
                server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_s, context=ctx)
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_s)
            with server:
                if not s.smtp_use_ssl and s.smtp_starttls:
                    server.starttls(context=ctx)
                if s.smtp_username and s.smtp_password:
                    server.login(s.smtp_username, s.smtp_password)
                server.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as e:
            log.error("sending e-mail failed", extra={"error": type(e).__name__})
            return False


def build_mailer(settings: Any) -> Mailer:
    backend = str(getattr(settings, "email_backend", "console")).lower()
    if backend == "smtp":
        return SmtpMailer(settings)
    if backend == "none":
        return NullMailer()
    return ConsoleMailer(show_body=str(settings.app_env).lower() in ("development", "test"))
