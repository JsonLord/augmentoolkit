# Hybrid CPU-First OCR Dispatcher — `spec.md`

**Version:** 1.0 (implementation specification)  
**Date:** 2026-10-16  
**Status:** Proposed; not implemented or deployed by this document  
**Primary engine:** PP-OCRv6 Small via RapidOCR + ONNX Runtime on CPU  
**Secondary engine:** existing FreeLLMAPI `alias-ocr` through an OpenAI-compatible endpoint (actual backing model/capabilities must be verified)  
**Preferred deployment:** separate Hugging Face CPU Basic OCR worker; private Debian-Devil dispatcher/control plane  
**Integration targets:** document ingestion, Augment Toolkit, deterministic PII tagging/anonymization

## 0. Decision record

Build a **CPU-first, page-level hybrid OCR pipeline** with two independent queues:

- **CPU lane (default):** RapidOCR >=3.9 PP-OCRv6 **Small** text detection + recognition, explicitly configured/pinned and served on a Hugging Face CPU Space or trusted local CPU installation. Prefer ONNX Runtime initially; compare OpenVINO after establishing a baseline.
- **OpenAI-compatible alternative lane:** the existing FreeLLMAPI model alias `alias-ocr`, **expected** to map to LightOnOCR-3-4B. Treat this as a hypothesis until its *actual serving model, image delivery, output and fallback destinations* are verified with a safe live probe. Never silently substitute a generic LLM.
- **Modes:** `auto` (default CPU-first + selective escalation), `throughput` (parallel distribution subject to measured capacity), `quality` (GPU for complex eligible pages, dual-run on selected pages), `cpu_only`, and `gpu_only` (explicit user policy still applies).
- **No naive round-robin** and no redundant GPU transcription of every page. Schedule eligible pages by complexity, engine capabilities, quality checks, latency, queue pressure, destination approval and budget.
- **Dispatcher on Debian-Devil:** communicates outbound with the CPU Space and with the **existing loopback/private FreeLLMAPI API**; it must not require the HF Space to reach a Debian `127.0.0.1` endpoint. Never expose a new public Debian port.
- **Pre-existing PDF text:** extract/validate embedded text *before* OCR, retaining source and confidence of method; do not re-OCR readable born-digital pages unless requested or extraction is suspect.
- **Privacy-first:** enforce trust/destination policy on every choice and again on every retry/fallback. A public HF Space is **not automatically approved for D2-sensitive documents**.

### Explicitly out of scope for v1

- Replacing existing FreeLLMAPI routing policies or altering its production containers.
- Training or fine-tuning either OCR model; Unlimited OCR, PP-OCRv6 Tiny/Medium and unrelated VLMs are not shipping backends.
- Promising semantic table understanding from PP-OCRv6 text recognition alone.
- Long-lived distributed queues, user billing, arbitrary web crawling, unsupervised PII publication, or GPU provisioning.
- Automatic production deployment, service restart, adding a public port, or modifying unrelated services.

## 1. Why this architecture

PP-OCRv6 Small offers a strong lightweight recognition baseline. RapidOCR >=3.9 defaults to PP-OCRv6 Small detection and recognition, but **explicitly configure both model tiers** and pin tested versions to prevent silent changes. LightOnOCR-3 offers markdown document reconstruction and, **only when the backing model supports it**, grounded layout-block coordinates. FreeLLMAPI provides an existing OpenAI-compatible `alias-ocr` entry; an alias alone does not prove a working multimodal path, LightOnOCR version, quota, security boundary or grounding support.

Hugging Face CPU Basic is reported as 2 vCPU / 16 GB RAM; capacity, sleep behaviour and availability vary by account and subscription. **All performance targets are measurement goals, not promises.** Keep backend interfaces modular so CPU can later move to trusted Debian/DGX hardware without API changes.

## 2. Target topology

