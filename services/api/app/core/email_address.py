"""Is this e-mail address real enough to accept? Syntax, provider rules (Gmail), throwaway domains and DNS.

This cannot prove that a mailbox exists: no provider (Gmail included) reliably answers that question to strangers,
and probing their SMTP servers gets the sender blocked. Proof of ownership is the verification e-mail
(auth_service.request_verification). These checks only refuse addresses that can never receive mail.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.core.errors import ValidationFailed

# Common typos of the big consumer domains -> the intended domain (we suggest, never silently change)
TYPO_DOMAINS: dict[str, str] = {
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.con": "gmail.com",
    "gmal.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "gmail.cm": "gmail.com",
    "gmaill.com": "gmail.com",
    "googlemail.co": "googlemail.com",
    "hotmial.com": "hotmail.com",
    "hotmail.co": "hotmail.com",
    "hotmal.com": "hotmail.com",
    "outlok.com": "outlook.com",
    "outlook.co": "outlook.com",
    "yaho.com": "yahoo.com",
    "yahoo.co": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "iclod.com": "icloud.com",
    "icloud.co": "icloud.com",
}
# A short list of well-known throwaway inbox services. Not exhaustive by design.
DISPOSABLE_DOMAINS = frozenset(
    """mailinator.com guerrillamail.com guerrillamail.net sharklasers.com 10minutemail.com 10minutemail.net
    tempmail.com temp-mail.org tempmail.net yopmail.com yopmail.net trashmail.com getnada.com nada.email
    dispostable.com maildrop.cc throwawaymail.com fakeinbox.com mintemail.com mailnesia.com mohmal.com
    emailondeck.com tempr.email discard.email spamgourmet.com mailcatch.com moakt.com tmail.ws tempinbox.com""".split()
)
GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_LOCAL_OK = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}$")
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_GMAIL_LOCAL = re.compile(r"^[a-z0-9.]+$")

DnsLookup = Callable[[str, float], bool | None]


def _fail(message: str, issue: str) -> ValidationFailed:
    return ValidationFailed(message, details=[{"field": "email", "issue": issue}])


def check_syntax(email: str) -> None:
    """Raises ValidationFailed for addresses that are syntactically impossible or obviously mistyped."""
    local, sep, domain = email.rpartition("@")
    if not sep or not local or not domain:
        raise _fail("Enter a valid email address.", "invalid")
    if not _LOCAL_OK.match(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        raise _fail("The part before the @ is not a valid email name.", "invalid_local_part")
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL.match(lb) for lb in labels) or not re.match(r"^[a-z]{2,63}$", labels[-1]):
        raise _fail("The part after the @ is not a valid domain.", "invalid_domain")
    if domain in TYPO_DOMAINS:
        raise _fail(f"Did you mean @{TYPO_DOMAINS[domain]}? Please check the address.", "domain_typo")
    if domain in DISPOSABLE_DOMAINS:
        raise _fail("Disposable email addresses are not accepted. Use an address you keep.", "disposable")
    if domain in GMAIL_DOMAINS:
        base = local.split("+", 1)[0]
        name = base.replace(".", "")
        # Gmail usernames: 6-30 characters, letters/digits/periods, start and end with a letter or digit
        if not _GMAIL_LOCAL.match(base) or not 6 <= len(name) <= 30 or not base[0].isalnum() or not base[-1].isalnum():
            raise _fail(
                "This is not a valid Gmail address (Gmail names have 6-30 letters, numbers and periods).",
                "invalid_gmail",
            )


def domain_accepts_mail(domain: str, timeout_s: float) -> bool | None:
    """True: the domain publishes MX (or, failing that, A/AAAA) records. False: it certainly does not exist / has a
    null MX. None: DNS could not be asked (no resolver, timeout) - callers must then NOT refuse the address."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:  # pragma: no cover - dnspython is a declared dependency
        return None
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout_s
    resolver.timeout = timeout_s
    try:
        answers = resolver.resolve(domain, "MX")
        hosts = [str(r.exchange).rstrip(".") for r in answers]
        return any(h for h in hosts)  # RFC 7505 null MX ("." only) = accepts no mail
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        pass
    except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.exception.DNSException):
        return None
    for rtype in ("A", "AAAA"):
        try:
            resolver.resolve(domain, rtype)
            return True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            continue
        except dns.exception.DNSException:
            return None
    return False


RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org"})
RESERVED_TLDS = ("test", "invalid", "example", "localhost")


def is_reserved(domain: str) -> bool:
    """RFC 2606 / 6761 names that never receive real mail (used by tests and demo data)."""
    return domain in RESERVED_DOMAINS or domain.rsplit(".", 1)[-1] in RESERVED_TLDS


def validate_deliverable(
    email: str,
    *,
    check_dns: bool,
    timeout_s: float,
    lookup: DnsLookup | None = None,
    allow_reserved: bool = False,
) -> None:
    """`email` must already be normalised (lower-cased, trimmed). Raises ValidationFailed.
    `allow_reserved` (development/test only) accepts example.com / *.test / *.invalid addresses without DNS."""
    check_syntax(email)
    domain = email.rpartition("@")[2]
    if is_reserved(domain):
        if allow_reserved:
            return
        raise _fail(f"The domain {domain} is reserved and cannot receive email.", "reserved_domain")
    if not check_dns or domain in GMAIL_DOMAINS:  # known-good domains need no lookup
        return
    ok = (lookup or domain_accepts_mail)(domain, timeout_s)
    if ok is False:
        raise _fail(f"The domain {domain} does not receive email. Check the address.", "domain_has_no_mail_server")
