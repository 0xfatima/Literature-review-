"""Client-IP derivation behind reverse proxies. Stdlib only.

Why this exists: behind the Next.js proxy every request reaches the API from the web container's address.
Using the TCP peer for per-IP rate limits would make the bucket GLOBAL (one attacker could lock everyone out).
Conversely, trusting the FIRST X-Forwarded-For entry is spoofable: proxies APPEND to a client-supplied header.

Rule: with N trusted proxy hops, the client is the N-th entry counted from the RIGHT (each trusted proxy
appended the address of its immediate peer). Anything to the left of that is untrusted and ignored.
N = 0 means: ignore the header entirely and use the TCP peer.
"""

from __future__ import annotations

import ipaddress


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(xff_header: str | None, peer: str | None, trusted_hops: int) -> str:
    fallback = _valid_ip(peer) if peer else None
    fallback = fallback or "unknown"
    if trusted_hops <= 0 or not xff_header:
        return fallback
    parts = [p.strip() for p in xff_header.split(",") if p.strip()]
    if len(parts) < trusted_hops:
        return fallback  # chain shorter than configured: misconfiguration or direct access -> do not trust
    return _valid_ip(parts[-trusted_hops]) or fallback
