# Same code as the API (modular monolith), different process.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
RUN useradd --create-home --uid 10001 app
COPY pyproject.toml requirements.lock.tx[t] ./
COPY services/api ./services/api
RUN if [ -f requirements.lock.txt ]; then pip install -c requirements.lock.txt -e .; else echo 'WARNING: no requirements.lock.txt - floating dependency versions' >&2; pip install -e .; fi && chown -R app:app /srv
USER app
WORKDIR /srv/services/api
# -B embeds the beat scheduler (job-recovery sweep). In production run exactly ONE beat process.
CMD ["celery", "-A", "app.jobs.celery_app:celery_app", "worker", "-B", "--loglevel=INFO", "--concurrency=2"]
