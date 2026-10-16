# Hybrid OCR release-readiness report

Date: 2026-10-16. Requested base branch commit:
`fe293a173364dfbfc0166f9dd26b0ed1eaef8c57`.
Implementation branch: `implement/hybrid-ocr`, isolated worktree
`/workspace/augmentoolkit-ocr`. Implementation commit and final measured container
results are recorded in the validation addendum below. The original checkout and
its running services were not changed.

**Status: CPU implementation validated locally; production/hybrid provider
qualification incomplete. No production deployment, restart, push or public port
was performed.** The user explicitly deferred live FreeLLMAPI/infrastructure access;
its endpoint and credentials are loaded from runtime environment secrets later.

## Delivered

- Separate authenticated CPU worker, pinned RapidOCR 3.10.0/ORT CPU 1.24.1,
  explicitly pinned/checksummed PP-OCRv6 Small detector and recognizer. Both warm
  and verify `CPUExecutionProvider` before readiness. Inference can run offline.
- Private Debian dispatcher job/page APIs, restricted OpenAI single-image façade,
  CLI, actual OpenAI-compatible `alias-ocr` image adapter and synthetic probe.
- PDF text-layer skip, bounded PDFium rasterization, disposable limited parser,
  image validation, five modes, independent bounded page queues, ordered assembly,
  retries/circuit breakers/budgets, cancellation, TTL/idempotency/result caps.
- Versioned normalized blocks, source identities, sensitivity, route/attempt
  provenance, quality flags and retained alternative evidence; no fake confidence.
- Default D2 classification and hard fail-closed D1/D2 remote routes, including
  hidden fallback assumptions. Separate HF worker secret, no FreeLLMAPI key in HF.
- Explicit Augmentoolkit client/anonymization handoff; legacy Tesseract/Presidio
  behavior remains untouched. OCR evidence is never automatically declassified.
- Hashed dependency lock, separate HF Docker Space files, environment template,
  offline CI, CC0 synthetic DE/EN fixture generator, benchmark runner, audit,
  threat model, API docs, ADRs, deployment/approval/rollback runbook.

Actual files live under `ocr/`, `.github/workflows/ocr.yml`, `pytest-ocr.ini`,
`spec.md`, this report and `docs/ocr-benchmarks/`. `spec.md` is byte-identical to
`spec (10).md`. See `ocr/docs/adr.md` for explicit deviations/remaining work.

## Validation evidence

Commands were executed from `/workspace/augmentoolkit-ocr`, using an independent
Python 3.11.17 venv at `/workspace/.ocr-venv`. Existing app dependencies were not
modified. Exact test totals and container digest are in the addendum.

```sh
OCR_MODELS_DIR=/workspace/.ocr-models OCR_RUN_LIVE_CPU=1 \
  /workspace/.ocr-venv/bin/python -m pytest -c pytest-ocr.ini
/workspace/.ocr-venv/bin/ruff check ocr
/workspace/.ocr-venv/bin/python -m ocr.cli run /tmp/ocr-review-fixtures/de-scan.pdf
/workspace/.ocr-venv/bin/python -m ocr.cli run /tmp/ocr-review-fixtures/en-200.png
```

**Real execution:** model artifact TLS/checksum validation; model initialization
and CPU inference; one image and scanned PDF via the real dispatcher API; DE PDF
and EN image via CLI; synthetic printed/blurred/rotated/blank/table/column/receipt
corpus; mixed 20/100-page PDFs; Docker dependency/model build; real HTTP inside a
read-only, network-disabled, two-CPU/two-GiB worker container with no host ports.
Worker health/readiness/auth and actual recognized text were asserted.

**Mocked execution:** alias request/response contract, 429/502/503, null/empty/
non-text/reasoning/truncated outputs; two-lane out-of-order completions, selective
escalation, fallback, timing-based selection, worker unavailability, provider
substitution denial, retries/budget/health changes. These tests do not establish
live GPU speed, model identity, grounding or provider authorization.

**Unrun/skipped:** live alias probe, Debian-Devil connectivity, actual HF Space
build/deployment/sleep tests, real GPU/hybrid benchmark/cost/quota, OpenVINO,
genuine human handwriting corpus, human-annotated box IoU and semantic table
fidelity. CI is configured but was not executed on GitHub by this task.

### Initial cloud baseline (not HF Basic)