```text
Augment Toolkit / document clients / local CLI
                    |
           [private ingress]
                    |
      Debian-Devil OCR dispatcher
       (loopback / trusted IPC)
        |     |         |
        |     |         +-- PDF text-layer extraction, page rasterization,
        |     |             metadata/sensitivity validation, routing, queue,
        |     |             quality checks, assembly, anonymization handoff
        |     |
        |     +-- existing FreeLLMAPI /v1/chat/completions
        |              model="alias-ocr"  --> approved actual inference provider
        |
        +-- HTTPS --> Hugging Face CPU Space, authenticated OCR worker
                         RapidOCR + PP-OCRv6 Small (ONNX)
```

**Important:** the HF CPU worker is a stateless page transcription service. The orchestrator, FreeLLMAPI keys, document assembly, job queue and sensitivity decisions live on Debian; a public worker must not receive confidential documents simply because it is reachable.

Recommended packages, adapted to the chosen repository's conventions:

```text
ocr/
  schemas.py             # Pydantic request, result, capability and error types
  ingest.py              # PDF/image validation, rasterization, text-layer checks
  classify.py            # deterministic page features
  security.py            # sensitivity and destination authorization
  routing.py             # deterministic scheduler, queue pressure, policies
  quality.py             # OCR confidence, completeness and structural checks
  normalize.py           # unified text + blocks + geometry, stable ordering
  adapters/
    rapidocr_cpu.py      # native/HTTP CPU adapter as appropriate
    freellm_alias.py     # OAI-compatible multimodal requests, alias-ocr
  jobs.py                # bounded in-memory job state, cleanup and cancellation
  api.py                 # private dispatcher FastAPI service
  worker_api.py          # HF CPU-only worker FastAPI service
  cli.py                 # CLI smoke/bench harness
  tests/
  fixtures/
  deploy/hf-cpu/        # Dockerfile, Space README, requirements/lock
  docs/
    routing.md
    threat-model.md
    benchmark.md
```

If the repo already has equivalents, **reuse them** instead of duplicating architecture. Do not force this directory tree onto an incompatible existing project.

## 3. Backend contracts

### 3.1 CPU adapter / worker

- Runtime: Python + RapidOCR **>=3.9** (pin exact tested versions); use PP-OCRv6 Small **detector and recognizer**, not the medium default from other PaddleOCR APIs. ONNX Runtime CPU; compare OpenVINO as an optional alternative.
- Load/warm once per process (account for lazy loading in recent RapidOCR). One CPU model worker initially on a two-vCPU Space. Avoid gunicorn/uvicorn worker duplication of model memory.
- Accept **one bounded image/page per request**, decode only supported image types, verify dimensions and byte limits; no arbitrary URL fetching.
- Return transcription, per-line text, score and quadrilateral coordinates, model/backend version, image dimensions, elapsed time. Do not claim high-level tables if unavailable.
- Prefer `POST /internal/ocr/page` with `multipart/form-data` (`file`, `request_id`, optional `options` JSON). Authenticate using separate **worker token** over TLS when remotely reachable. Health endpoints: `/health` (liveness) and `/ready` (model initialized); optionally `/internal/capabilities` with supported scripts, engine and version.
- Do not log images, OCR text, bearer tokens or personal data. Sanitize operational errors. Cap CPU thread pools (`OMP_NUM_THREADS`, `ORT_*` or engine equivalents) and profile in the actual Space.
- Package model weights deterministically or pin verified downloads/revisions; warm-up must fail visibly if weights cannot be loaded. Make cache/sleep and ephemeral disk limitations explicit.

### 3.2 FreeLLMAPI `alias-ocr` adapter

