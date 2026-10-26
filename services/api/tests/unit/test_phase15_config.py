# stdlib-only
from app.core.settings_checks import find_unsafe_settings
from app.providers.scholarly import parse_endpoints

PROD = {
    "app_env": "production",
    "session_secret": "x" * 40,
    "cookie_secure": True,
    "database_url": "postgresql://u:real@h/db",
    "rate_limit_writes_per_min": 600,
}


def test_endpoint_overrides_parse_known_providers_only():
    got = parse_endpoints(" openalex=https://m/works , nope=https://x , crossref=ftp://bad, europepmc=http://l/s ")
    assert got == {"openalex": "https://m/works", "europepmc": "http://l/s"}
    assert parse_endpoints("") == {}


def test_plain_http_endpoint_overrides_are_refused_in_production():
    assert any(
        "SCHOLARLY_ENDPOINTS" in p for p in find_unsafe_settings({**PROD, "scholarly_endpoints": "openalex=http://x"})
    )
    assert not any(
        "SCHOLARLY" in p for p in find_unsafe_settings({**PROD, "scholarly_endpoints": "openalex=https://x"})
    )
