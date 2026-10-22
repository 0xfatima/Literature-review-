# stdlib-only
"""Regression guard for the per-IP rate-limit key (CI drills `ip-limit` / `ip-isolation`).

Next.js rewrites forward a client-supplied X-Forwarded-For verbatim and add nothing, so they must never be the
hop that TRUSTED_PROXY_HOPS=1 trusts. The compose stack therefore routes browsers through an nginx edge proxy that
OVERWRITES the header with the TCP peer and sends /api/* straight to the API. These checks fail if that wiring
is undone. Stdlib-only: plain text checks, no YAML/nginx parser."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[4]
NGINX = (ROOT / "infra" / "proxy" / "nginx.conf").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def _active(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_edge_proxy_overwrites_forwarded_for_with_tcp_peer():
    conf = _active(NGINX)
    assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr\s*;", conf)
    assert "$proxy_add_x_forwarded_for" not in conf  # appending would keep the client-forged entries


def test_api_location_goes_directly_to_api_and_inherits_headers():
    conf = _active(NGINX)
    api_block = re.search(r"location\s+/api/\s*\{(.*?)\}", conf, re.S)
    assert api_block, "no /api/ location"
    assert "proxy_pass $api_upstream" in api_block.group(1)
    assert "http://api:8000" in conf


def test_no_location_block_overrides_inherited_proxy_headers():
    """nginx drops ALL inherited proxy_set_header directives in a block that defines its own. A single
    Upgrade header in `location /` once removed Host and X-Forwarded-* for every page request."""
    conf = _active(NGINX)
    blocks = re.findall(r"location\s+\S+\s*\{(.*?)\}", conf, re.S)
    assert len(blocks) >= 2
    for body in blocks:
        assert "proxy_set_header" not in body, body
    for header in ("Host", "X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host"):
        assert re.search(rf"proxy_set_header\s+{header}\s", conf), header


def test_compose_publishes_only_the_proxy_and_trusts_exactly_one_hop():
    assert 'TRUSTED_PROXY_HOPS: "1"' in COMPOSE
    assert re.search(r"^  proxy:\s*(#.*)?$", COMPOSE, re.M)
    assert '"3000:80"' in COMPOSE  # public entry point is the proxy
    assert '"3000:3000"' not in COMPOSE  # web must not be published directly
    assert '"127.0.0.1:8000:8000"' in COMPOSE and '"8000:8000"' not in COMPOSE.replace('"127.0.0.1:8000:8000"', "")
