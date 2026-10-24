"""Configuration and infrastructure consistency for document handling. Stdlib + Settings only: these fail when the
limits, the proxy, the shared storage volume or the OCR image drift apart."""

import pathlib
import re

import pytest

from app.core.config import Settings
from app.core.settings_checks import find_invalid_document_settings, find_unsafe_settings

ROOT = pathlib.Path(__file__).resolve().parents[4]
NGINX = (ROOT / "infra" / "proxy" / "nginx.conf").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()
WORKER = (ROOT / "infra" / "docker" / "worker.Dockerfile").read_text()
API = (ROOT / "infra" / "docker" / "api.Dockerfile").read_text()
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()


def active(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_defaults_are_valid():
    assert find_invalid_document_settings(Settings().model_dump()) == []


@pytest.mark.parametrize(
    "update,needle",
    [
        ({"storage_backend": "s3"}, "STORAGE_BACKEND"),
        ({"malware_scanner": "virustotal"}, "MALWARE_SCANNER"),
        ({"upload_max_bytes": 0}, "UPLOAD_MAX_BYTES"),
        ({"upload_max_bytes": 10**12}, "cannot exceed"),
        ({"ocr_dpi": 10}, "OCR_DPI"),
        ({"ocr_languages": "eng;rm -rf"}, "OCR_LANGUAGES"),
        ({"ocr_languages": ""}, "OCR_LANGUAGES"),
        ({"document_process_timeout_s": 800, "ocr_budget_s": 200}, "JOB_DEFAULT_TIMEOUT_S"),
    ],
)
def test_invalid_document_settings_are_refused_in_every_environment(update, needle):
    with pytest.raises(ValueError, match=needle):
        Settings(**update)


def test_memory_storage_and_required_scan_without_a_scanner_are_refused_outside_local_environments():
    base = Settings().model_dump() | {"app_env": "production"}
    assert any("STORAGE_BACKEND=memory" in p for p in find_unsafe_settings(base | {"storage_backend": "memory"}))
    assert any("MALWARE_SCAN_REQUIRED" in p for p in find_unsafe_settings(base | {"malware_scan_required": True}))
    assert not any("STORAGE" in p for p in find_unsafe_settings(base))


def test_relative_storage_root_resolves_against_the_repository_not_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert Settings(storage_local_root="./storage").storage_root_path == (ROOT / "storage").resolve()
    assert Settings(storage_local_root=str(tmp_path / "x")).storage_root_path == tmp_path / "x"


def test_nginx_allows_slightly_more_than_the_upload_limit_on_the_upload_route_only():
    conf = active(NGINX)
    block = re.search(r'location\s+~\s+"[^"]*documents[^"]*"\s*\{(.*?)\}', conf, re.S)
    assert block, "no dedicated upload location"
    mb = int(re.search(r"client_max_body_size\s+(\d+)m", block.group(1)).group(1))
    assert mb * 1024 * 1024 > Settings().upload_max_bytes  # nginx must not reject a legal file before the API sees it
    assert mb * 1024 * 1024 <= Settings().upload_max_bytes + 10 * 1024 * 1024  # ...nor accept far more than allowed
    assert "proxy_set_header" not in block.group(1)  # would drop every inherited header (see test_edge_proxy_config)
    default = int(re.search(r"^\s*client_max_body_size\s+(\d+)m", conf, re.M).group(1))
    assert default <= 10  # every other route keeps the small default


def test_api_and_worker_share_one_storage_volume():
    api = re.search(r"^  api:\n(.*?)^  worker:", COMPOSE, re.S | re.M).group(1)
    worker = re.search(r"^  worker:\n(.*?)^  web:", COMPOSE, re.S | re.M).group(1)
    assert "storage:/srv/storage" in api and "storage:/srv/storage" in worker
    assert re.search(r"^volumes:\n(?:  \w+:\n)*  storage:", COMPOSE, re.M)
    assert "STORAGE_LOCAL_ROOT: /srv/storage" in COMPOSE
    for dockerfile in (API, WORKER):
        assert "mkdir -p /srv/storage && chown app:app /srv/storage" in dockerfile  # the non-root user can write


def test_the_worker_image_installs_the_ocr_engine_and_the_api_image_does_not_need_it():
    assert "tesseract-ocr" in WORKER and "tesseract-ocr-eng" in WORKER and "tesseract" not in API


def test_ci_installs_tesseract_and_requires_the_real_ocr_test():
    assert "tesseract-ocr" in CI and 'REQUIRE_OCR_TESTS: "1"' in CI


def test_env_example_documents_every_document_setting_with_safe_defaults():
    for key in ("STORAGE_BACKEND=local", "UPLOAD_MAX_BYTES=52428800", "FEATURE_OCR=false", "MALWARE_SCANNER=none"):
        assert key in ENV_EXAMPLE
    assert "MALWARE_SCAN_REQUIRED=false" in ENV_EXAMPLE
