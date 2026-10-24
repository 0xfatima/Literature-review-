"""Typed settings from environment (12-factor). Fails fast outside local environments."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_checks import find_invalid_document_settings, find_unsafe_settings

# The repository-root .env (services/api/app/core/config.py -> parents[4]). Loading it by absolute path means
# `alembic`, `uvicorn` and `celery` find it whether they are started from the repo root or from services/api.
# A .env in the current directory is read second and wins. Real environment variables always beat both files.
# In containers neither file exists (.dockerignore excludes .env); compose injects variables instead.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_REPO_ROOT_ENV = _REPO_ROOT / ".env"


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

    # Documents (Phase 6). Uploaded PDFs are untrusted input: they are validated at the edge, stored under a generated
    # key (never the user's filename) and parsed in a resource-limited child process by the worker.
    storage_backend: str = "local"  # local | memory (memory is for tests only: it cannot be shared by api and worker)
    storage_local_root: str = (
        "./storage"  # relative paths resolve against the repository root; api and worker MUST share it
    )
    upload_max_bytes: int = 50 * 1024 * 1024  # per file; infra/proxy/nginx.conf must allow a little more than this
    upload_max_pages: int = 600  # more pages than this fails processing with an actionable message
    upload_max_extracted_chars: int = 15_000_000  # text-bomb guard
    upload_quota_bytes_per_project: int = 2 * 1024**3
    upload_quota_files_per_project: int = 1000
    upload_quota_bytes_per_user: int = 10 * 1024**3
    rate_limit_upload_per_hour: int = 120
    document_removed_retention_days: int = (
        30  # removed documents keep their file this long, then are purged (0 = next sweep)
    )
    document_purge_interval_s: float = 3600.0  # how often the purge/orphan sweep runs
    document_orphan_grace_s: int = 86_400  # stored objects without a database row are deleted only after this age
    document_process_timeout_s: float = 420.0  # hard limit for the text-extraction child process
    document_child_memory_mb: int = 2048  # address-space limit for the child process (0 = no limit; POSIX only)
    feature_ocr: bool = False  # OCR only for pages that have no usable text layer; needs the tesseract binary
    ocr_languages: str = "eng"  # tesseract language codes, joined with +
    ocr_dpi: int = 200
    ocr_max_pages: int = 100  # per document; remaining image-only pages are reported as unreadable
    ocr_budget_s: float = 300.0  # total OCR time per document
    ocr_timeout_per_page_s: float = 60.0
    tesseract_cmd: str = "tesseract"
    malware_scanner: str = "none"  # none | clamav
    clamd_host: str = "localhost"
    clamd_port: int = 3310
    clamd_timeout_s: float = 60.0
    malware_scan_required: bool = False  # true: a document that cannot be scanned fails instead of being parsed

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
    def storage_root_path(self) -> Path:
        root = Path(self.storage_local_root).expanduser()
        return root if root.is_absolute() else (_REPO_ROOT / root).resolve()

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
        bad = find_invalid_document_settings(self.model_dump())
        if bad:
            raise ValueError("Invalid configuration: " + "; ".join(bad))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
