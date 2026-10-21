# stdlib-only
from app.core.settings_checks import find_unsafe_settings

GOOD = dict(app_env="production", session_secret="x" * 40, cookie_secure=True,
            database_url="postgresql+psycopg://u:realpw@db/x", cors_allowed_origins="https://app.example")


def test_local_environments_are_not_enforced():
    assert find_unsafe_settings(dict(app_env="development", session_secret="CHANGE_ME", cookie_secure=False)) == []
    assert find_unsafe_settings(dict(app_env="test")) == []


def test_good_production_config_passes():
    assert find_unsafe_settings(GOOD) == []


def test_each_unsafe_value_is_reported():
    assert any("SESSION_SECRET" in p for p in find_unsafe_settings({**GOOD, "session_secret": "CHANGE_ME_LONG_RANDOM"}))
    assert any("SESSION_SECRET" in p for p in find_unsafe_settings({**GOOD, "session_secret": "short"}))
    assert any("COOKIE_SECURE" in p for p in find_unsafe_settings({**GOOD, "cookie_secure": False}))
    assert any("DATABASE_URL" in p for p in find_unsafe_settings({**GOOD, "database_url": "postgresql://u:CHANGE_ME@h/d"}))
    assert any("wildcard" in p for p in find_unsafe_settings({**GOOD, "cors_allowed_origins": "*"}))
    assert any("APP_ENV" in p for p in find_unsafe_settings({**GOOD, "app_env": "prod"}))
