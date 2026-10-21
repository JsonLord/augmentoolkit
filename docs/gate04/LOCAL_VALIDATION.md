# Reproduce the private application checks

Use Python 3.11 and Node 22. Install OCR and parent locks separately from the
original full pipeline dependency list:

```sh
python -m pip install --require-hashes -r ocr/requirements.lock -r requirements/parent.lock
python -m pip install -r ocr/requirements-dev.txt playwright==1.55.0
python -m playwright install --with-deps chromium
python -m py_compile api.py tasks.py
python -c "from api import app; print('API_IMPORT_OK')"
python -m pytest -c pytest-ocr.ini
python -m pytest -c pytest-ocr.ini tests/parent
ATK_JOB_EXECUTION_ENABLED=true python -m pytest -c pytest-ocr.ini tests/test_hf_space_recovery.py
npm --prefix atk-interface ci
npm --prefix atk-interface run build
docker build -t augmentoolkit-gate04 .
python tests/parent/container_smoke.py
```

The legacy persistence test's temporary execution flag applies only to the mocked
unit-test process, not a worker or application server. Do not export it globally.
The container harness generates its own synthetic operator credential and one
synthetic D0 image, uses an ephemeral **loopback-only** port and removes only its
own container in a `finally` block. It requires no real Redis, HF or OCR provider
credentials. Browser verification uses system Chromium if found, otherwise the
Playwright-installed browser. Do not use real documents in these checks.

In the managed cloud environment, caches had to use writable workspace paths:
`UV_CACHE_DIR=/workspace/.uv-cache`, `npm_config_cache=/workspace/.npm-cache`, and
`BUILDX_CONFIG=/workspace/.docker-buildx`. The Python venv was
`/workspace/.ocr-venv`. TLS verification remained enabled. The first networked
Docker build also supplied
`--secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt`; ordinary CI needs no
session proxy secret. These are environment overrides, not application settings.

Runtime defaults keep queue execution and remote OCR off. The data mount must be
owned by UID 1000 with mode 0700; the smoke test supplies an isolated tmpfs. Keep
source/model files read-only. Set `WORKER_TOKEN` securely for authenticated private
checks; do not embed it in the image or frontend bundle. The token grants broad
operator access and is not a human identity/review integration.

Rollback requires stopping only a separately authorized staging instance and
selecting its previous image; no existing runtime was modified by this task.
Do not roll source backward while assuming private data paths automatically
migrate back. Export/backup classified storage only through an independently
approved process. Nothing here authorizes deployment, persistent job execution,
HF synchronization or automatic exports.