`docs/ocr-benchmarks/cloud-baseline.json` retains the initial uncommitted baseline,
including base revision and its explicit working-tree annotation. Python 3.11.17,
5 logical CPUs visible; ORT intra-op 2/inter-op 1. 16 synthetic fixtures, three
runs each. Model init 4.38 s; measured OCR latency p50 1.76 s, p95 3.87 s, p99
4.64 s; peak process RSS 855.8 MiB. This is a local baseline, not a service SLA.

Mixed PDF batches preserved page order and used embedded text on half the pages:
20 pages in 21.31 s (56.32 pages/min), 100 in 103.10 s (58.19 pages/min).
EN/DE clean printed 100/200/300 DPI fixtures and text-layer PDFs had 0 CER/WER on
this small synthetic corpus. The blur fixture had CER 4.30%, WER 37.5%; rotated
scan CER 78.49%, WER 93.75%. The final quality policy flags blur/low contrast and
rotated low-confidence output for review. These results do not generalize to
unseen scans; no human independent corpus review is claimed.

The final two-CPU container run records runtime source digest, cgroup CPU/memory
limits, per-fixture errors/latencies, original-order checks and resource metrics
separately. Neither CPU-only run qualifies hybrid overflow.

## Acceptance matrix

| Requirement | Evidence/status |
|---|---|
| Verified CPU versions/tier/weights | Real initialization, checksum and CPU provider tests pass |
| Authenticated bounded HF worker | Docker build and local real HTTP pass; actual HF account deployment deferred |
| Dispatcher jobs and independent page queues | API, limits, order, partial failure, cancellation and TTL tests pass |
| Actual multimodal alias adapter/probe | Code/contract mocks pass; live verification deferred, disabled by default |
| D2 actual destination/fallback enforcement | All unauthorized remote routes fail closed; no audited protocol to enable D2 alias |
| Five routing modes | Unit/integration tests pass; quality/layout capability qualification remains conservative |
| Throughput balancing | EWMA/hysteresis/budget gates tested with mocks; real hybrid >15% gate unqualified/off |
| PDF skip/raster/image end-to-end | Real CPU API, CLI and mixed batch checks pass |
| Normalization/provenance/no invented confidence | Geometry/null-box/ordering/alternative evidence tests pass |
| Safe downstream handoff | Fail-closed typed bridge tests pass; no production PII model integration claimed |
| Corpus and benchmark | Reproducible synthetic CPU evidence; handwriting/human box/table qualification outstanding |
| Production safety | Original checkout clean; baseline listeners unchanged; no deployment or service restart |

## Outstanding release gates and owner actions

1. Choose a **separate** CPU Space and approve its D0 processing boundary. Provision
   only `OCR_CPU_AUTH_TOKEN` there. Do not overwrite `Leon4gr45/augmentanonymous`.
2. Later, provision the actual private `FREELLMAPI_BASE_URL` and
   `FREELLMAPI_API_KEY` on the dispatcher. Run a safe D0 image probe and investigate
   actual serving model/provider, quota, image support and grounding with its
   operator. A successful alias name/models listing is insufficient.
3. Keep D2 local. An audited, enforceable executed-destination/fallback contract
   against the approved inference catalogue is required for any future D2 alias
   change. Metadata or a feature flag alone cannot enable it in this code.
4. Run authenticated HF CPU Basic cold/warm/memory/sleep checks and a permitted
   real hybrid benchmark. Keep overflow disabled until >15% end-to-end gain is
   measured on mixed 20+/100-page batches. Do not infer it from mocked timing.
5. Validate a representative human-reviewed DE/EN/handwriting/layout corpus and
   calibrate quality thresholds. Rotated internal text, blur, complex tables,
   column order and handwriting have explicit limitations. Pin/check licenses and
   review infrastructure-specific controls before sensitive production ingestion.

No raw documents, images, keys or production URLs are included in benchmark
aggregates or logs. Model weights are external build artifacts, not git contents.
The package is not a regulatory compliance certification. ModelScope metadata declares the RapidAI/RapidOCR model repository Apache License
2.0; RapidOCR is Apache-2.0, ONNX Runtime MIT, Pillow MIT-CMU, pypdf BSD-3-Clause
and pypdfium2 includes BSD-3-Clause/Apache-2.0/dependency notices. These licenses
and bundled PDFium third-party notices must be retained/reviewed in deployment;
no additional rights are asserted for user documents or downstream providers.

Rollback and exact approval steps: `ocr/docs/deployment.md`. Disable new ingress
or select `cpu_only`/disable GPU; stop only the new deployment after approval.
No database migration or rollback of existing services is required.

## Validation addendum

Pending final committed-source two-CPU container measurement. No release-ready
claim is made while the external gates above remain unresolved.
