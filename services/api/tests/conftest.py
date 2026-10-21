import os

# Must be set before any `app` import so Settings picks up the test configuration.
os.environ.setdefault("APP_ENV", "test")
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.setdefault("RATE_LIMIT_LOGIN_PER_MIN", "10")