- Config: `FREELLMAPI_BASE_URL` (existing private URL including `/v1`), `FREELLMAPI_API_KEY` secret, `OCR_GPU_MODEL=alias-ocr`, bounded timeout, concurrency and request budget. **Never hardcode credentials.**
- Perform startup capability and routing investigation before enabling the GPU lane: resolve actual backing model **if observable**, ensure a safe D0 synthetic image reaches a multimodal provider, record returned effective destination and validate grounding behaviour. `/v1/models` alone is insufficient.
- Send OpenAI-compatible `POST /v1/chat/completions` with an **image data URL** inside `messages[0].content`, `model: "alias-ocr"`, `stream: false`. If verified LightOnOCR-3, user content contains `image_url` plus optional text `grounding`; if not verified, use only the empirically supported prompt/response contract.
- LightOnOCR-3 recommendations (apply only if provider supports/accepts them): page longest edge ~2048 px; `temperature` 0.1–0.2; `enable_thinking: false` only when supported; use `grounding` for layout blocks. **Do not send provider-specific parameters blindly** through FreeLLMAPI.
- Parse ordinary OpenAI completion content, handling null/empty, provider errors, truncation, non-text content and markdown tables. Preserve raw output internally for controlled diagnostics only, not persistent sensitive logs.
- Grounding outputs from LightOnOCR-3 use block markers like `![table](x1,y1,x2,y2)` with normalized 0..1000 coordinates. Parse defensively; plain markdown is not guaranteed to include boxes.
- Never interpret HTTP 200 with empty/truncated output as successful OCR. Confirm enough content for the page, but do not fabricate text. Handle 429/503/502/timeouts with retry budget and circuit breaker.
- Verify the **actual provider and any fallback** before each D2-sensitive request. If a trustworthy executed-destination identity cannot be obtained/enforced, GPU lane is **unqualified for D2** and must fail closed for D2.

### 3.3 Stable shared adapter interface

```python
class OCRBackend(Protocol):
    async def capabilities(self) -> BackendCapabilities: ...
    async def recognize(self, page: RasterPage, options: OCROptions) -> OCRPageResult: ...
    async def health(self) -> BackendHealth: ...
```

Capabilities should explicitly indicate `text`, `bounding_boxes`, `layout_blocks`, `tables`, `math`, `languages`, `max_image_px`, `supports_sensitive_classes`, `effective_destination_verifiable` and `grounding_verified`. **Do not mark an unsupported feature as true based on model name alone.**

## 4. Public/private API contract

Prefer job-oriented REST for PDF/batches and a small synchronous path for one image. Keep an optional OpenAI-compatible single-image façade separate from the true job API.

### Core dispatcher endpoints

- `POST /v1/ocr/jobs` — authenticated `multipart/form-data`: `file` (PDF / PNG / JPEG / TIFF where supported), `options` JSON. Returns **202** with `{job_id, status, status_url, result_url, request_id}`.
- `GET /v1/ocr/jobs/{job_id}` — status: `queued | running | completed | partial | failed | cancelled`, counts, timestamps, non-sensitive errors.
- `GET /v1/ocr/jobs/{job_id}/result` — only if ready; JSON envelope with assembled Markdown, pages, normalized blocks, quality flags and per-page provenance.
- `DELETE /v1/ocr/jobs/{job_id}` — cancel outstanding work and purge transient stored data according to state; no false guarantee of cancelling remote work that already finished.
- `POST /v1/ocr/page` — synchronous one-page OCR, bounded request size; same normalized page contract.
- `/health`, `/ready`, optional `/metrics` **loopback/private access only**. Do not expose sensitive labels or content in metrics.

### Example job options

```json
{
  "mode": "auto",
  "sensitivity": "D0",
  "output": "both",
  "language_hints": ["de", "en"],
  "layout": "auto",
  "preserve_boxes": true,
  "extract_pdf_text_layer": true,
  "prefer_cpu": true,
  "allow_external_cpu": true,
  "allow_gpu_overflow": false,
  "max_pages": 100
}
```

`output`: `text | markdown | structured | both`. These are **OCR-service-specific** fields, not OpenAI parameters. Defaults and user-supplied flags **cannot weaken server policy**. `sensitivity` may only be elevated by callers; server-side classification/policy takes precedence when stricter.

### Optional OpenAI-compatible façade (phase 2)

