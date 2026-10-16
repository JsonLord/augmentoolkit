# Private OCR API

Run the separate `ocr.api:app` ASGI application through an existing trusted ingress
or an explicitly approved loopback/Unix socket. It is not mounted into or started
by the existing Augmentoolkit application. Runtime settings are read from process
environment (Space secrets or Debian secret injection), never bundled in code.

All routes except `/health` require `Authorization: Bearer $OCR_DISPATCHER_TOKEN`.
`GET /ready` returns 200 only when CPU is warmed, otherwise 503. `/docs` and
`/openapi.json` provide the generated versioned schema. `/metrics` is authenticated
and returns content-free lane counters, queue depth, cold/warm latency percentiles.

| Method/path | Contract |
|---|---|
| POST `/v1/ocr/jobs` | Multipart `file`, `options` JSON; 202 with job/status/result URLs |
| GET `/v1/ocr/jobs/{id}` | queued/running/completed/partial/failed/cancelled, counts, timestamps, safe errors |
| GET `/v1/ocr/jobs/{id}/result` | `ocr-result/v1` JSON, only when a result is retained; 409 otherwise |
| DELETE `/v1/ocr/jobs/{id}` | Cancel pending work and purge retained result; no promise of undoing remote work |
| POST `/v1/ocr/page` | One image or one-page PDF, normalized `ocr-page/v1` result |
| POST `/v1/chat/completions` | Restricted single-image façade described below |

Use `Idempotency-Key` (max 128 characters) on job creation. Keys are scoped to the
authenticated service credential, not globally. Reuse with different effective
options/bytes returns 409. Multiple users sharing a token share that scope; this
is not a multi-tenant identity provider. 404 does not disclose another credential's
job. TTL eviction removes result/idempotency records. Errors include 401 auth,
403 unauthorized destination, 413 size, 422 input/backend errors, 429 capacity/rate,
409 result/idempotency state, 503 unavailable readiness.

```sh
# Existing private endpoint supplied by the operator; never use a guessed port.
curl --fail-with-body -H "Authorization: Bearer $OCR_DISPATCHER_TOKEN" \
  -F 'file=@public-example.pdf' \
  -F 'options={"mode":"cpu_only","sensitivity":"D0","output":"both"}' \
  "$OCR_DISPATCHER_URL/v1/ocr/jobs"
```

Options: `mode` (auto/cpu_only/gpu_only/quality/throughput), `sensitivity`, `output`
(text/markdown/structured/both), `language_hints`, `layout`, `preserve_boxes`,
`extract_pdf_text_layer`, `force_ocr`, `prefer_cpu`, `allow_external_cpu`,
`allow_gpu_overflow`, `max_pages`. Unknown fields are rejected. `output` selects
assembled text/Markdown fields; per-page source evidence remains in the envelope.
Language hints do not replace the pinned multilingual recognition model. CPU
preference is a hint subject to explicit mode, quality and capability requirements.

Results contain stable zero-based page indexes and SHA256-derived page identities,
engine/backend versions, sensitivity, normalized blocks, flags, route reasons,
attempt counts/timing/destinations and alternative evidence. Markdown delimiters
are one-based `<!-- page:1 -->`. Geometry is finite 0..1000 in source-raster
orientation. Missing geometry/confidence stays null; alias destination stays null
when not verifiable. No invented token usage or LightOn confidence.

The optional façade accepts `model: ocr-auto | ocr-cpu | ocr-gpu`, `stream:false`,
and exactly one user message containing exactly one image_url with
`data:image/png;base64,...` (JPEG/TIFF also supported). An `ocr_options` extension
can increase sensitivity or restrict routing. It returns standard non-streaming
chat choices plus an `ocr` provenance extension, without invented `usage`.
Arbitrary text messages, remote URLs, tools/functions, extra parameters, streaming
and PDFs are rejected. This is not full OpenAI API compatibility and does not
modify FreeLLMAPI. The raw JSON body cap makes effective inline-image capacity
slightly smaller than the 25 MiB binary multipart limit.

Worker API: separate token `OCR_CPU_AUTH_TOKEN`, `POST /internal/ocr/page` multipart
one raster image (`file`, optional bounded `request_id`, empty `{}` `options`).
`GET /internal/capabilities` returns conservative model capabilities. It admits one
inference at a time and rejects excess work, rather than accumulating an unbounded
queue. It does not receive FreeLLMAPI credentials or document classification policy.
