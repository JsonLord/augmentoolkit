# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY atk-interface/package.json atk-interface/package-lock.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci
COPY atk-interface/ ./
RUN npm run build

FROM python:3.11.17-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 \
    OCR_MODELS_DIR=/opt/ocr-models HOME=/home/atk ATK_DATA_ROOT=/data \
    ATK_JOB_EXECUTION_ENABLED=false HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ARG OCR_SOURCE_REVISION=unknown
ENV OCR_SOURCE_REVISION=$OCR_SOURCE_REVISION AUGMENTOOLKIT_CODE_REVISION=$OCR_SOURCE_REVISION
WORKDIR /app
COPY ocr/requirements.lock /tmp/ocr.lock
COPY requirements/parent.lock /tmp/parent.lock
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --require-hashes -r /tmp/ocr.lock -r /tmp/parent.lock
COPY ocr/download_models.py ocr/models.json /tmp/model-download/
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    python /tmp/model-download/download_models.py
COPY . /app
COPY --from=frontend /frontend/dist /app/atk-interface/dist
RUN useradd --uid 1000 --create-home atk && mkdir -p /data && chown atk:atk /data && chmod 700 /data \
    && chmod -R a+rX /opt/ocr-models /app
USER atk
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health',timeout=3)"
# One ASGI listener; the parent explicitly enters the mounted /ocr lifespan.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-access-log", "--limit-concurrency", "32"]