- If the host application benefits from an OAI client, expose **single-image only** `POST /v1/chat/completions` with `model: "ocr-auto"`, `"ocr-cpu"` or `"ocr-gpu"` and supported `image_url: {url: "data:image/png;base64,..."}`. Return standard non-streaming chat completions with normalized text/markdown and model metadata.
- Do **not** claim full OpenAI endpoint compatibility. Reject PDF, remote image URLs, arbitrary chat prompts, tools/functions and streaming in this façade unless explicitly implemented and tested. Do not invent token usage.
- **Do not modify existing FreeLLMAPI `/v1/chat/completions` to implement this.** The façade (if shipped) belongs to the OCR dispatcher as a separate optional interface.

### Result schema (illustrative; implement versioned Pydantic models)

```json
{
  "schema_version": "ocr-result/v1",
  "job_id": "uuid",
  "status": "completed",
  "page_count": 2,
  "markdown": "...",
  "pages": [
    {
      "page_index": 0,
      "source": "embedded_text|cpu_ocr|alias_ocr",
      "engine": "pp-ocrv6-small",
      "provider_destination": "hf-cpu-approved-only-if-policy-allows",
      "text": "...",
      "markdown": "...",
      "image_width": 1240,
      "image_height": 1754,
      "blocks": [{
        "type": "text",
        "text": "...",
        "bbox_1000": [10, 20, 800, 60],
        "polygon_1000": null,
        "confidence": 0.94,
        "confidence_kind": "rapidocr-recognition"
      }],
      "flags": [],
      "route_reason": "cpu_default",
      "latency_ms": 421,
      "attempt_count": 1
    }
  ],
  "failures": []
}
```

Rules: `page_index` zero-based, page order stable, geometry normalized to `0..1000` in the *source raster orientation* (retain page rotation/transform where applicable); `bbox_1000`/`polygon_1000` may be null when not known. Do not invent LightOn confidence from completion text. Keep confidence types distinct; never compare incomparable raw scores directly. Record actual engine and provider when known; if unavailable, use `null` and restrict security accordingly. Never include real document text in request tracing.

## 5. Input normalization and page preparation

1. Accept PDF/image bytes or explicitly trusted caller-provided local file paths through CLI only. **Do not fetch arbitrary URLs**; defend against SSRF/path traversal.
2. Validate extension plus content signature, PDF parser outcomes, page count, decompression ratio, image dimensions, total pixels, size and total processing budget. Defaults to propose and test: 25 MiB upload, 100 pages, 20 megapixels/page, bounded PDF raster memory. All limits configurable downward.
3. For PDFs, inspect each page for extractable glyph/text layer using `pypdf` and/or `pypdfium2` (choose verified implementation). If text appears usable, emit `embedded_text` with geometry only when directly available; suspicious or near-empty layers proceed to OCR.
4. Render scanned pages with `pypdfium2` (preferred license-friendly candidate; verify dependency licenses) at a sane DPI and apply optional orientation/crop handling. Preserve original page order/rotation and coordinate transform.
5. Compute cheap deterministic features: text-layer coverage, image dimensions, orientation, image blur/contrast, visual line density, possible columns/table rulings, detected handwriting proxy, content density. Never claim these heuristics provide semantic certainty.
6. Optionally rescale a *separate copy* for LightOnOCR's verified max input edge. Do not shrink CPU input without testing recognition degradation.
7. For low-confidence or very complex pages, mark reasons rather than silently altering content.

## 6. Page-level routing policy

### Modes

| Mode | Rule | Guardrails |
|---|---|---|
| `auto` **default** | embedded text -> skip; regular images -> CPU; preclassified complex pages -> eligible alias; escalate CPU pages only if quality signals fail | GPU only if healthy, image-capable, policy-approved and within configured budget |
| `throughput` | distribute *independent* pages between CPU and alias according to live completion-time estimates | `allow_gpu_overflow=true`, benchmark-qualified lane, queue/budget/security guard; never equal round-robin by default |
| `quality` | route complex documents to verified LightOnOCR and selected pages to dual-run comparison | no blanket dual-run; quality comparisons preserve both candidates and disagreements |
| `cpu_only` | CPU or acceptable text layer | never quietly use GPU on failure |
| `gpu_only` | alias or acceptable text layer | never quietly use CPU unless explicitly authorized |

