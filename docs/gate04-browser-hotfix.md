# Gate 04 browser hotfix qualification

This is a local, synthetic release candidate based on PR #5 (`e009700411f9a0cead10725f47c1febd902fcff9`). It has not been deployed. Gate 04 stays open pending separately authorized staging qualification. No job execution, HF persistence, provider integration or sensitive processing is enabled.

## Runtime changes

The Docker command raises Uvicorn's HTTP connection/task admission limit from 8 to 32, retaining one worker, one listener and the existing single CPU-backend lifespan. OCR admission, authentication and upload limits are unchanged. No frontend retries were added. A higher limit of 64 was unnecessary because 32 passed.

The FastAPI fallback serves only declared React document routes: `/`, `/inputs`, `/outputs`, `/configs`, `/monitor`, `/logs`, `/chat`, and one-segment detail routes `/configs/:configId` and `/logs/:taskId`. Existing backend routes resolve before this fallback. Reserved API operation names cannot become detail IDs. Unknown paths and missing assets return JSON 404; only existing files in `/assets/` are served as assets. Parameterized React routes identify documents, not data authorization. `/ocr` remains the mounted worker. Operator credentials remain memory-only.

## Before/after

| Metric | Baseline | Hotfix |
| --- | --- | --- |
| Uvicorn concurrency limit | 8 | 32 |
| Worker/listener count | 1 | 1 |
| Frontend deep-link documents | 6/9 returned 404 | 9/9 returned HTML 200, including browser refresh |
| Eight idle HTTP connections plus health request | 503; one concurrency warning | 200 |
| Browser reloads | Not qualified | 10/10 |
| Client route navigations | Not qualified | 20/20 |
| Unintended 404/503 in defined hotfix workload | Baseline defects reproduced; full workload not run | 0 |
| Concurrency-limit warnings | 1 in deterministic reproduction | 0 |
| Synthetic D0 CPU OCR | Previously qualified | Passed; 1.7132 seconds including local HTTP |
| Missing/D1/D2 across three OCR interfaces | Previously qualified | 9/9 rejected with 422 |
| Auth / disabled queue behavior | Existing boundaries | Preserved; missing/invalid 401, intentional queue/submission 503 |
| Job execution | Disabled | Disabled |

The baseline reproduction used the existing combined image `sha256:5cabaf4b25f02e7a6c82d3a5c83a7799c9b1b81eae7e33d7f0181a47bad99e6e`. Eight deliberately held idle connections demonstrate Uvicorn's connection-budget behavior; this is not a claim that the complete browser workload was executed against the baseline. User-supplied staging logs independently reported “Exceeded concurrency limit.”

The local hotfix image was `sha256:988df3fff72e447418714296333bad8abfbef8a5a419560bb8e0cfd2b0ccad73`, built with `OCR_SOURCE_REVISION=p0-local-candidate`. Runtime source hashes are recorded in the evidence. CI rebuilds from the exact final commit with its real SHA. The harness executes from the checkout; its final overlap assertion was also checked against recorded timing intervals without repeating inference.

## Workload and resource evidence

`tests/parent/container_smoke.py` starts a fresh non-root, read-only container with private tmpfs storage and an ephemeral loopback port, and removes it on exit. It records HTTP status/content type/duration and constant-only server log counts, without tokens or raw log payloads. No real document, dataset write or provider request is used.

The workload combines six static-asset clients, five authenticated read-only listing clients, one real synthetic D0 OCR request, browser reloads/navigation and direct refresh of every supported document route. Recorded intervals prove all five listing requests overlap. The text matches `Public OCR benchmark.` after whitespace normalization, retaining D0, cpu_ocr, pp-ocrv6-small and local-cpu provenance. There is one successful inference per complete smoke run, no inference retries.

The final local run observed 790.73 MiB maximum sampled RSS, 847.02 MiB process peak RSS, 11.40 MiB final RSS growth from initialized startup, and average cgroup CPU use of 0.229 cores (22.9% of one core). Sampling includes diagnostic exec overhead. The smoke asserts final RSS growth under 128 MiB; this is a short-workload bound, not a long-term leak or concurrency-capacity benchmark. Exactly one Uvicorn process, one startup and no job worker were observed. Native model objects were not separately instrumented; the unchanged lifespan constructs the one CPU backend in this one process.

