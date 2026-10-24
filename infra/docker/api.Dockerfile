FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
RUN useradd --create-home --uid 10001 app && mkdir -p /srv/storage && chown app:app /srv/storage  # a new named volume copies this ownership
COPY pyproject.toml requirements.lock.tx[t] ./
COPY services/api ./services/api
RUN if [ -f requirements.lock.txt ]; then pip install -c requirements.lock.txt -e .; else echo 'WARNING: no requirements.lock.txt - floating dependency versions' >&2; pip install -e .; fi && chown -R app:app /srv
USER app
WORKDIR /srv/services/api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz',timeout=2).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