### Decision algorithm (baseline)

```text
for each page in document order:
  if valid_existing_text_layer(page) and not force_ocr: record embedded_text; continue
  destinations = health_checked_backends()
  destinations = destinations filtered by actual destination trust and input sensitivity
  destinations = destinations filtered by supported input/output capabilities
  if none: fail page with no_authorized_destination or backend_unavailable
  if explicit backend mode: use that eligible backend or fail closed
  if complex_layout(page) and alias supports demanded layout and capacity: choose alias
  else: choose CPU
  if mode == throughput and eligible(alias):
      T_cpu = (cpu_queued / max(1, cpu_workers)) * cpu_EWMA_ms + cpu_EWMA_ms
      T_gpu = (gpu_queued / max(1, gpu_workers)) * gpu_EWMA_ms + gpu_EWMA_ms
      choose alias for the page ONLY if measured T_gpu is materially lower
      AND GPU per-window budget and quality guard hold; use hysteresis to prevent flapping
  execute in bounded backend queue
  validate content/result type
  if CPU result fails calibrated quality checks: retry/escalate to alias if approved
  if alias fails: bounded retry or fallback to CPU if approved, without hiding quality limitations
  emit page result or explicit failure reason
assemble original order; report partial status when any page cannot be extracted
```

Practical defaults for initial measurement (tune using test evidence): **CPU concurrency=1**, **alias concurrency=2**, **per-backend queue capacity=32**, **max retries=1 per backend**, exponential backoff on retryable 429/503/timeout and circuit breaker. These are *starting settings*, not known optimal values. Respect user cancellation and backend rate limits. Use per-backend moving median/EWMA from **successful warm** requests, with cold-start and failure penalties. Require a measurable throughput gain (e.g. >15%) before enabling auto-overflow by default.

### Quality checks

- CPU line-level confidence from RapidOCR, but calibrate thresholds on actual German/English fixtures. Initial `low_confidence` below a configurable p10/aggregate threshold is only a heuristic, not a quality guarantee.
- Coverage/completeness checks: empty content on nonblank page, suspiciously small output, repeated text, misordered columns, unexpected truncation, high unresolved noise.
- LightOn output: markdown parse integrity, grounding marker validity and bounds, nonempty text, consistency with expected nonblank content. **No fake comparable confidence**.
- Escalate when warranted. In `quality`, run a selective double pass and retain disagreement/alternatives; choose winning candidate conservatively by checks or flag `needs_review`.
- Protect verbatim content: no hallucinated corrections, no silently filled tables/cells, no semantic paraphrase. Preserve page boundaries and positions; mark uncertain passages explicitly.

## 7. Security, classification and trust boundaries (release blocking)

**Classification:** `D0` public, `D1` internal, `D2` sensitive; unknown defaults to conservative policy. Only an authorized app/policy may downgrade sensitivity. Uploaded images may contain personal data **before** anonymization, so running anonymization after OCR does not justify raw image egress.

