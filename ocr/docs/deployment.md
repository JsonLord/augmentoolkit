# Installation, deployment review and rollback

Do not run the original `linux.sh`, root Dockerfile, or an HF upload as part of this
OCR rollout: they manage the existing application. No production deploy/restart
has been authorized. Work in the isolated implementation worktree.

## Reproduce locally

Python 3.11, Linux x86_64. Use a separate venv; no dependency change to the existing
application is necessary. Keep TLS and package/model checksum verification enabled.

```sh
python3.11 -m venv /tmp/ocr-venv
/tmp/ocr-venv/bin/pip install --require-hashes -r ocr/requirements.lock
/tmp/ocr-venv/bin/pip install -r ocr/requirements-dev.txt
export OCR_MODELS_DIR=/opt/ocr-models  # select an operator-writable location
/tmp/ocr-venv/bin/python -m ocr.download_models
/tmp/ocr-venv/bin/python -m pytest -c pytest-ocr.ini
OCR_RUN_LIVE_CPU=1 /tmp/ocr-venv/bin/python -m pytest -c pytest-ocr.ini ocr/tests/test_live.py
/tmp/ocr-venv/bin/python -m ocr.fixtures.generate /tmp/ocr-fixtures
/tmp/ocr-venv/bin/python -m ocr.cli run /tmp/ocr-fixtures/de-scan.pdf --format markdown
```

`ocr/.env.example` lists settings without secrets. The application reads environment
variables directly; it does not load arbitrary `.env` files. On Debian, a reviewed
service manager can inject an EnvironmentFile with mode 0600. On HF, enter values
under Space Settings → Secrets; those become environment variables at startup.

The dispatcher uses `OCR_DISPATCHER_TOKEN`; the CPU Space uses only
`OCR_CPU_AUTH_TOKEN`. Configure the dispatcher with that token plus `OCR_CPU_URL`.
`FREELLMAPI_BASE_URL` must be the actual existing private API URL including `/v1`;
`FREELLMAPI_API_KEY` stays on Debian. `OCR_GPU_MODEL` must remain `alias-ocr`.
They can be filled later from runtime secrets, as requested; there are no stored
credential values or guessed endpoints. Never place FreeLLMAPI keys in a public
CPU worker. If a separate HF-hosted dispatcher is later desired, it needs its own
trust review and cannot reach a Debian loopback address by itself.

Start the dispatcher only on an operator-approved loopback socket or Unix socket,
for example through existing private ingress using `uvicorn ocr.api:app --uds
/path/to/approved/ocr.sock --workers 1 --no-access-log`. Select the approved writable
socket location; do not create new public listeners. One process is required for
consistent in-memory jobs. Existing services need no restart.

## Separate CPU Space

Review the target Space identity, privacy, token and D0-only egress policy first.
`Agent.md` names the existing application Space, not a newly selected OCR Space.
Do not overwrite it. Stage only `ocr/`, `ocr/deploy/hf-cpu/Dockerfile` as root
`Dockerfile`, and `ocr/deploy/hf-cpu/README.md` as root `README.md`. The README
contains Docker SDK/app_port 7860 metadata. CPU worker never calls Debian.

Build from repository root:

```sh
docker build -f ocr/deploy/hf-cpu/Dockerfile -t hybrid-ocr-cpu:review .
```

In the managed cloud proxy, supply its public trust bundle as an ephemeral BuildKit
secret (`--secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt`). Do not disable
TLS verification. Build-time destinations: package registry, Debian signed package
mirrors and pinned ModelScope model URLs/CDNs. Models are checksum-verified and
baked into the image. No inference-time model download is needed.

The reviewed image runs non-root, one Uvicorn worker, ORT intra-op 2/inter-op 1 and
OpenCV 2 threads. Test with CPU/memory limits and no host port publication. A real
HF CPU Basic deployment must be explicitly approved, followed by authenticated
health/readiness, public-fixture OCR, memory/cold-start measurements, sleep/wake
checks and D0-only dispatcher integration. No availability guarantee for sleeping
Spaces is made.

## Alias qualification later

Set the private URL/key securely, leave sensitive data out, then run
`python -m ocr.cli probe-alias`. Review observed model/provider output with the
FreeLLMAPI operator. `/v1/models` or an alias name alone cannot qualify routing.
`OCR_ENABLE_GPU=true` permits only D0 after a successful multimodal challenge;
D1/D2 remain blocked. Grounding/provider enforcement must be separately audited
before any D2 extension. Reprobe after model/config changes; qualification expires
after one hour, after which requests fail closed until a controlled restart/reprobe.

Overflow stays disabled until real CPU/GPU mixed 20+/100-page batches demonstrate
>15% end-to-end gain under approved budgets. Enable both operator gates plus caller
permission only after retaining that evidence. Mocked timing tests are not evidence.

## Rollback

Disable new dispatcher ingress or set `OCR_ENABLE_GPU=false` and select `cpu_only`.
Stop only this new worker/dispatcher after owner approval. Revert the implementation
commit in a review branch if needed; no migration or existing service rollback is
required. Retained in-memory jobs are lost on restart; clients must resubmit with
explicit awareness that remote work may already have executed. Keep D2 local until
an audited destination enforcement design is delivered.
