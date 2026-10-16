# Hybrid CPU-first OCR

Separate private dispatcher and authenticated PP-OCRv6 Small CPU worker.
No production integration or startup occurs on import. See:

- [API and schemas](docs/api.md)
- [Install, HF packaging, secrets and rollback](docs/deployment.md)
- [Routing, limits and cancellation](docs/routing.md)
- [Reproducible benchmarks](docs/benchmark.md) and [distribution notices](THIRD_PARTY.md)
- [Security boundaries](docs/threat-model.md)
- [Audit](docs/audit.md) and [decisions/deviations](docs/adr.md)
- [Release-readiness report](../docs/OCR_RELEASE_READINESS.md)

`python -m ocr.cli run file.pdf --format markdown` runs trusted local CPU.
`python -m ocr.cli probe-alias` uses a synthetic public image and credentials from
runtime environment. `python -m ocr.benchmark` generates a CC0 synthetic DE/EN
corpus and measures real CPU OCR. Live alias and all D2 remote routing default off.