- Respect established FreeLLMAPI routing: approved inference-group destinations for D2 are **helmholtz, blablador, instituteforX**, after normalization and verification. `auto-smart`/`jev-router` remain authorized to that *specific inference catalogue*; generic `auto` is **not** authorized for D2. This new OCR dispatcher must **not** broaden trust just because a model is named `alias-ocr`.
- The HF CPU Space is a **separate data destination**. Default approval: **D0 only** unless an explicit, verified approval is configured. A private HF Space or bearer token provides access control, **not by itself** authorization to send D2 information to the remote processor.
- If `alias-ocr` resolves to an approved inference provider and effective destination/fallback can be guaranteed, it may handle D2. If the executed provider cannot be proven, fail closed; no silent egress to unknown providers.
- Re-run destination authorization on **every retry, escalation, re-route and fallback**. Do not use the alias's declared/provider label as sole proof. For provider-level hidden fallback, require either enforceable provider filtering plus proof or classify lane as unapproved for sensitive data.
- Credentials only in environment secrets or existing secret management; redact Authorization headers and data URLs in traces. Use TLS to remote services, short request timeouts, no raw-document logs, bounded in-memory retention (default job TTL: 30 minutes, configurable), delete temp files; encryption at rest when persistence is later introduced.
- Authenticate client and worker endpoints; local loopback binding on Debian, **no new public ports or Caddy/Funnel rules**. Rate limits, upload limits, cancellation, path traversal and image-decompression-bomb protection.
- If user says `gpu_only` but destination is unapproved for their document, return `403/422 no_authorized_destination`, never downgrade policy or route elsewhere.
- Document provenance and original sensitivity must be passed to downstream anonymization. OCR output must not be assumed anonymous. Later GLiNER/PII stages can produce typed tags such as `[PERSON]`, `[CITY]`, `[RELIGION]` while preserving source references; those stages are outside v1 OCR recognition itself.

## 8. Reliability, visibility and operations

- Async bounded queues, priority/fairness across jobs (avoid one 500-page job starving all others), per-job max active pages, request timeouts, health/readiness checks, circuit breaker and jittered backoff.
- Idempotency key per client job (scoped to caller) and stable page identity; prevent duplicate billing/processing where feasible; avoid duplicate inclusion on retries. Acknowledge that external retries can still run the backend twice.
- Exact page-index ordering in results, even when CPU and alias finish out of order. Report partial failures per page; never return `completed` when pages failed silently.
- Observe `pages/min`, median/p95/p99 warm latency per backend, cold start, queue wait, error type, escalation and fallbacks, CPU RAM/CPU time, image size, actual cost/rate pressure and OCR error rate on labelled fixtures. No PII or image content in logs/metrics.
- With ephemeral queue, restart may lose in-flight jobs; return explicit semantics and document them. Do not claim durable processing across Space sleep or Debian restart. A persistent queue is deferred.
- Handle HF Space sleep/unavailability via readiness preflight and cold start timeouts without blocking unrelated GPU jobs.
- `alias-ocr` 200/empty-content or reasoning-only must be treated as extraction failure, **not** fabricated completion. Do not change existing FreeLLMAPI streaming logic.
- Expose config feature flags to disable GPU, disable overflow, gate remote egress, control dual-run, and revert to CPU-only.

## 9. Deployment configuration

### Dispatcher (Debian-Devil)

```
OCR_DISPATCHER_BIND=127.0.0.1:PORT_EXISTING_OR_APPROVED_LOOPBACK_ONLY
OCR_CPU_URL=https://<space-subdomain>.hf.space
OCR_CPU_AUTH_TOKEN=<secret>
OCR_CPU_CONCURRENCY=1
FREELLMAPI_BASE_URL=http://127.0.0.1:<existing-port>/v1
FREELLMAPI_API_KEY=<secret>
OCR_GPU_MODEL=alias-ocr
OCR_GPU_CONCURRENCY=2
OCR_DEFAULT_MODE=auto
OCR_ENABLE_GPU_OVERFLOW=false
OCR_ALLOW_EXTERNAL_CPU_D0=true
OCR_ALLOW_EXTERNAL_CPU_D1=false
OCR_ALLOW_EXTERNAL_CPU_D2=false
OCR_GPU_D2_ENABLED=false  # enable only after destination enforcement qualification
OCR_MAX_UPLOAD_MIB=25
OCR_MAX_PAGES=100
OCR_JOB_TTL_MIN=30
```

Environment placeholders are examples, **not discovered or verified live addresses**. Resolve actual FreeLLMAPI port/config from the user environment; do not use a guessed value. Use existing loopback ingress if possible; no public listener.

### Hugging Face CPU worker

