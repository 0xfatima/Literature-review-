"""Typed settings from environment (12-factor). Fails fast outside local environments."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_checks import find_unsafe_settings

# The repository-root .env (services/api/app/core/config.py -> parents[4]). Loading it by absolute path means
# `alembic`, `uvicorn` and `celery` find it whether they are started from the repo root or from services/api.
# A .env in the current directory is read second and wins. Real environment variables always beat both files.
# In containers neither file exists (.dockerignore excludes .env); compose injects variables instead.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(_REPO_ROOT_ENV, ".env"), extra="ignore", case_sensitive=False)

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

    # Review workflow. max_iterations bounds the human question loop on one checkpoint thread (spec §5.2).
    workflow_max_iterations: int = 60
    # TEST/DEV ONLY: sleep this long at the start of every graph node (worker-crash drill). Refused elsewhere.
    workflow_debug_node_delay_s: float = 0.0

    # Scholarly search (Phase 4). Providers are called only from the worker. Contact e-mail and API key are sent to
    # the providers that ask for them (OpenAlex/Crossref polite pools) and are never stored with search records.
    search_providers_enabled: str = "openalex,europepmc,arxiv,crossref"
    scholarly_contact_email: str | None = None
    openalex_api_key: str | None = None
    search_http_timeout_s: float = 20.0
    search_max_retries: int = 3  # per request, after the first attempt (timeouts, 429, 5xx)
    search_backoff_base_s: float = 1.0
    search_max_backoff_s: float = 30.0
    search_page_size: int = 100
    search_max_records_per_provider: int = 500  # a query that reports more is marked "capped", never "complete"
    search_job_budget_s: float = 780.0  # stop cleanly (resumable) before the worker's hard task time limit
    rate_limit_search_per_hour: int = 30

    # LLM (Phase 5). LLM_PROVIDER=none (default) disables every model call; nothing is sent anywhere.
    # The key lives only in the backend/worker environment. It is never logged, stored or returned by the API.
    llm_provider: str = "none"  # none | gemini
    llm_api_key: str | None = None
    llm_model: str | None = None  # no default on purpose: verify the model is available on your tier
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_timeout_s: float = 60.0
    llm_min_interval_s: float = 4.5  # request spacing shared across workers (free tiers allow ~10-30 RPM)
    llm_max_retries: int = 3
    llm_send_private_documents: bool = False  # hard default; consent gates arrive with document extraction
    llm_batch_size: int = 10  # papers per relevance request (fewer requests = less quota)
    llm_max_papers_per_job: int = 300
    relevance_job_budget_s: float = 780.0
    rate_limit_relevance_per_hour: int = 20
    # Screening (Phase 5). Title/abstract: an exclusion reason is encouraged; set true to require one.
    screening_ta_require_exclusion_reason: bool = False

    session_cookie_name: str = "srs_session"
    session_ttl_hours: int = 168
    # No default on purpose. Reserved for signed values in later phases (sessions use random opaque tokens today).
    # Outside development/test a missing, short or placeholder value is refused at startup (settings_checks).
    session_secret: str | None = None
    cookie_secure: bool = False

    rate_limit_login_per_min: int = 10
    rate_limit_register_per_hour: int = 20

    dev_seed_email: str = "demo@example.invalid"
    # No default on purpose: set DEV_SEED_PASSWORD (.env.example has a development placeholder). seed-dev only.
    dev_seed_password: str | None = None

    @property
    def allowed_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        base = self.app_base_url.strip().rstrip("/")
        if base and base not in origins:
            origins.append(base)
        return origins

    @model_validator(mode="after")
    def _fail_fast_outside_local(self) -> Settings:
        problems = find_unsafe_settings(self.model_dump())
        if problems:
            raise ValueError("Unsafe configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
