# stdlib-only
from app.core.net import client_ip

PEER = "172.18.0.5"  # e.g. the Next.js container


def test_zero_hops_ignores_the_header_entirely():
    assert client_ip("203.0.113.9", PEER, 0) == PEER
    assert client_ip("203.0.113.9, 198.51.100.2", PEER, 0) == PEER


def test_one_trusted_proxy_uses_the_rightmost_entry_not_the_first():
    # Browser sent a forged XFF; the trusted proxy appended the address it actually saw.
    assert client_ip("6.6.6.6, 203.0.113.9", PEER, 1) == "203.0.113.9"
    assert client_ip("203.0.113.9", PEER, 1) == "203.0.113.9"


def test_two_trusted_proxies_count_from_the_right():
    assert client_ip("6.6.6.6, 203.0.113.9, 10.0.0.7", PEER, 2) == "203.0.113.9"
    assert client_ip("6.6.6.6, 203.0.113.9, 10.0.0.7", PEER, 1) == "10.0.0.7"


def test_chain_shorter_than_configured_falls_back_to_peer():
    assert client_ip("203.0.113.9", PEER, 2) == PEER  # direct access / misconfiguration: do not trust
    assert client_ip(None, PEER, 1) == PEER and client_ip("", PEER, 1) == PEER


def test_garbage_values_never_become_a_key():
    assert client_ip("not-an-ip", PEER, 1) == PEER
    assert client_ip("1.2.3.4, <script>", PEER, 1) == PEER
    assert client_ip(None, None, 0) == "unknown" and client_ip(None, "bogus", 0) == "unknown"


def test_ipv6_normalised_and_whitespace_tolerated():
    assert client_ip(" 2001:DB8::1 ", PEER, 1) == "2001:db8::1"
    assert client_ip("6.6.6.6 ,  203.0.113.9 ", PEER, 1) == "203.0.113.9"


def test_spoofing_cannot_move_a_client_into_another_bucket():
    # An attacker varying the forged leading entries still maps to ONE key (their real address).
    keys = {client_ip(f"{i}.{i}.{i}.{i}, 203.0.113.9", PEER, 1) for i in range(1, 50)}
    assert keys == {"203.0.113.9"}