- Docker or Gradio Space with FastAPI HTTP service on HF-required app port **7860**; the server's `0.0.0.0:7860` within the Space is HF-managed, **not** a new public port on Debian.
- `Dockerfile`, pinned `requirements`/lock, minimal `README.md` Space YAML, `/health` and `/ready`; use one Uvicorn worker, avoid CUDA dependencies. Include a simple page-upload demo **only if access controls and privacy are appropriate**; never make a tokenless document processing public API accidentally.
- `OCR_CPU_AUTH_TOKEN` validated at API edge, no user-upload content in persistence, no shared `FREELLMAPI_API_KEY` secret in the Space.
- Measure speed and RSS on the actual two-core Space, test model-download cache/cold start and explain Space sleep. Do not claim 24/7 availability on free hardware.
- The dispatcher can also target a **trusted local CPU adapter** for D2 or when the external worker is unavailable, subject to actual local CPU capacity.

## 10. Validation / reproducible benchmark

Create a legally usable, sanitized fixture corpus with expected ground truth:

1. DE/EN clean PDFs with valid text layers (verify OCR is skipped).
2. DE/EN printed scanned pages at 100/200/300 DPI.
3. Multi-column articles, forms and HTML-style tables.
4. Receipts, low-contrast/blurred/rotated documents, accent/umlaut-heavy text.
5. Handwriting where both backends might struggle (explicit limitations).
6. Blank page, malicious/huge input, damaged PDF, visually nonblank page with no extractable output.
7. A multi-page PDF of at least 20 pages with mixed routes and out-of-order completion.

Measure **CER/WER against human-verified transcription**, structural/table fidelity, output completeness, normalized bounding-box overlap when available, images/sec/pages/min, CPU utilization, peak RSS, warm/cold latency p50/p95, GPU cost/quota and request failure rate. Publish benchmark script, machine/revision details, reproducible raw aggregate metrics (without documents). No unsupported performance claims.

Qualification gates:

- CPU-only path meets correctness and memory bounds; one image and one PDF end-to-end.
- `alias-ocr` multimodal **safe D0 live probe** returns validated OCR; actual destination, auth and grounding support documented. If unavailable, ship GPU as **disabled/unqualified** and keep CPU functional.
- `throughput` enabled only after measured end-to-end throughput gain on mixed 20+/100-page batches under allowed capacity. If not achieved, maintain `auto` CPU-first and gate overflow.
- No D2 document can reach an unauthorized destination, including injected fallback/hidden model substitution; fail-closed tests required.
- No documents/images/keys appear in logs, exception messages or metrics.
- Fixed 1-based/0-based indexing tests, markdown page delimiters, boxes geometry and missing-box handling.

## 11. Testing / acceptance matrix

**Unit:** input validation; text-layer skip; classification; selection on mode/capabilities; EWMA/queue thresholds; health changes; security matrix D0/D1/D2; normalization and geometry; timeout/backoff; 429/502/503; empty reply; response truncation; error statuses; cancellation; job TTL.

**Integration (mocked):** two backends complete out of order but result pages remain ordered; CPU fail -> approved GPU; GPU fail -> approved CPU; no fallback to forbidden destination; D2 alias with an unproven hidden fallback blocked; CPU Space inaccessible; corrupted PDFs; large adversarial image; OCR line confidence low -> escalate and annotate.

**Integration (real, opt-in):** actual PP-OCRv6 Small end-to-end on test image; actual `alias-ocr` with a **non-sensitive fixture** and no production configuration mutation. If credentials or network unavailable, report SKIP and keep GPU lane disabled; **never pretend mocked execution proves live eligibility**.

**Deployment dry-run:** CPU Docker image builds; local smoke check; install on HF via documented explicit steps. Real HF deployment, secrets and FreeLLMAPI production changes require owner approval unless already expressly authorized.

**Acceptance checklist (all required for code-ready v1):**

