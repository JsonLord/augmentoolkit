# Gate 04 application correction report

**Gate 04 remains incomplete. No deployment, merge, job execution, remote OCR or automated export is authorized by this report.**

This replaces the unsupported closeout claims in reviewed commit
`f321b09d1e1a531e2c8641f61e91c088f28d271a`. The correction branch is
`fix/gate04-parent-application`, based on that commit, targeting
`jules-agent-0-deployment-9610133099829518547`. It cross-references PR #4;
it does not modify PR #4's branch or the other worktrees.

## Implemented boundaries

- `api.py` imports successfully. A protected router applies the actual HTTPBearer
  dependency in `app_auth.py` to every parent business route: submission, resume,
  fork, interrupt, queue/status/parameters, HF job metadata, file listings,
  output downloads and logs. Missing `WORKER_TOKEN` returns 503; invalid/missing
  credentials with a configured token return 401. Health, documentation and the
  frontend shell are public. Credentials use constant-time comparison.
- `queue_readiness.py` checks both Redis and Huey storage. Typed Redis/Huey
  failures return sanitized 503 responses, including a connection lost after
  readiness. Redis construction has no import-time connection attempt or `None`
  client fallback. Connection timeouts are bounded. Queue availability does
  **not** establish that a consumer is alive.
- Job execution defaults to disabled in both API and worker. HF startup discovery
  requires `ATK_HF_RECOVERY_ENABLED=true`; auto-resume additionally requires the
  execution gate. These settings were not enabled on any running service.
- The root Dockerfile builds the actual Vite frontend and installs pinned,
  hash-verified OCR and parent control-plane dependencies. It runs one
  `uvicorn api:app` process as UID 1000. The parent explicitly enters and exits
  `/ocr`'s lifespan. `ocr/deploy/hf-cpu/Dockerfile` remains unchanged as the
  standalone worker image. The original pipeline `requirements.txt` is preserved.
- `app_storage.py` confines parent/worker writable paths to private data storage.
  Inputs, outputs, generation, external configs, logs and temporary downloads
  reside under `ATK_DATA_ROOT`. Absolute escapes, sibling prefixes and symlinks
  are rejected; recursive listings and archives also reject symlink entries.
  Config reads accept exact operator-maintained source aliases or private external
  configs. Generated training config paths, merged writable config overrides,
  downloaded-input targets and chunk metadata writes are checked. Invalid worker
  storage is rejected before lock acquisition. Logs use exclusive 0600 creation;
  subprocess files inherit umask 077. Full parameter dictionaries are no longer
  printed by the runner.
- Frontend API requests carry an operator token held only in memory, restricted
  to the application's origin, without following redirects. Unlock/lock controls
  are provided; refresh clears credentials. Optional localhost inference probes
  are disabled unless `VITE_LOCAL_SERVICES=true` was explicitly built in.
- Mounted OCR requires explicit D0 on all recognition interfaces. Missing/D1/D2
  requests are rejected. The dispatcher sends the verified D0 classification
  without mutating the original options. CPU results now retain the source
  classification and page reference. Remote OCR initialization is disabled by
  default, including when compatible-provider environment variables are present.
- Unrelated formatting/type modernization across the inherited changes was
  restored to the integration branch version. Historical anonymization evaluations
  and frozen corpora are unchanged. No model-quality assertion was weakened.

## Verification performed locally

Python 3.11.17, host Node 24.19.0/npm 11.9.0, Node 22 in Docker/CI, Docker 28.4.0,
RapidOCR 3.10.0, ONNX Runtime 1.24.1; existing pinned PP-OCRv6 Small models
were checksum-verified at image build and startup. No recognition model or
qualification corpus was introduced.

| Check | Result |
| --- | --- |
| `python -m py_compile api.py tasks.py` | Passed |
| `python -c "from api import app; print('API_IMPORT_OK')"` | Passed |
| `python -m pytest -c pytest-ocr.ini` | 478 passed, 13 skipped |
| `python -m pytest -c pytest-ocr.ini tests/parent` | 101 passed |
| Existing HF persistence tests | 8 passed; Redis/HF/Popen mocked; execution flag scoped to test process only |
| Ruff on OCR, offline chunking, new parent modules/tests | Passed, including format check |
| `npm --prefix atk-interface ci` | Passed with unchanged package lock |
| `npm --prefix atk-interface run build` | Passed |
| `docker build -t augmentoolkit-gate04 .` | Combined image built; proxy CA secret supplied for networked build steps in this environment |
| Non-root read-only image startup | Passed with private tmpfs data and no capabilities |
| Mounted OCR health/readiness/docs | Passed against real combined container |
| Real Redis-unavailable behavior | Authenticated queue/submission requests returned 503; no consumer or pipeline started |
| Synthetic D0 inference | Real CPU recognition returned `Public\nOCR\nbenchmark.`; words match after whitespace normalization, D0 retained |
| Missing/D1/D2 OCR rejection | Nine real HTTP rejection combinations, no recognition for rejected requests |
| Chromium browser | Frontend loaded; token unlock, authenticated Inputs listing, lock passed; no JavaScript errors |

The local functional evidence is in `docs/gate04/local-synthetic-container.json`.
Recognition latency there is a single smoke observation, **not a benchmark or a
privacy qualification**. Initial harness attempts failed on an overly strict
line-break assertion and then a `docker top` invocation omitting its required PID
column. Those attempts are not counted as successful complete checks. Both were
corrected; the complete harness subsequently passed. Local test servers published
only an ephemeral loopback binding and were removed afterward.

The expanded `application.yml` CI builds the combined image, runs parent and
persistence regressions and repeats the synthetic/browser smoke. `ocr.yml` also
runs for parent-only changes. Exact-head GitHub CI results must be checked on the
PR; this document does not treat workflow configuration as evidence of execution.

## Remaining blockers and limits

- No HF redeployment or live Space source/revision verification was performed.
  The existing Space must not be assumed to contain these changes.
- No real Redis/Huey consumer, HF persistence credentials, dataset transmission,
  production recovery, pipeline execution or external OCR provider was qualified.
  Compatible-provider model identity, multimodality and downstream destination
  remain unverified; remote processing stays disabled.
- The image contains the parent control plane and CPU OCR runtime. Specialized
  pipeline/training dependencies remain in the original `requirements.txt` and
  were not installed or exercised as part of this HTTP image qualification.
- Some legacy frontend controls reference backend operations absent from the
  integration API (e.g. config content editing, uploads and LLM routes). They
  return 404; building/rendering the frontend is not end-to-end acceptance of
  every legacy workflow. Optional inference services were not started.
- The filesystem boundary requires service-owned private storage, without an
  untrusted concurrent local writer. It is not a sandbox for arbitrary pipeline
  Python code. Per-pipeline storage behavior and sensitive-log handling still
  require review before execution is enabled. Existing public source paths must
  be migrated into private storage or registered as explicit read-only aliases.
- Thirteen OCR skips remain explicit (opt-in model/remote integrations and optional
  NLTK checks); historical anonymization quality failures remain unchanged.
  The Starlette TestClient deprecation, outdated Browserslist data and existing
  Vite bundle-size warning remain non-failing diagnostics.

**Development review: PARTIAL, pending formal review and exact-head CI. Deployment,
real sensitive-document processing and automated release: NO-GO. Gate 04 is not complete.**
