"""Typed settings from environment (12-factor). Fails fast outside local environments."""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_checks import find_unsafe_settings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    cors_allowed_origins: str = "http://localhost:3000"
    log_level: str = "INFO"
    trusted_proxy_hops: int = 0  # number of trusted reverse proxies in front of the API (0 = ignore X-Forwarded-For)

    database_url: str = "postgresql+psycopg://srs:CHANGE_ME@localhost:5432/srs"
    db_pool_size: int = 5
    db_pool_timeout_s: int = 10

    redis_url: str = "redis://localhost:6379/0"
    job_default_timeout_s: int = 900
    job_max_retries: int = 3
    job_stale_after_s: int = 120
    job_requeue_queued_after_s: int = 60
    job_sweep_interval_s: float = 60.0
    worker_health_timeout_s: float = 1.5

    session_cookie_name: str = "srs_session"
    session_ttl_hours: int = 168
    session_secret: str = "CHANGE_ME_LONG_RANDOM"  # reserved for signed values in later phases; not used for session tokens
    cookie_secure: bool = False

    rate_limit_login_per_min: int = 10
    rate_limit_register_per_hour: int = 20

    dev_seed_email: str = "demo@example.invalid"
    dev_seed_password: str = "demo-password-change-me"

    @property
    def allowed_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        base = self.app_base_url.strip().rstrip("/")
        if base and base not in origins:
            origins.append(base)
        return origins

    @model_validator(mode="after")
    def _fail_fast_outside_local(self) -> "Settings":
        problems = find_unsafe_settings(self.model_dump())
        if problems:
            raise ValueError("Unsafe configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
