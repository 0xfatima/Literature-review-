# Same code as the API (modular monolith), different process.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
RUN useradd --create-home --uid 10001 app && mkdir -p /srv/storage && chown app:app /srv/storage
# OCR engine (used only when FEATURE_OCR=true, and only for pages with no text layer). Add language packs as needed,
# e.g. tesseract-ocr-deu, and list them in OCR_LANGUAGES.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock.tx[t] ./
COPY services/api ./services/api
RUN if [ -f requirements.lock.txt ]; then pip install -c requirements.lock.txt -e .; else echo 'WARNING: no requirements.lock.txt - floating dependency versions' >&2; pip install -e .; fi && chown -R app:app /srv
USER app
WORKDIR /srv/services/api
# -B embeds the beat scheduler (job-recovery sweep). In production run exactly ONE beat process.
CMD ["celery", "-A", "app.jobs.celery_app:celery_app", "worker", "-B", "--loglevel=INFO", "--concurrency=2"]