- [ ] Verified dependencies and explicitly pinned PP-OCRv6 Small detector/recognizer.
- [ ] CPU worker runnable locally and with HF CPU Docker config; one worker; authenticated, bounded uploads.
- [ ] Debian dispatcher job API with page-level async processing and deterministic output order.
- [ ] Real OpenAI multimodal `alias-ocr` adapter with safe capability probe and bounded retries; absence handled cleanly.
- [ ] Security labels and actual destination/fallback authorization enforced in code and covered by fail-closed tests.
- [ ] `auto`, `cpu_only`, `gpu_only`, `throughput`, `quality` modes implemented, with overflow **off by default** until benchmark gate.
- [ ] Embedded PDF text skip and reproducible rasterization.
- [ ] Normalized text, markdown, blocks/geometry/provenance schema with no invented confidence.
- [ ] Bounded queue, per-page partial failure, cancellation, retention/cleanup and observable metrics.
- [ ] Safe anonymization handoff preserving source evidence and document sensitivity.
- [ ] Offline unit/integration tests, optional real smoke tests, release/readiness report and rollback steps.
- [ ] No unrequested service restart, modified active deployment, opened public port or exposed secret.

## 12. Implementation sequence

1. **Phase 0 — repo/environment audit (non-destructive).** Inventory existing OCR, FreeLLMAPI endpoint config, actual `alias-ocr` metadata, authentication/permissions, target repo conventions and selected Space. Capture baseline. Confirm model API versions and license. No production mutation.
2. **Phase 1 — CPU worker.** RapidOCR Small ONNX backend, HF Docker packaging, auth, data limits, warmed readiness, real local fixtures.
3. **Phase 2 — dispatcher MVP.** PDF/image ingest, text-layer skip, normalized schema, job API, bounded async CPU queue, no remote GPU dependence, test coverage.
4. **Phase 3 — alias adapter.** OpenAI multimodal image request, optional grounding, mocks plus optional non-sensitive live probe, security/effective destination checks, fail-closed handling.
5. **Phase 4 — selective routing.** `auto`, `quality`, explicit modes, approved fallback, quality checks and structured evidence, privacy test matrix.
6. **Phase 5 — throughput and benchmarks.** Capacity-aware scheduling with 2 independent bounded queues; benchmarks; enable overflow only behind feature flag and with positive measured gain.
7. **Phase 6 — deployment readiness.** docs, example env, runbooks, optional OpenAI-compatible single-image façade, package/test status, explicit install instructions; stop short of live production deployment without authorization.

## 13. Deliverables for Codex

- Source changes on a **new isolated branch/worktree**, respecting existing repo conventions.
- `spec.md` copied into or referenced from the target repo with changes from this spec recorded as ADRs, not silently ignored.
- Pinned dependencies, Docker HF CPU Space, `.env.example`, configuration and threat-model docs.
- Working API, CLI sample for a German/English PDF and a one-page image.
- Unit + integration + benchmark script + sanitized fixtures. CI configured for offline tests; live tests opt-in.
- `docs/OCR_RELEASE_READINESS.md`: implemented/partial/unimplemented, exact commands and measured results, blocked D2/live endpoints/quotas, compliance caveats, change summary, actual files, git commit SHA, rollback path and explicit deployment approval steps.
- Do not claim green/live readiness without testing actual backend APIs on permitted fixtures.

## 14. Upstream documentation / verification sources

- PaddleOCR PP-OCRv6 docs: https://www.paddleocr.ai/main/en/ and https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md
- RapidOCR v3.9+ behavior and API: https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/usage/
- RapidOCR installation/ONNX: https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/
- LightOnOCR official repo, one-image chat format, grounding markers, dimensions: https://github.com/lightonai/LightOnOCR
- LightOnOCR-3-4B model: https://huggingface.co/lightonai/LightOnOCR-3-4B
- HF Spaces CPU/sleep/privacy documentation: https://huggingface.co/docs/hub/spaces-overview

**Verification caveat:** This spec reflects design decisions and public upstream contracts as of 2026-10-16. The user's live `alias-ocr`, FreeLLMAPI route, actual authorized destinations, chosen target checkout and target Space have **not** been inspected in this document. Codex must verify them and fail closed when evidence is absent.