The browser expects JSON 401 on data requests after refresh clears credentials. Known missing configuration-content operations and JSON Redis-unavailable responses are recorded separately from document failures. A plain Uvicorn 503 cannot be reclassified as an intentional queue failure. Missing assets remain 404. These exclusions do not enable unqualified features.

Two earlier harness issues were corrected before the passing run: classification probes omitted their operator header, and a repeated Inputs click incorrectly expected a fresh network response despite frontend caching. A subsequent strict check also needed to distinguish expected locked-state 401 responses. Failed-run evidence remains outside the repository in `/workspace/p0-after-harness-*.json`; no failed run is counted as passing. Two earlier local smoke attempts completed a synthetic inference before later harness assertions failed; the final successful run contains exactly one additional inference. No inference was repeated to optimize recognition.

## Commands and scope

Run from the repository, using Python 3.11 and the pinned OCR/parent dependencies:

```bash
python -m py_compile api.py tasks.py
python -c 'from api import app; print("API_IMPORT_OK")'
ruff check app_auth.py app_storage.py queue_readiness.py tests/parent
python -m pytest -c pytest-ocr.ini tests/parent
ATK_JOB_EXECUTION_ENABLED=true python -m pytest -c pytest-ocr.ini tests/test_hf_space_recovery.py
python -m pytest -c pytest-ocr.ini
npm --prefix atk-interface ci
npm --prefix atk-interface run build
docker build --build-arg OCR_SOURCE_REVISION="$(git rev-parse HEAD)" -t augmentoolkit-gate04 .
python tests/parent/container_smoke.py --evidence /tmp/gate04-container-results.json
```

Local results: Python compilation/import and Ruff passed; **141 parent tests passed**, including 40 new routing/admission regressions; **478 OCR tests passed, 13 skipped** (existing opt-in model/provider tests); **8 mocked HF recovery tests passed**. The temporary true execution flag above is limited to that existing mock-only test process; it does not start a consumer or alter container/staging flags. React dependency install/build and Docker build passed. Vite reports existing bundle-size/dynamic-import warnings; npm audit findings were not remediated in this focused patch. A managed-environment build also supplies the public CA via `--secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt` and uses its writable Buildx configuration. TLS verification stays enabled.

The existing parent GitHub workflow already builds the complete image and runs this expanded smoke harness; the OCR workflow runs the default OCR suite. Exact-head CI URLs and the final candidate SHA are returned with the PR. No historical recognition corpus/report was changed.

## Staging upload preparation — not authorization to execute

After formal approval, prepare a clean payload from the final reviewed commit. Preserve the existing staging payload exclusions (runtime storage, `.env*`, repository metadata and local dependency/build directories), retain `sdk: docker` and `app_port: 7860`, change the README title to `augment`, pin Dockerfile's default `OCR_SOURCE_REVISION` to the reviewed GitHub SHA, and add `STAGING_SOURCE.txt` with that SHA. Compare the payload manifest before upload. Do not upload a mutable worktree, secret files or local evidence containing credentials.

The prepared payload and exact source SHA are supplied alongside delivery. The upload command, **not executed**, is:

```bash
hf upload Leon4gr45/augment /workspace/gate04-p0-upload/payload . \
  --repo-type space --revision main \
  --commit-message "Gate04 P0 browser hotfix from SOURCE_SHA"
```

Replace SOURCE_SHA with the delivered full SHA. Before executing, independently verify that the target is still private and its current revision remains `0f72df3ea434c72fd6c6144149b50c373ff0a2cf`; if it changed, stop and re-review. Use a parent-commit guard through the official Hub API wrapper, as in the previous approved upload, because the CLI alone does not enforce this guard. An upload can trigger a rebuild/restart and requires separate authorization. Do not change secrets or enable any execution flags.

The rollback source remains the current staging revision `0f72df3ea434c72fd6c6144149b50c373ff0a2cf`; rollback would also require separate authorization. PR #5 remains open and unmerged. This hotfix is stacked on PR #5 so its diff contains only these P0 changes.

## Remaining qualifications

Local patch qualification passes. Staging deployment and Gate 04 completion are **not qualified**: the running Space still uses the old revision. The effective HF private gateway identity/cookie plus application Bearer boundary remains unresolved from prior testing. Recheck it during an authorized staging qualification, together with resource observations and the fixed browser workload. Real Redis consumers, HF persistence, external providers, sensitive documents and automated release remain NO-GO. `automated_release_eligible=false`.
