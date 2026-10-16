# Routing and reliability

`ocr.routing.Dispatcher` owns two independent bounded lanes. Defaults: CPU 1,
alias 2 workers, 32 pending items per lane, two active pages per job, four active
jobs, 128 retained job records, 30 minute creation-time TTL. One parser process
runs at a time; its address space is capped at 2 GiB, CPU time at 30 seconds and
wall time at 40 seconds. It receives no credential environment. An output cap
bounds the parent transfer. Native parser isolation is resource containment, not
a general hostile-code sandbox; keep PDFium/Pillow/parser dependencies current.

Input limits: 25 MiB, 100 pages, 20 MP/page, 200 MP/document. Limits can be lowered.
Post-processing limits: 8 MiB serialized result/job, 64 MiB aggregate retained
results. Capacity exhaustion is explicit. Source bytes/raster pages are transient;
results contain classified OCR evidence until purge or TTL. Restart loses jobs.

Usable PDF text skips OCR and carries `text_layer_heuristic` provenance. This is a
heuristic, not proof that a malicious text layer matches visible content. Use
`force_ocr` for suspicious files. PDFium renders at 200 DPI; EXIF orientation is
applied to images. Boxes refer to the resulting source raster, not an unrotated
PDF coordinate system. Embedded text currently has no invented geometry.

- `auto`: CPU first; complex pages can use alias only with verified layout support.
  CPU quality problems selectively escalate to an eligible alias.
- `cpu_only`/`gpu_only`: explicit lane; no cross-lane fallback.
- `quality`: selective comparison for complex pages when the operator enables
  `OCR_QUALITY_DUAL_RUN`. Keep both results and disagreement/review flags.
- `throughput`: requires caller permission, operator enablement, benchmark
  qualification and both warm EWMAs. Alias must predict completion at least 15%
  sooner. Default overflow is off; no real hybrid speedup is yet qualified.

Authorize immediately before every attempt, including retries, fallback and
escalation. Health, circuit breaker and per-minute alias budget are checked there.
At most one retry follows 429/502/503/transport timeout with jitter; three failures
open the lane for 30 seconds. No silent retry changes the model or destination.
The first successful lane call is recorded separately from the warm EWMA.

Initial quality thresholds are uncalibrated heuristics: p10 line score <0.7,
empty nonblank input, implausibly short text, repetition, replacement characters,
column-order warning, low-contrast/blur proxy and malformed Markdown. The blur/contrast proxy uses image variance normalized by square-root ink density
(initial threshold 145, checked on the synthetic corpus, not a general calibration).
CPU scores are never synthesized
for alias text. Unknown boxes stay null. Clear quality failures produce explicit
page failures or review flags. Rotated scans and blur are known limitations.

Cancellation purges a retained result and stops pending page work. A request
already executing remotely may finish and incur cost. Native ORT work retains its
serialization lock until it finishes; it cannot be force-interrupted safely.
Jobs that have failures and successes are `partial`, not `completed`. Results and
Markdown delimiters use original page order even if completions arrive out of order.
