"""Pure validation of configuration safety. Stdlib only so it is testable without pydantic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PLACEHOLDER_MARKERS = ("CHANGE_ME", "changeme", "placeholder")
LOCAL_ENVS = frozenset({"development", "test"})


def find_unsafe_settings(values: Mapping[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means OK. Only enforced outside local envs."""
    env = str(values.get("app_env", "development")).lower()
    if env in LOCAL_ENVS:
        return []
    problems: list[str] = []
    secret = str(values.get("session_secret") or "")  # None / unset counts as missing
    if not secret or any(m.lower() in secret.lower() for m in PLACEHOLDER_MARKERS) or len(secret) < 32:
        problems.append("SESSION_SECRET must be a real random value of at least 32 characters")
    if not values.get("cookie_secure"):
        problems.append("COOKIE_SECURE must be true outside development/test")
    db = str(values.get("database_url", ""))
    if any(m.lower() in db.lower() for m in PLACEHOLDER_MARKERS):
        problems.append("DATABASE_URL still contains a placeholder password")
    origins = str(values.get("cors_allowed_origins", ""))
    if "*" in origins:
        problems.append("CORS_ALLOWED_ORIGINS must not contain a wildcard")
    if float(values.get("workflow_debug_node_delay_s") or 0) != 0:
        problems.append("WORKFLOW_DEBUG_NODE_DELAY_S is a test hook and must be 0 outside development/test")
    if int(values.get("search_max_records_per_provider") or 0) > 10_000:
        problems.append("SEARCH_MAX_RECORDS_PER_PROVIDER above 10000 is refused (provider fair-use limits)")
    if values.get("llm_send_private_documents"):
        problems.append("LLM_SEND_PRIVATE_DOCUMENTS must stay false until the consent gates are implemented")
    if str(values.get("storage_backend", "local")).lower() == "memory":
        problems.append("STORAGE_BACKEND=memory is for tests only: the API and the worker cannot share it")
    if str(values.get("malware_scanner", "none")).lower() == "none" and values.get("malware_scan_required"):
        problems.append("MALWARE_SCAN_REQUIRED=true needs MALWARE_SCANNER=clamav")
    if env not in {"staging", "production"}:
        problems.append(f"APP_ENV '{env}' is not one of development|test|staging|production")
    return problems


STORAGE_BACKENDS = ("local", "memory")
MALWARE_SCANNERS = ("none", "clamav")


def find_invalid_document_settings(values: Mapping[str, Any]) -> list[str]:
    """Document/upload settings that are wrong in EVERY environment (development included). Stdlib only."""
    problems: list[str] = []
    if str(values.get("storage_backend", "local")).lower() not in STORAGE_BACKENDS:
        problems.append(f"STORAGE_BACKEND must be one of {', '.join(STORAGE_BACKENDS)} (S3 is not implemented yet)")
    if str(values.get("malware_scanner", "none")).lower() not in MALWARE_SCANNERS:
        problems.append(f"MALWARE_SCANNER must be one of {', '.join(MALWARE_SCANNERS)}")
    for name in (
        "upload_max_bytes",
        "upload_max_pages",
        "upload_max_extracted_chars",
        "upload_quota_bytes_per_project",
        "upload_quota_files_per_project",
        "upload_quota_bytes_per_user",
        "ocr_dpi",
    ):
        if int(values.get(name) or 0) <= 0:
            problems += [f"{name.upper()} must be a positive number"]
    if int(values.get("upload_max_bytes") or 0) > int(values.get("upload_quota_bytes_per_project") or 0):
        problems.append("UPLOAD_MAX_BYTES cannot exceed UPLOAD_QUOTA_BYTES_PER_PROJECT")
    if not 72 <= int(values.get("ocr_dpi") or 0) <= 600:
        problems.append("OCR_DPI must be between 72 and 600")
    langs = str(values.get("ocr_languages") or "")
    if not langs or any(not part.replace("_", "").isalnum() for part in langs.split("+")):
        problems.append("OCR_LANGUAGES must look like eng or eng+deu")
    if float(values.get("document_process_timeout_s") or 0) + float(values.get("ocr_budget_s") or 0) >= float(
        values.get("job_default_timeout_s") or 0
    ):
        problems.append(
            "DOCUMENT_PROCESS_TIMEOUT_S + OCR_BUDGET_S must be below JOB_DEFAULT_TIMEOUT_S (the worker's hard limit)"
        )
    return problems
