# OCR trust boundaries

D0 = public, D1 = internal, D2 = sensitive. Unknown input defaults to D2.
Effective sensitivity is the stricter of caller label and server minimum.
A deployment must set the server minimum to its classification policy; callers
cannot be trusted to classify a mixed-trust upload collection correctly.

| Destination | D0 | D1 | D2 |
|---|---|---|---|
| Trusted local CPU | allowed | allowed | allowed |
| Authenticated remote HF CPU worker | explicit server/caller permission | blocked | blocked |
| Probed alias, operator enabled | allowed | blocked | blocked |
| Unprobed/unhealthy alias | blocked | blocked | blocked |

An HTTPS URL, private Space, token or model name does not approve a data processor.
The established D2 inference catalogue is helmholtz, blablador, instituteforX.
`auto-smart`/`jev-router` permission is confined to that catalogue; generic `auto`
is not D2 authorization. This implementation changes none of those production
policies. It has no audited enforceable executed-destination/fallback attestation
protocol, so D2 alias routing is hard-disabled even when metadata claims an
approved provider. `OCR_GPU_D2_ENABLED` is intentionally unsupported. Implement
and audit a real enforcement contract as a separate change before enabling it.

The D0 alias probe renders a random synthetic string in an image without putting
that string in the prompt. Successful extraction establishes only a tested
multimodal path. Returned model/provider fields are observations, not trustworthy
proof of destination enforcement. A model change revokes probe qualification.
Grounding/table/math capabilities are not enabled by alias/model name.

No arbitrary URL ingestion, HTTP redirects to other processors, remote file paths,
OpenAI tools, PDF chat attachments or general-purpose chat execution are exposed.
Remote service URLs are operator configuration and require HTTPS; HTTP is allowed
only for loopback. OpenAI façade accepts one inline image and no arbitrary prompt.

Client and worker tokens are separate. Authentication precedes multipart parsing;
fixed/chunked bodies and rates are bounded. No wildcard CORS. All endpoints except
content-free `/health` require auth, including docs, metrics and readiness.
Service must bind loopback/trusted IPC on Debian. No Caddy, Funnel, public Debian
port, existing production container or FreeLLMAPI routing change is needed.

Operational errors contain constant codes. Provider exception bodies, images,
base64, bearer tokens and OCR text are not logged. Native PDF parser stderr is
suppressed in its disposable process; ASGI guards prevent unexpected exceptions
from escaping into server tracebacks. Server access logs should be disabled.
Source raster bytes are not persisted; multipart spool files are closed by the
request lifecycle. There is no durable queue or at-rest document store. A future
persistent queue needs encryption, tenant authorization and retention review.

Anonymization handoff retains source page identity and original sensitivity.
OCR results are never presumed anonymous. The bridge requires a typed tagger
result and fails closed on errors; it does not use the legacy fail-open fallback.
Actual GLiNER/PII model qualification and release approval remain downstream work.
