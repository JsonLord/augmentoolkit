# Reproducible OCR benchmarks

The generator creates CC0 synthetic public DE/EN printed pages (100/200/300 DPI),
text-layer/scanned PDFs, blur/contrast/rotation, ruled tables, columns, a receipt,
blank/damaged input and mixed 20-/100-page PDFs. No real personal data or private
source documents are used. DejaVu Sans is a prerequisite, provided in the worker
image. Ground truth is rendered source text; independent human review, genuine
handwriting and annotated layout/table ground truth remain outstanding.

```sh
OCR_MODELS_DIR=/path/to/verified/models OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 \
  python -m ocr.benchmark --runs 3 --fixtures /tmp/ocr-fixtures \
  --output /tmp/ocr-report.json
```

The report records revision/runtime source digest, cgroup limits, platform,
engine versions, model-init and warm latency percentiles, CER/WER, per-fixture
failures/flags/geometry bounds, batches/pages/min, CPU time and peak process RSS.
It does not persist documents or OCR text. CPU-only mixed batches use both embedded
text and CPU OCR; they are not proof of CPU/GPU overflow speedup.

For the two-CPU reproduction, build the reviewed image from repository root, with
an optional ephemeral proxy CA mount in a managed environment, then:

```sh
docker run --rm --network none --cpus 2 --memory 2g --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m hybrid-ocr-cpu:REVIEWED_REVISION \
  sh -c 'python -m ocr.benchmark --runs 3 --fixtures /tmp/fixtures --output /tmp/report.json > /tmp/summary.json && cat /tmp/report.json'
```

No host ports are published. The benchmark overrides the worker server command;
the image's inherited liveness healthcheck may report unhealthy because no server
is expected in that container. Its state is not an OCR outcome or readiness result.
A separate actual-server smoke test validates readiness/auth/OCR. Limits do not
make this an actual Hugging Face CPU Basic deployment, nor an exclusive-core test.

See `docs/ocr-benchmarks/` and `docs/OCR_RELEASE_READINESS.md` for retained measurements,
accuracy limitations, exact source revision and production qualification gaps.
Never enable overflow based on these CPU-only measurements or mocked alias timing.
