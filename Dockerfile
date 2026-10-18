# syntax=docker/dockerfile:1
FROM python:3.11.17-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 \
    OCR_MODELS_DIR=/opt/ocr-models HOME=/home/ocr
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ARG OCR_SOURCE_REVISION=unknown
ENV OCR_SOURCE_REVISION=$OCR_SOURCE_REVISION
WORKDIR /app
# Build from repository root. Only OCR dependencies and code enter this separate worker.
COPY ocr/requirements.lock /tmp/requirements.lock
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY ocr /app/ocr
# Download once at build time, verify pinned SHA256s, keep inference offline.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    python -m ocr.download_models
RUN useradd --uid 1000 --create-home ocr && chmod -R a+rX /opt/ocr-models /app
USER ocr
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health',timeout=3)"
CMD ["uvicorn", "ocr.worker_api:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-access-log", "--limit-concurrency", "4"]
